"""Command line interface (``irrbb ...``)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from irrbb.config import RunConfig, get_settings
from irrbb.data.quality import DataQualityError
from irrbb.data.sources import DATASETS, DatabaseSource, DataSource, FileSource, read_table
from irrbb.engine.runner import RunResult
from irrbb.service import MissingInputError, run_from_source

if TYPE_CHECKING:
    from alembic.config import Config

    from irrbb.db.repository import Repository

app = typer.Typer(
    help="IRRBB engine: EVE, NII, gap and sensitivities for the banking book.", no_args_is_help=True
)
db_app = typer.Typer(help="Database management (Alembic migrations, staging).")
app.add_typer(db_app, name="db")
console = Console()

AsOf = Annotated[
    datetime, typer.Option("--as-of", formats=["%Y-%m-%d"], help="Valuation date (YYYY-MM-DD).")
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _config(
    as_of: date, config: Path | None, tier1: float | None, profile: str | None = None
) -> RunConfig:
    if config:
        return RunConfig.from_yaml(config, as_of=as_of, tier1_capital=tier1, profile=profile)
    if profile:
        return RunConfig.from_profile(profile, as_of=as_of, tier1_capital=tier1)
    return RunConfig(as_of=as_of, tier1_capital=tier1)


def _execute(source: DataSource, cfg: RunConfig, compare_to: datetime | None) -> RunResult:
    try:
        return run_from_source(source, cfg, compare_to.date() if compare_to else None)
    except MissingInputError as exc:
        console.print(f"[red]{exc}")
        raise typer.Exit(2) from exc
    except DataQualityError as exc:
        console.print(f"[red]{exc}")
        raise typer.Exit(3) from exc


def _print_result(result: RunResult) -> None:
    s = result.summary()
    ccy = s["reporting_currency"]
    console.rule(f"[bold]IRRBB run {s['run_id'][:8]} — as of {s['as_of']}")
    console.print(
        f"Positions: {s['positions']}  Rejected: {s['rejected']}  "
        f"Base EVE: {s['base_eve']:,.0f} {ccy}"
    )

    t = Table(title=f"ΔEVE / ΔNII by scenario ({ccy})")
    for col in ("Scenario", "ΔEVE full reval", "ΔEVE standardised", "ΔNII"):
        t.add_column(col, justify="right" if col != "Scenario" else "left")
    eve = result["eve_regulatory"]
    nii = result["nii_regulatory"]
    for scn in nii.get_column("scenario").to_list():
        if scn == "BASE":
            continue

        def pick(method: str, scenario: str = scn) -> str:
            v = eve.filter((pl.col("method") == method) & (pl.col("scenario") == scenario))
            if v.is_empty():
                return "n/a"  # NII-only scenario (ramp)
            return f"{float(v.get_column('delta_eve_rep_net').sum()):,.0f}"

        dn = float(nii.filter(pl.col("scenario") == scn).get_column("delta_nii_rep").sum())
        t.add_row(scn, pick("FULL_REVALUATION"), pick("STANDARDISED"), f"{dn:,.0f}")
    console.print(t)

    o = Table(title="Supervisory outlier tests")
    for col in ("Test", "Method", "Worst scenario", "Loss", "Ratio", "Threshold", "Breach"):
        o.add_column(col)
    for r in s["outlier_tests"]:
        ratio = "n/a" if r["ratio"] is None else f"{r['ratio']:.2%}"
        o.add_row(
            r["test"],
            r["method"],
            r["worst_scenario"],
            f"{r['loss']:,.0f}",
            ratio,
            f"{r['threshold']:.0%}",
            str(r["breach"]),
        )
    console.print(o)
    lim = result["limits"]
    if not lim.is_empty():
        lt = Table(title="Limits")
        for col in ("Limit", "Worst scenario", "Measure", "Utilisation", "Status"):
            lt.add_column(col)
        colour = {"OK": "green", "WARNING": "yellow", "BREACH": "red"}
        for r in lim.iter_rows(named=True):
            m = (
                "n/a"
                if r["measure"] is None
                else (f"{r['measure']:,.0f}" if r["basis"] == "ABSOLUTE" else f"{r['measure']:.2%}")
            )
            u = "n/a" if r["utilisation"] is None else f"{r['utilisation']:.0%}"
            c = colour.get(r["status"], "white")
            lt.add_row(r["limit"], str(r["worst_scenario"]), m, u, f"[{c}]{r['status']}")
        console.print(lt)
    ndf = result["ndf_settlements"]
    if not ndf.is_empty():
        by = ndf.group_by("settlement_currency").agg(
            pl.len().alias("n"), pl.col("settlement_amount").sum(), pl.col("pv_rep").sum()
        )
        for r in by.sort("settlement_currency").iter_rows(named=True):
            ccy = r["settlement_currency"]
            console.print(
                f"NDFs settled in {ccy}: {r['n']}, expected settlement "
                f"{r['settlement_amount']:,.0f} {ccy}, MtM {r['pv_rep']:,.0f} "
                f"{s['reporting_currency']}"
            )
    rec = s["reconciliation"]
    if rec:
        console.print("Cash-flow reconciliation: " + ", ".join(f"{k}={v}" for k, v in rec.items()))
    dq = s["data_quality"]
    if dq:
        console.print("Data-quality findings: " + ", ".join(f"{k}={v}" for k, v in dq.items()))
    if result.rejected:
        console.print(f"[yellow]{len(result.rejected)} rejected position(s):")
        for pid, why in result.rejected[:20]:
            console.print(f"  {pid}: {why}")


@app.command()
def sample(
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("data/sample"),
    as_of: AsOf = datetime(2026, 11, 24),
) -> None:
    """Write a synthetic multi-currency sample dataset."""
    from irrbb.data.sample import write_sample_data

    for p in write_sample_data(out, as_of.date()):
        console.print(f"wrote {p}")


@app.command()
def run(
    as_of: AsOf,
    data_dir: Annotated[Path, typer.Option(help="Directory with input files.")] = Path("data"),
    sources: Annotated[
        Path | None,
        typer.Option(
            help="sources.yaml routing datasets to files / DB / Bloomberg / mapped exports."
        ),
    ] = None,
    config: Annotated[Path | None, typer.Option(help="YAML run configuration.")] = None,
    profile: Annotated[str | None, typer.Option(help="Packaged profile (irrbb profiles).")] = None,
    compare_to: Annotated[
        datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="Previous date for day-over-day curve checks."),
    ] = None,
    tier1: Annotated[float | None, typer.Option(help="Tier 1 capital (reporting ccy).")] = None,
    out: Annotated[Path | None, typer.Option(help="Write result tables here.")] = None,
    fmt: Annotated[str, typer.Option(help="parquet or csv.")] = "parquet",
    persist: Annotated[bool, typer.Option(help="Store results in the database.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run IRRBB measures from files (positions, cashflows, curve_quotes, fx_rates)."""
    _setup_logging(verbose)
    cfg = _config(as_of.date(), config, tier1, profile)
    from irrbb.data.market_sources import build_sources

    src: DataSource = build_sources(sources, _repo) if sources else FileSource(data_dir)
    result = _execute(src, cfg, compare_to)
    _print_result(result)
    if out:
        paths = result.write(out / result.run_id, "csv" if fmt == "csv" else "parquet")
        console.print(f"Wrote {len(paths)} files to {out / result.run_id}")
    if persist:
        _repo().save_run(result)
        console.print(f"Persisted run {result.run_id}")


