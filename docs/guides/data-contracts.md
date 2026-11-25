# Data contracts

Upstream systems deliver up to four datasets per valuation date, plus the
optional, effective-dated `nmd_assumptions` dataset (see
[Deposit assumptions](deposit-assumptions.md)). Column names
are case-insensitive; types are coerced (ISO dates, `true/false/1/0/Y/N`
booleans). Unknown extra columns are allowed and ignored. Rates are always
**decimals** (`0.045` = 4.5%).

!!! info "Positions and cash flows"
    **Positions** (contract terms) drive the calculations. They let the engine
    re-project floating coupons, apply behavioural scalars per scenario and
    compute NII properly. When a system can also send its **cash flows** for
    the same positions, the engine uses them to reconcile, not to value (see
    [Reconciliation](reconciliation.md)). Cash flows alone are valued as
    fixed, scenario-invariant flows.

## `positions`: one row per leg

| Column | Type | Req. | Description |
|---|---|:-:|---|
| `position_id` | str | ✔ | Trade / account identifier. Legs of one trade share it. |
| `leg_id` | str | | Leg identifier (default `"1"`). |
| `product_type` | str | ✔ | `DDA`, `MMDA`, `SAVINGS`, `TERM_DEPOSIT`, `LOAN`, `MORTGAGE`, `CREDIT_CARD`, `BOND`, `IRS`, `CCS`, `FX_FORWARD`, `NDF`, `FX_SWAP` |
| `book` | str | | Reporting book / portfolio (default `BANKING`). |
| `counterparty` | str | | Carried for reporting. |
| `currency` | str | ✔ | ISO code of the leg. |
| `direction` | str | | `RECEIVE` (asset / receive leg) or `PAY` (liability / pay leg). Defaults per product. |
| `notional` | float | ✔ | **Outstanding** balance at the valuation date, positive. |
| `start_date` | date | | Effective date (default: valuation date). Future dates make forward-starting legs. |
| `maturity_date` | date | ✔* | Final maturity (*not needed for NMDs). Matured rows are skipped. |
| `rate_type` | str | | `FIXED`, `FLOAT`, `ADMINISTERED`, `NONE`. Defaults per product. |
| `fixed_rate` | float | | Coupon, or client rate for NMDs / administered products. |
| `index` | str | | Projection curve name for floating legs (e.g. `USD-SOFR`). |
| `spread` | float | | Spread over the index. |
| `payment_frequency` | str | | `ZERO`, `1M`, `3M`, `6M`, `12M`. Also the reset frequency. |
| `day_count` | str | | `ACT/360` (default), `ACT/365F`, `ACT/ACT`, `30/360`. |
| `amortization` | str | | `BULLET`, `LINEAR`, `ANNUITY`. |
| `initial_exchange` | bool | | Notional exchanged at start (CCS / FX swap near leg). |
| `final_exchange` | bool | | Principal exchanged at maturity (false for IRS). |
| `current_rate` | float | | All-in rate of the period in progress (floating legs). Strongly recommended. |
| `runoff_rate` | float | | Annual CPR (loans) or early-redemption rate (term deposits). |
| `nmd_category` | str | | `RETAIL_TRANSACTIONAL`, `RETAIL_NON_TRANSACTIONAL`, `WHOLESALE`. |
| `core_share` | float | | NMD core share override. |
| `core_avg_maturity` | float | | NMD core average maturity override (years). |
| `beta` | float | | Rate pass-through for NII (NMDs, administered rates). |
| `settlement_currency` | str | | NDF settlement currency (informational). |
| `discount_curve` | str | | Explicit discount curve for the leg (e.g. `BRL-NDF-IMPLIED`); overrides `discount_overrides` and the currency default. |

## `cashflows`: externally computed flows

| Column | Type | Req. | Description |
|---|---|:-:|---|
| `position_id` | str | ✔ | Identifier. |
| `currency` | str | ✔ | ISO code. |
| `pay_date` | date | ✔ | Payment date (past dates are dropped). |
| `interest` | float | | Interest amount, **signed** from the bank's view (+ = inflow). |
| `principal` | float | | Principal amount, signed. For floating instruments send the notional at the next reset as principal. |
| `product_type`, `book`, `rate_type`, `index` | str | | Reporting attributes. |

## `curve_quotes`: raw market data

| Column | Type | Req. | Description |
|---|---|:-:|---|
| `curve_name` | str | ✔ | e.g. `USD-SOFR`, `EUR-ESTR`, `USD-TERM-SOFR-3M`. |
| `currency` | str | ✔ | Curve currency. |
| `tenor` | str | ✔ | `ON`, `1W`, `1M`, …, `30Y`. |
| `quote_type` | str | ✔ | `DEPOSIT`, `OIS_SWAP`, `ZERO_CC`, `ZERO_ANNUAL`, `DISCOUNT_FACTOR`, or for FX-implied curves `FX_SPOT`, `FX_OUTRIGHT`, `FX_POINTS`. |
| `value` | float | ✔ | Quote (decimal) or discount factor. |
| `maturity_date` | date | | Overrides the tenor. |
| `day_count` | str | | Money-market day count (per curve). |
| `is_discount` | bool | | Marks the currency's discount (risk-free) curve. |
| `base_curve` | str | | FX-implied curves only: discount curve of the other currency (e.g. `USD-SOFR`). |

## `fx_rates`: spot FX

| Column | Type | Req. | Description |
|---|---|:-:|---|
| `currency` | str | ✔ | ISO code. |
| `rate` | float | ✔ | Reporting-currency units per **one** unit of `currency` (EUR → 1.08). |

## Validation

[`normalise`][irrbb.data.schemas.normalise] checks required columns and nulls
and coerces types. Blocking errors raise
[`DataValidationError`][irrbb.data.schemas.DataValidationError] (HTTP 422 via
the API). Row-level problems (e.g. a loan without maturity) do not fail the
run: the position is **rejected** and listed in the run summary.
