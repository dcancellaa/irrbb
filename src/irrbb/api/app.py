"""FastAPI application exposing ingestion, runs and results.

Start with ``irrbb serve`` and browse ``http://localhost:8000/docs`` for the
interactive OpenAPI documentation.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date
from functools import lru_cache
from typing import Annotated, Any

import numpy as np
import polars as pl
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from irrbb import __version__
from irrbb.api.schemas import (
    CurvePoint,
    CurveRequest,
    Health,
    RunListItem,
    RunRequest,
    RunSummary,
    StageResponse,
)
from irrbb.config import RunConfig, Settings, get_settings, list_profiles
from irrbb.data.market import curves_from_frame
from irrbb.data.quality import DataQualityError, check_quotes, compare_quotes
from irrbb.data.schemas import (
    CURVE_QUOTES_REQUIRED,
    CURVE_QUOTES_SCHEMA,
    DATASET_CONTRACTS,
    DataValidationError,
    normalise,
)
from irrbb.data.sources import DATASETS, DatabaseSource
from irrbb.db.models import RESULT_MODELS
from irrbb.db.repository import Repository, get_engine
from irrbb.scenarios.shocks import BCBS_SHOCK_SIZES
from irrbb.service import MissingInputError, run_from_source

_CONTRACTS = DATASET_CONTRACTS


@lru_cache(maxsize=4)
def _repo_for(url: str) -> Repository:
    repo = Repository(get_engine(url))
    repo.create_all()
    return repo


def get_repo(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[Repository]:
    """FastAPI dependency providing the repository.

    Args:
        settings: Application settings.

    Yields:
        Repository bound to ``settings.database_url``.
    """
    yield _repo_for(settings.database_url)


RepoDep = Annotated[Repository, Depends(get_repo)]


def _check_dataset(dataset: str) -> None:
    if dataset not in DATASETS:
        raise HTTPException(404, f"Unknown dataset {dataset!r}; expected one of {DATASETS}")


def _stage(
    repo: Repository, dataset: str, as_of: date, df: pl.DataFrame, source_system: str | None
) -> StageResponse:
    schema, required = _CONTRACTS[dataset]
    try:
        norm, report = normalise(df, schema, required, dataset)
    except DataValidationError as exc:
        raise HTTPException(422, exc.report.errors) from exc
    rows = repo.stage(dataset, norm, as_of, source_system)
    return StageResponse(dataset=dataset, as_of=as_of, rows=rows, warnings=report.warnings)


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Returns:
        The application.
    """
    app = FastAPI(
        title=get_settings().api_title,
        version=__version__,
        description="Interest Rate Risk in the Banking Book: ingestion, runs (EVE, NII, gap, "
        "sensitivities) and results.",
    )

    @app.get("/health", response_model=Health, tags=["system"])
    def health(settings: Annotated[Settings, Depends(get_settings)]) -> Health:
        """Liveness probe."""
        return Health(version=__version__, database=settings.database_url.split(":")[0])

    @app.get("/profiles", tags=["scenarios"])
    def profiles() -> list[str]:
        """Packaged run profiles (e.g. ``us_bank``)."""
        return list_profiles()

    @app.get("/data/{as_of}/market-checks", tags=["data"])
    def market_checks(
        as_of: date, repo: RepoDep, compare_to: date | None = None
    ) -> list[dict[str, Any]]:
        """Data-quality checks on the staged curve quotes (optionally day-over-day)."""
        raw = repo.load("curve_quotes", as_of)
        if raw.is_empty():
            raise HTTPException(404, f"No curve quotes staged for {as_of}")
        quotes, _ = normalise(raw, CURVE_QUOTES_SCHEMA, CURVE_QUOTES_REQUIRED, "curve_quotes")
        try:
            curves = curves_from_frame(quotes, as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        out = check_quotes(quotes, curves)
        if compare_to:
            prev = repo.load("curve_quotes", compare_to)
            if not prev.is_empty():
                prev, _ = normalise(prev, CURVE_QUOTES_SCHEMA, CURVE_QUOTES_REQUIRED, "previous")
                out = pl.concat([out, compare_quotes(quotes, prev)])
        return out.to_dicts()

    @app.get("/scenarios/shock-sizes", tags=["scenarios"])
    def shock_sizes() -> dict[str, dict[str, float]]:
        """BCBS 368 currency shock sizes in basis points."""
        return {
            c: {"parallel": s.parallel * 1e4, "short": s.short * 1e4, "long": s.long * 1e4}
            for c, s in BCBS_SHOCK_SIZES.items()
        }

    # ----------------------------------------------------------- ingestion
    @app.post("/data/{dataset}/{as_of}/file", response_model=StageResponse, tags=["data"])
    async def upload_file(
        dataset: str,
        as_of: date,
        repo: RepoDep,
        file: Annotated[UploadFile, File(description="CSV or Parquet file")],
        source_system: str | None = None,
    ) -> StageResponse:
        """Stage a dataset from an uploaded CSV / Parquet file."""
        _check_dataset(dataset)
        raw = await file.read()
        name = (file.filename or "").lower()
        df = (
            pl.read_parquet(io.BytesIO(raw))
            if name.endswith(".parquet")
            else pl.read_csv(io.BytesIO(raw), try_parse_dates=True)
        )
        return _stage(repo, dataset, as_of, df, source_system)

    @app.post("/data/{dataset}/{as_of}/records", response_model=StageResponse, tags=["data"])
    def upload_records(
        dataset: str,
        as_of: date,
        records: list[dict[str, Any]],
        repo: RepoDep,
        source_system: str | None = None,
    ) -> StageResponse:
        """Stage a dataset from a JSON array of records (system-to-system push)."""
        _check_dataset(dataset)
        if not records:
            raise HTTPException(422, "Empty payload")
        return _stage(
            repo, dataset, as_of, pl.DataFrame(records, infer_schema_length=None), source_system
        )

    @app.get("/data/{as_of}", tags=["data"])
    def staged(as_of: date, repo: RepoDep) -> dict[str, int]:
        """Row counts of staged datasets for a date."""
        return repo.staged_counts(as_of)

    # ----------------------------------------------------------- runs
    @app.post("/runs", response_model=RunSummary, tags=["runs"])
    def create_run(req: RunRequest, repo: RepoDep) -> RunSummary:
        """Run all IRRBB measures on the data staged for ``as_of``.

        With ``profile`` the packaged profile is loaded first and the fields set
        explicitly in the request override it.
        """
        overrides = req.model_dump(exclude_unset=True, exclude={"persist", "profile", "compare_to"})
        overrides["as_of"] = req.as_of
        try:
            cfg = (
                RunConfig.from_profile(req.profile, **overrides)
                if req.profile
                else RunConfig(**overrides)
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            result = run_from_source(DatabaseSource(repo), cfg, req.compare_to)
        except MissingInputError as exc:
            raise HTTPException(409, str(exc)) from exc
        except DataQualityError as exc:
            raise HTTPException(
                422, {"message": str(exc), "findings": exc.findings.to_dicts()}
            ) from exc
        if req.persist:
            repo.save_run(result)
        return RunSummary(run_id=result.run_id, summary=result.summary())

    @app.get("/runs", response_model=list[RunListItem], tags=["runs"])
    def list_runs(
        repo: RepoDep, as_of: date | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> list[dict[str, Any]]:
        """List runs, newest first."""
        return repo.list_runs(as_of, limit)

    @app.get("/runs/{run_id}", response_model=RunSummary, tags=["runs"])
    def get_run(run_id: str, repo: RepoDep) -> RunSummary:
        """Summary of a stored run."""
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return RunSummary(run_id=run.run_id, summary=run.summary)

    @app.get("/runs/{run_id}/tables/{table}", tags=["runs"], response_model=None)
    def get_table(
        run_id: str,
        table: str,
        repo: RepoDep,
        fmt: Annotated[str, Query(pattern="^(json|csv)$")] = "json",
        scenario: str | None = None,
        currency: str | None = None,
    ) -> list[dict[str, Any]] | PlainTextResponse:
        """Fetch a result table as JSON records or CSV, optionally filtered."""
        if table not in RESULT_MODELS:
            raise HTTPException(404, f"Unknown table; expected one of {sorted(RESULT_MODELS)}")
        df = repo.run_table(run_id, table)
        if scenario and "scenario" in df.columns:
            df = df.filter(pl.col("scenario") == scenario)
        if currency and "currency" in df.columns:
            df = df.filter(pl.col("currency") == currency.upper())
        if fmt == "csv":
            return PlainTextResponse(df.write_csv(), media_type="text/csv")
        return df.to_dicts()

    @app.delete("/runs/{run_id}", tags=["runs"])
    def delete_run(run_id: str, repo: RepoDep) -> dict[str, bool]:
        """Delete a run and its results."""
        if not repo.delete_run(run_id):
            raise HTTPException(404, "Run not found")
        return {"deleted": True}

    # ----------------------------------------------------------- utilities
    @app.post("/curves/bootstrap", response_model=list[CurvePoint], tags=["curves"])
    def bootstrap(req: CurveRequest) -> list[CurvePoint]:
        """Bootstrap raw quotes and return zero rates on a standard grid."""
        try:
            cs = curves_from_frame(pl.DataFrame(req.quotes, infer_schema_length=None), req.as_of)
        except (ValueError, DataValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        grid = np.array([0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30], dtype=np.float64)
        out: list[CurvePoint] = []
        for c in cs:
            for t, z, d in zip(grid, c.zero_rate(grid), c.discount_factor(grid), strict=True):
                out.append(
                    CurvePoint(
                        curve_name=c.name,
                        tenor_years=float(t),
                        zero_rate=float(z),
                        discount_factor=float(d),
                    )
                )
        return out

    return app


app = create_app()
