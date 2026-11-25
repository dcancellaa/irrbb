# Deposit-study assumptions

NMD behaviour (core share, core average maturity, beta, repricing lag) comes
from the bank's deposit study. Until the study is final the engine runs on
placeholders. Replace them **without code changes** through the optional
`nmd_assumptions` dataset.

Template: `examples/nmd_assumptions.csv`

```csv
product_type,currency,nmd_category,core_share,core_avg_maturity,beta,reprice_months,source
DDA,,RETAIL_TRANSACTIONAL,0.70,3.0,0.10,1,2026 deposit study v1
MMDA,,,0.40,1.5,0.65,1,2026 deposit study v1
MMDA,USD,WHOLESALE,0.25,1.0,0.85,1,2026 deposit study v1
```

Leave `currency` or `nmd_category` empty to mean "any".

## Precedence

1. Position-level columns on the row (`core_share`, `core_avg_maturity`, `beta`).
2. The most specific `nmd_assumptions` row: product + currency + category,
   then product + currency, then product + category, then product only.
3. `nmd_assumptions` in the run configuration (per product).
4. Built-in placeholders
   ([`DEFAULT_NMD_ASSUMPTIONS`][irrbb.behavioral.nmd.DEFAULT_NMD_ASSUMPTIONS]).

BCBS 368 caps are applied last (`enforce_nmd_caps`); a warning is logged when
a cap binds.

## Versioning

In the database the dataset is **effective dated**. A study staged with
`--as-of 2026-01-01` applies to every run on or after that date until a newer
version is staged:

```bash
irrbb db stage nmd_assumptions deposit_study_2026.csv --as-of 2026-01-01
```

With file sources, place `nmd_assumptions.csv` in the data root (or in a
dated sub-folder).
