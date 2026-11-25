# IRRBB Engine

Interest Rate Risk in the Banking Book measurement engine for a multi-currency
banking book: **MtM, ΔEVE (full revaluation and BCBS 368 standardised),
NII/ΔNII, repricing gap, PV01 / key-rate DV01 and supervisory outlier tests**.

The engine takes contract terms or cash flows from upstream systems and raw
curves from market data. Bootstrapping, shocks, discounting and MtM all run
inside the engine.

Products: DDA, MMDA, savings, term deposits, loans (fixed, floating, amortising,
prepayable), mortgages, bonds, IRS, cross-currency swaps, FX forwards, NDFs
and FX swaps.

Stack: Polars · numpy/scipy · SQLAlchemy 2 · Alembic · FastAPI · Typer ·
Pydantic · MkDocs Material.

Built for a **US full-licence bank** under US supervisory expectations
(profile `us_bank`): ±100–400bp shocks, NII ramps, non-parallel and basis
scenarios, and limits against base NII, base EVE and own Tier 1. BRL exposure
(NDFs vs USD / EUR, settled in USD or EUR) is valued on the offshore
NDF-implied curve.

## Quick start

```bash
uv sync --all-groups
uv run irrbb sample --out data --as-of 2026-11-24
uv run irrbb run --as-of 2026-11-24 --data-dir data --profile us_bank --tier1 1.25e9 --out output
uv run irrbb serve                      # REST API → http://127.0.0.1:8000/docs
uv run mkdocs serve -a 127.0.0.1:8001   # documentation
```

## Layout

```text
src/irrbb/
├── core/         enums, dates & schedules, BCBS time buckets
├── curves/       zero curves, bootstrapping (deposits + OIS), curve sets, FX
├── scenarios/    BCBS 368 shocks, custom / key-rate shocks, lower bounds, scalars
├── instruments/  Leg primitive, skeleton generators, portfolio factory
├── behavioral/   NMD core/non-core model with BCBS caps
├── engine/       vectorised projection/discounting, run orchestration
├── measures/     EVE, NII, gap, sensitivities, outlier tests
├── data/         data contracts, validation, file/DB sources, sample data
├── db/           SQLAlchemy models, repository, Alembic migrations
├── api/          FastAPI app
├── cli.py        Typer CLI
└── config.py     RunConfig (YAML) and Settings (env)
```

Inputs can be routed per dataset (files, database, Bloomberg, head-office
curve exports) with `--sources examples/sources.yaml`. Templates for the
deposit study, the Bloomberg tickers and the head-office curve mapping are in
`examples/`. Database: PostgreSQL 19 (`docker compose up -d db`); SQLite for
development.

## Quality gates

```bash
uv run pytest && uv run ruff check src tests && uv run mypy src && uv run mkdocs build --strict
```
