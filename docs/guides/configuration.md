# Configuration

## Profiles

Start from the packaged `us_bank` profile with `--profile us_bank`, or from
your own YAML with `--config`.
See [Regulatory set-up](regulatory-setup.md).

## Run configuration (YAML)

All fields of [`RunConfig`][irrbb.config.RunConfig] can be set in YAML and
passed with `--config`. `as_of` and `tier1_capital` can be overridden on the
command line.

```yaml
reporting_currency: USD
nii_horizon_months: 12
lower_bound: ZERO            # NONE | ZERO | EBA
gain_weight: 0.0             # 0 = BCBS 368, 0.5 = EBA
tier1_capital: 900000000
eve_sot_threshold: 0.15
nii_sot_threshold: 0.05
behavioural_scalars: true    # BCBS prepayment / TDRR scalars per scenario

shock_sizes:                 # override / add currencies (bp)
  COP: { parallel: 400, short: 500, long: 300 }

custom_scenarios:
  - name: PARALLEL_UP_100
    kind: parallel
    bp: 100
  - name: ALCO_BEAR_STEEPENER
    kind: tenor
    times: [0, 1, 5, 10, 30]
    bp_points: [25, 50, 100, 150, 150]
    currencies: [USD]
  - name: DEPOSIT_FLIGHT
    kind: parallel
    bp: 200
    nmd_beta_scalar: 1.5
  - name: BASIS_TERM_SOFR_UP_25   # basis risk: shock one curve only
    bp: 25
    curves: [USD-TERM-SOFR-3M]

nmd_assumptions:
  MMDA: { core_share: 0.45, core_avg_maturity: 1.75, beta: 0.6 }

key_rate_pillars: [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30]
discount_map: { USD: USD-SOFR, EUR: EUR-ESTR }

us_scenarios:                # US management scenarios (omit to skip)
  parallel_bp: [100, 200, 300, 400]
  ramp_bp: [100, 200, 300, 400]
  ramp_months: 12

limits:
  - { name: NII_RAMP_200, metric: NII, basis: PCT_BASE,   # threshold omitted → NOT_SET
      scenarios: [US_RAMP_UP_200_12M, US_RAMP_DOWN_200_12M] }

external_cashflow_mode: AUTO # AUTO | RECONCILE | VALUE
recon_tolerance_abs: 1.0
recon_tolerance_rel: 0.0001
recon_interest_tolerance_rel: 0.02

market_data_checks:
  max_abs_rate: 0.5
  max_pillar_jump_bp: 150
  max_daily_move_bp: 50
  required_tenors: [1M, 3M, 6M, 1Y, 2Y, 3Y]   # us_bank profile (book mostly < 3Y)
  fail_on_error: true

keep_cashflows: BASE         # NONE | BASE | ALL

outlier_tests: false         # BCBS 368 outlier test (no US equivalent)

discount_map:                # currency → discount curve
  BRL: BRL-NDF-IMPLIED       # offshore curve: no onshore BRL business
discount_overrides: {}       # product → currency → curve, for exceptions

ndf_settlement_currencies: [USD, EUR]
ndf_default_settlement: null # null = reporting currency (flagged as warning)
ndf_discount_by_pair: {}     # e.g. {EURBRL: BRL-NDF-IMPLIED-EUR} once available
```

An example ships at `examples/run_config.yaml`.

## Process settings (environment)

| Variable | Default | Description |
|---|---|---|
| `IRRBB_DATABASE_URL` | `sqlite:///irrbb.db` | SQLAlchemy URL |
| `IRRBB_DATA_DIR` | `data` | Default input directory |
| `IRRBB_OUTPUT_DIR` | `output` | Default output directory |
| `IRRBB_API_TITLE` | `IRRBB Engine API` | OpenAPI title |

Settings may also be placed in a `.env` file.

## Database migrations

```bash
irrbb db upgrade                    # apply migrations
irrbb db revision -m "add column"   # autogenerate after changing irrbb/db/models.py
```
