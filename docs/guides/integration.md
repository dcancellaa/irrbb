# Integrating source systems

There are three integration patterns. Each ends in the same normalised data
contracts.

## 1. File drops (batch EOD)

Upstream jobs write `positions`, `cashflows`, `curve_quotes` and `fx_rates`
as CSV, Parquet, JSON or NDJSON into a directory, optionally under a dated
sub-folder:

```text
/data/irrbb/
├── 2026-11-24/
│   ├── positions.parquet
│   ├── cashflows.parquet
│   ├── curve_quotes.csv
│   └── fx_rates.csv
└── 2026-11-25/ ...
```

```bash
irrbb run --as-of 2026-11-24 --data-dir /data/irrbb --persist
```

## 2. Push to the staging database (API)

Systems push records over HTTP. Each push replaces that dataset's rows for
the date (and for `source_system`, if given), so several systems can feed
`positions` independently:

```bash
curl -X POST "http://irrbb/data/positions/2026-11-24/file?source_system=CORE_BANKING" \
     -F "file=@deposits.csv"
curl -X POST "http://irrbb/data/positions/2026-11-24/records?source_system=MUREX" \
     -H "Content-Type: application/json" -d @derivatives.json
curl -X POST "http://irrbb/runs" -H "Content-Type: application/json" \
     -d '{"as_of": "2026-11-24", "tier1_capital": 900000000}'
```

## 3. Custom adapters

Implement the [`DataSource`][irrbb.data.sources.DataSource] protocol to read
directly from a warehouse, an API or a message bus:

```python
from datetime import date
import polars as pl
from irrbb.config import RunConfig
from irrbb.service import run_from_source


class SnowflakeSource:
    def __init__(self, conn) -> None:
        self.conn = conn

    def load(self, dataset: str, as_of: date) -> pl.DataFrame | None:
        query = f"select * from irrbb_feed.{dataset} where as_of_date = '{as_of}'"
        return pl.read_database(query, self.conn)


result = run_from_source(SnowflakeSource(conn), RunConfig(as_of=date(2026, 11, 24)))
```

## Market data

Raw curves come from outside (Bloomberg, Refinitiv, the treasury system).
The engine **bootstraps** them (deposits + OIS par swaps) and applies every
shock itself, so discount factors, shocked curves and MtM are all produced
in-house and are fully reproducible. Already-built zero curves can be sent as
`ZERO_CC` quotes.

`POST /curves/bootstrap` returns the zero curve for a set of quotes, which
helps validate market data before a run.