@app.command("run-db")
def run_db(
    as_of: AsOf,
    config: Annotated[Path | None, typer.Option(help="YAML run configuration.")] = None,
    profile: Annotated[str | None, typer.Option(help="Packaged profile.")] = None,
    compare_to: Annotated[
        datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="Previous date for day-over-day curve checks."),
    ] = None,
    tier1: Annotated[float | None, typer.Option(help="Tier 1 capital.")] = None,
    persist: Annotated[bool, typer.Option(help="Store results.")] = True,
) -> None:
    """Run IRRBB measures on data staged in the database."""
    repo = _repo()
    result = _execute(
        DatabaseSource(repo), _config(as_of.date(), config, tier1, profile), compare_to
    )
    _print_result(result)
    if persist:
        repo.save_run(result)
        console.print(f"Persisted run {result.run_id}")


@app.command()
def profiles() -> None:
    """List packaged run profiles."""
    from irrbb.config import PROFILE_DIR, list_profiles

    for name in list_profiles():
        first = (PROFILE_DIR / f"{name}.yaml").read_text().splitlines()[0].lstrip("# ")
        console.print(f"[bold]{name}[/bold]  {first}")


@app.command()
def scenarios() -> None:
    """Show the BCBS 368 shock sizes (bp)."""
    from irrbb.scenarios.shocks import BCBS_SHOCK_SIZES

    t = Table(title="BCBS 368 Annex 2 shock sizes (bp)")
    for col in ("Currency", "Parallel", "Short", "Long"):
        t.add_column(col)
    for c, s in sorted(BCBS_SHOCK_SIZES.items()):
        t.add_row(c, f"{s.parallel * 1e4:.0f}", f"{s.short * 1e4:.0f}", f"{s.long * 1e4:.0f}")
    console.print(t)


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Start the REST API (OpenAPI docs at /docs)."""
    import uvicorn

    uvicorn.run("irrbb.api.app:app", host=host, port=port, reload=reload)


# ------------------------------------------------------------------ db
def _repo() -> Repository:
    from irrbb.db.repository import Repository, get_engine

    return Repository(get_engine(get_settings().database_url))


def _alembic_config() -> Config:
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


@db_app.command("upgrade")
def db_upgrade(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """Apply Alembic migrations."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    console.print(f"Database upgraded to {revision}")


@db_app.command("revision")
def db_revision(message: Annotated[str, typer.Option("-m")]) -> None:
    """Autogenerate a new Alembic revision from the ORM models."""
    from alembic import command

    command.revision(_alembic_config(), message=message, autogenerate=True)


@db_app.command("stage")
def db_stage(
    dataset: Annotated[str, typer.Argument(help=f"One of {', '.join(DATASETS)}.")],
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    as_of: AsOf,
    source_system: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Stage an input file into the database for a valuation date."""
    from irrbb.data.schemas import DATASET_CONTRACTS, normalise

    if dataset not in DATASETS:
        raise typer.BadParameter(f"dataset must be one of {DATASETS}")
    schema, required = DATASET_CONTRACTS[dataset]
    df, report = normalise(read_table(file), schema, required, dataset)
    n = _repo().stage(dataset, df, as_of.date(), source_system)
    console.print(f"Staged {n} {dataset} rows for {as_of.date()}")
    for w in report.warnings:
        console.print(f"[yellow]{w}")


@db_app.command("runs")
def db_runs(limit: Annotated[int, typer.Option()] = 20) -> None:
    """List stored runs."""
    console.print_json(json.dumps(_repo().list_runs(limit=limit)))


if __name__ == "__main__":
    app()
