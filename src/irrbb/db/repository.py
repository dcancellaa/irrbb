"""Database access: engine/session factory and a repository for frames.

Frames are moved in and out of the database with SQLAlchemy Core (no pandas
dependency). Production target is PostgreSQL
(``IRRBB_DATABASE_URL=postgresql+psycopg://user:pwd@host/db``), where large
batches are loaded with ``COPY``. SQLite works for development and tests.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from typing import Any, cast

import polars as pl
from sqlalchemy import Engine, Table, create_engine, delete, event, func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from irrbb.db.models import RESULT_MODELS, STAGING_MODELS, Base, Run
from irrbb.engine.runner import RunResult


@lru_cache(maxsize=8)
def get_engine(url: str) -> Engine:
    """Create (and cache) a SQLAlchemy engine.

    SQLite connections get foreign keys enabled.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        The engine.
    """
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn: Any, _: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def _table(model: type[Base]) -> Table:
    return cast(Table, model.__table__)


def _columns(model: type[Base]) -> list[str]:
    return [c.name for c in _table(model).columns if c.name not in {"id"}]


EFFECTIVE_DATED: frozenset[str] = frozenset({"nmd_assumptions"})
"""Staged datasets loaded as the latest version on or before the run date."""

INSERT_CHUNK: int = 5_000
"""Rows per bulk ``INSERT`` statement (keeps bind-parameter counts small)."""


COPY_MIN_ROWS: int = 2_000
"""Above this many rows PostgreSQL inserts use ``COPY ... FROM STDIN``."""


def _copy_postgres(session: Session, model: type[Base], frame: pl.DataFrame) -> None:
    """Stream a frame into PostgreSQL with ``COPY ... FROM STDIN (FORMAT csv)``.

    Polars writes the CSV natively, so no per-row Python objects are created.
    Nulls are written as unquoted empty fields, which ``COPY`` reads as NULL.
    """
    table = _table(model)
    prep = session.get_bind().dialect.identifier_preparer
    cols = ", ".join(prep.quote(c) for c in frame.columns)
    sql = f"COPY {prep.format_table(table)} ({cols}) FROM STDIN WITH (FORMAT csv)"
    raw = session.connection().connection.driver_connection
    payload = frame.write_csv(include_header=False, null_value="")
    with raw.cursor() as cur, cur.copy(sql) as copy:  # type: ignore[union-attr]
        copy.write(payload)


def _write_frame(
    session: Session, model: type[Base], df: pl.DataFrame, extra: dict[str, Any]
) -> int:
    """Insert a frame into a table: ``COPY`` on PostgreSQL, chunked INSERTs otherwise.

    Args:
        session: Open session (inside a transaction).
        model: Target ORM model.
        df: Frame; only columns of the table are written.
        extra: Constant columns added to every row (e.g. ``run_id``).

    Returns:
        Rows written.
    """
    cols = [c for c in _columns(model) if c in df.columns and c not in extra]
    if df.is_empty():
        return 0
    frame = df.select(cols).with_columns([pl.lit(v).alias(k) for k, v in extra.items()])
    if session.get_bind().dialect.name == "postgresql" and frame.height >= COPY_MIN_ROWS:
        _copy_postgres(session, model, frame)
        return frame.height
    records = frame.to_dicts()
    for i in range(0, len(records), INSERT_CHUNK):
        session.execute(insert(model), records[i : i + INSERT_CHUNK])
    return frame.height


class Repository:
    """Read and write staging data and run results.

    Args:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session = sessionmaker(engine, expire_on_commit=False)

    def create_all(self) -> None:
        """Create all tables (tests / quick start; production uses Alembic)."""
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        """Open a new ORM session.

        Returns:
            A session bound to the repository's engine.
        """
        return self._session()

    # ------------------------------------------------------------- staging
    def stage(
        self,
        dataset: str,
        df: pl.DataFrame,
        as_of: date,
        source_system: str | None = None,
        replace: bool = True,
    ) -> int:
        """Store an input dataset for a valuation date.

        Args:
            dataset: One of ``positions``, ``cashflows``, ``curve_quotes``, ``fx_rates``.
            df: Normalised frame.
            as_of: Valuation date partition.
            source_system: Optional source label (e.g. ``"CORE_BANKING"``).
            replace: Delete existing rows for ``as_of`` (and source) first.

        Returns:
            Number of rows inserted.

        Raises:
            KeyError: For an unknown dataset.
        """
        model = STAGING_MODELS[dataset]
        with self.session() as s, s.begin():
            if replace:
                t = _table(model)
                stmt = delete(t).where(t.c.as_of_date == as_of)
                if source_system:
                    stmt = stmt.where(t.c.source_system == source_system)
                s.execute(stmt)
            n = _write_frame(s, model, df, {"as_of_date": as_of, "source_system": source_system})
        return n

    def load(self, dataset: str, as_of: date) -> pl.DataFrame:
        """Load a staged dataset for a valuation date.

        Args:
            dataset: Dataset name.
            as_of: Valuation date.

        Effective-dated datasets (:data:`EFFECTIVE_DATED`, e.g. deposit-study
        assumptions) return the latest version on or before ``as_of``.

        Returns:
            Frame with the dataset's columns (excluding technical columns).
        """
        model = STAGING_MODELS[dataset]
        cols = [c for c in _columns(model) if c not in {"as_of_date", "source_system"}]
        table = _table(model)
        target = as_of
        if dataset in EFFECTIVE_DATED:
            with self.engine.connect() as conn:
                latest = conn.execute(
                    select(func.max(table.c.as_of_date)).where(table.c.as_of_date <= as_of)
                ).scalar()
            if latest is None:
                return pl.DataFrame(schema={c: pl.String for c in cols})
            target = latest
        stmt = select(*[table.c[c] for c in cols]).where(table.c.as_of_date == target)
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt)]
        if not rows:
            return pl.DataFrame(schema={c: pl.String for c in cols})
        return pl.DataFrame(rows, infer_schema_length=None)

    def staged_counts(self, as_of: date) -> dict[str, int]:
        """Row counts per staged dataset for a date.

        Args:
            as_of: Valuation date.

        Returns:
            Mapping dataset → rows.
        """
        out: dict[str, int] = {}
        with self.session() as s:
            for name, model in STAGING_MODELS.items():
                t = _table(model)
                stmt = select(func.count()).select_from(t).where(t.c.as_of_date == as_of)
                out[name] = int(s.execute(stmt).scalar_one())
        return out

    # ------------------------------------------------------------- results
    def save_run(self, result: RunResult) -> str:
        """Persist a run and all of its result tables.

        Args:
            result: Run result.

        Returns:
            The run id.
        """
        summary = json.loads(json.dumps(result.summary(), default=str))
        with self.session() as s, s.begin():
            s.add(
                Run(
                    run_id=result.run_id,
                    as_of_date=result.config.as_of,
                    created_at=result.created_at,
                    status="COMPLETED",
                    reporting_currency=result.config.reporting_currency,
                    config=json.loads(result.config.model_dump_json()),
                    summary=summary,
                    rejected={pid: why for pid, why in result.rejected},
                )
            )
            s.flush()
            for name, model in RESULT_MODELS.items():
                df = result.tables.get(name)
                if df is None or df.is_empty():
                    continue
                _write_frame(s, model, df, {"run_id": result.run_id})
        return result.run_id

    def list_runs(self, as_of: date | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List recent runs.

        Args:
            as_of: Filter on valuation date.
            limit: Maximum number of runs.

        Returns:
            Run metadata dictionaries, newest first.
        """
        stmt = select(Run).order_by(Run.created_at.desc()).limit(limit)
        if as_of:
            stmt = stmt.where(Run.as_of_date == as_of)
        with self.session() as s:
            return [
                {
                    "run_id": r.run_id,
                    "as_of": r.as_of_date.isoformat(),
                    "created_at": r.created_at.isoformat(),
                    "status": r.status,
                    "reporting_currency": r.reporting_currency,
                }
                for r in s.scalars(stmt)
            ]

    def get_run(self, run_id: str) -> Run | None:
        """Fetch run metadata.

        Args:
            run_id: Run identifier.

        Returns:
            The run, or ``None``.
        """
        with self.session() as s:
            return s.get(Run, run_id)

    def run_table(self, run_id: str, table: str) -> pl.DataFrame:
        """Load one result table of a run.

        Args:
            run_id: Run identifier.
            table: Result table name (key of :data:`RESULT_MODELS`).

        Returns:
            The table as a frame.

        Raises:
            KeyError: For an unknown table.
        """
        model = RESULT_MODELS[table]
        cols = [c for c in _columns(model) if c != "run_id"]
        t = _table(model)
        stmt = select(*[t.c[c] for c in cols]).where(t.c.run_id == run_id)
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt)]
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its results.

        Args:
            run_id: Run identifier.

        Returns:
            ``True`` if a run was deleted.
        """
        with self.session() as s, s.begin():
            for model in RESULT_MODELS.values():
                t = _table(model)
                s.execute(delete(t).where(t.c.run_id == run_id))
            res = s.execute(delete(_table(Run)).where(_table(Run).c.run_id == run_id))
        return bool(getattr(res, "rowcount", 0))
