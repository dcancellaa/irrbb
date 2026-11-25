import polars as pl
import pytest
from fastapi.testclient import TestClient

from irrbb.config import RunConfig, get_settings
from irrbb.data.sample import sample_cashflows, sample_curve_quotes, sample_fx, sample_positions
from irrbb.data.sources import DatabaseSource, FileSource
from irrbb.db.repository import Repository, get_engine
from irrbb.engine import RunResult, run_from_frames
from irrbb.service import MissingInputError, run_from_source

from .conftest import AS_OF


@pytest.fixture(scope="module")
def result() -> RunResult:
    cfg = RunConfig(as_of=AS_OF, tier1_capital=1e9)
    return run_from_frames(
        cfg, sample_curve_quotes(), sample_fx(), sample_positions(AS_OF), sample_cashflows(AS_OF)
    )


def test_all_tables_present(result):
    for name in RunResult.TABLES:
        assert name in result.tables
    assert result.rejected == []
    assert set(result["eve"].get_column("method").unique()) == {"FULL_REVALUATION", "STANDARDISED"}
    assert result["nii_regulatory"].height == 7


def test_key_rates_sum_to_parallel_pv01(result):
    kr = result["key_rates"].group_by("currency").agg(pl.col("dv01").sum())
    pv01 = result["pv01"].group_by("currency").agg(pl.col("pv01").sum())
    joined = kr.join(pv01, on="currency")
    for r in joined.iter_rows(named=True):
        assert r["dv01"] == pytest.approx(r["pv01"], rel=2e-3, abs=50)


def test_base_delta_is_zero(result):
    base = result["eve"].filter(pl.col("scenario") == "BASE")
    assert base.get_column("delta_eve").abs().max() == 0.0


def test_gap_reconciles_to_repricing_principal(result):
    gap = result["gap"]
    last = gap.group_by("currency").agg(pl.col("cumulative_gap").last(), pl.col("gap").sum())
    for r in last.iter_rows(named=True):
        assert r["cumulative_gap"] == pytest.approx(r["gap"])


def test_write_outputs(result, tmp_path):
    paths = result.write(tmp_path, "parquet")
    assert (tmp_path / "summary.json").exists()
    assert len(paths) == len(result.tables) + 1


def test_file_source_and_missing_inputs(tmp_path):
    from irrbb.data.sample import write_sample_data

    write_sample_data(tmp_path, AS_OF)
    res = run_from_source(FileSource(tmp_path), RunConfig(as_of=AS_OF, compute_key_rates=False))
    assert res.summary()["positions"] > 10
    (tmp_path / "fx_rates.csv").unlink()
    with pytest.raises(MissingInputError):
        run_from_source(FileSource(tmp_path), RunConfig(as_of=AS_OF))


def test_database_round_trip(tmp_path, result):
    repo = Repository(get_engine(f"sqlite:///{tmp_path / 'db.sqlite'}"))
    repo.create_all()
    for name, df in {
        "positions": sample_positions(AS_OF),
        "cashflows": sample_cashflows(AS_OF),
        "curve_quotes": sample_curve_quotes(),
        "fx_rates": sample_fx(),
    }.items():
        from irrbb.api.app import _CONTRACTS
        from irrbb.data.schemas import normalise

        norm, _ = normalise(df, *_CONTRACTS[name], name)
        assert repo.stage(name, norm, AS_OF) == df.height
    res = run_from_source(DatabaseSource(repo), RunConfig(as_of=AS_OF, tier1_capital=1e9))
    assert res.summary()["base_eve"] == pytest.approx(result.summary()["base_eve"], rel=1e-9)
    run_id = repo.save_run(res)
    assert repo.list_runs()[0]["run_id"] == run_id
    assert repo.run_table(run_id, "eve").height == result["eve"].height
    assert repo.delete_run(run_id)


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("IRRBB_DATABASE_URL", f"sqlite:///{tmp_path / 'api.sqlite'}")
    from irrbb.api.app import create_app

    client = TestClient(create_app())
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/scenarios/shock-sizes").json()["USD"]["parallel"] == pytest.approx(200)
    for name, df in {
        "positions": sample_positions(AS_OF),
        "cashflows": sample_cashflows(AS_OF),
        "curve_quotes": sample_curve_quotes(),
        "fx_rates": sample_fx(),
    }.items():
        r = client.post(
            f"/data/{name}/{AS_OF}/file",
            files={"file": (f"{name}.csv", df.write_csv(), "text/csv")},
        )
        assert r.status_code == 200, r.text
    assert client.get(f"/data/{AS_OF}").json()["positions"] == sample_positions(AS_OF).height
    r = client.post(
        "/runs", json={"as_of": str(AS_OF), "tier1_capital": 1e9, "compute_key_rates": False}
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert client.get("/runs").json()[0]["run_id"] == run_id
    eve = client.get(f"/runs/{run_id}/tables/eve", params={"scenario": "PARALLEL_UP"}).json()
    assert eve and all(row["scenario"] == "PARALLEL_UP" for row in eve)
    csv = client.get(f"/runs/{run_id}/tables/gap", params={"fmt": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert client.post(f"/data/positions/{AS_OF}/records", json=[{"x": 1}]).status_code == 422
    assert client.post("/runs", json={"as_of": "2020-01-01"}).status_code == 409
    curve = client.post(
        "/curves/bootstrap",
        json={"as_of": str(AS_OF), "quotes": sample_curve_quotes().head(12).to_dicts()},
    )
    assert curve.status_code == 200 and len(curve.json()) == 11
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
