# Getting started

## Install

The project uses [uv](https://docs.astral.sh/uv/) and requires Python ≥ 3.12.

```bash
uv sync --all-groups          # runtime + dev + docs dependencies
uv run irrbb --help
```

Production runs on PostgreSQL (see [Database](guides/database.md)):

```bash
docker compose up -d db      # local PostgreSQL 19
export IRRBB_DATABASE_URL="postgresql+psycopg://irrbb:irrbb@localhost:5432/irrbb"
```

## 1. Generate sample data

```bash
uv run irrbb sample --out data --as-of 2026-11-24
```

This writes `positions.csv`, `cashflows.csv`, `curve_quotes.csv` and
`fx_rates.csv`: a USD/EUR/GBP/MXN book with every supported product, plus
USD/BRL and EUR/BRL NDFs (settled in USD and EUR) on the offshore BRL curve.

## 2. Run from files

```bash
uv run irrbb run --as-of 2026-11-24 --data-dir data --tier1 900000000 --out output
```

The console prints ΔEVE (both methods) and ΔNII per scenario, plus the outlier
tests. All result tables are written to `output/<run_id>/` as Parquet (or CSV
with `--fmt csv`) together with a `summary.json`.

## 3. Use the database

```bash
uv run irrbb db upgrade                                  # Alembic migrations
uv run irrbb db stage positions data/positions.csv --as-of 2026-11-24
uv run irrbb db stage cashflows data/cashflows.csv --as-of 2026-11-24
uv run irrbb db stage curve_quotes data/curve_quotes.csv --as-of 2026-11-24
uv run irrbb db stage fx_rates data/fx_rates.csv --as-of 2026-11-24
uv run irrbb run-db --as-of 2026-11-24 --tier1 900000000  # results persisted
uv run irrbb db runs
```

## 4. Start the API

```bash
uv run irrbb serve --port 8000
```

Open <http://localhost:8000/docs> for interactive OpenAPI documentation.
See [REST API](guides/api.md).

## 5. Use it as a library

```python
from datetime import date
from irrbb.config import RunConfig
from irrbb.data.sample import sample_curve_quotes, sample_fx, sample_positions
from irrbb.engine import run_from_frames

as_of = date(2026, 11, 24)
cfg = RunConfig(as_of=as_of, tier1_capital=9e8, lower_bound="EBA", gain_weight=0.5)
result = run_from_frames(cfg, sample_curve_quotes(), sample_fx(), sample_positions(as_of))

result["eve_regulatory"]       # ΔEVE loss per scenario & method
result["nii"]                  # NII / ΔNII per currency & scenario
result["position_pv"]          # MtM per position, currency and scenario
result.summary()               # headline figures
```

## Development

```bash
uv run pytest                 # tests
uv run ruff check src tests   # lint (google docstrings enforced)
uv run mypy src               # strict type checking
uv run mkdocs serve           # this documentation at http://127.0.0.1:8000
```
