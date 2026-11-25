# Database: PostgreSQL

The production database is **PostgreSQL 19** (released 19 November 2026;
supported to November 2031). SQLite is the default for development and
tests only.

```bash
docker compose up -d db                     # postgres:19 (see docker-compose.yml)
cp .env.example .env                        # set IRRBB_DATABASE_URL
export IRRBB_DATABASE_URL="postgresql+psycopg://irrbb:***@db-host:5432/irrbb"
irrbb db upgrade                            # Alembic migrations 0001 → 0003
```

The driver (`psycopg` 3) is a core dependency.

## Schema

* `stg_*` tables hold staged inputs partitioned by `as_of_date`;
  `stg_nmd_assumption` is effective dated.
* `run` plus `res_*` tables hold results keyed by `run_id`, with
  `ON DELETE CASCADE` from `run`.
* Amounts are `DOUBLE PRECISION`.
* Migrations live in `src/irrbb/db/migrations`. After changing
  `irrbb/db/models.py`:

  ```bash
  irrbb db revision -m "describe the change"   # autogenerate, then review
  irrbb db upgrade
  ```

## Bulk loading

Batches of 2,000 rows or more (`COPY_MIN_ROWS`) are streamed with
`COPY ... FROM STDIN (FORMAT csv)`: Polars writes the CSV natively and psycopg
streams it. Smaller batches use chunked `INSERT`s.

| Step (about 80k position rows, `us_bank` profile) | Time |
|---|---|
| Full run (23 scenarios, 15 revalued for EVE) | ≈ 24 s |
| Persist results (≈ 1.36M rows, mostly `res_position_pv`) | ≈ 8 s |

Measured on an Apple-silicon laptop against a local PostgreSQL.

## Verification

```bash
IRRBB_TEST_PG_URL=postgresql+psycopg://irrbb:***@localhost:5432/irrbb_test uv run pytest
```

This runs stage → run → persist → read-back against the server, including
the `COPY` path. The test **drops and recreates** the tables, so point it at
a dedicated test database.

!!! note "Version verified"
    The suite and the migrations (upgrade, downgrade, and `alembic check` for
    drift) were verified on **PostgreSQL 18.6**; PostgreSQL 19 was not yet
    packaged for Homebrew. The engine uses only standard SQL, `COPY` and
    `DOUBLE PRECISION`, nothing version specific. Run the command above once
    against your PostgreSQL 19 server before go-live.

## Operations

* Back up the `run` / `res_*` tables: they are the audit trail of reported figures.
* `res_position_pv` grows by about positions × EVE scenarios rows per run
  (≈ 1.2M for 80k positions). Partition it by `run_id` range, or purge
  intermediate runs, as retention policy allows.
