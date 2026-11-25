# Modelling products

Each product decomposes into one or more **legs**. Defaults apply when a field
is empty (see [`PRODUCT_DEFAULTS`][irrbb.instruments.factory.PRODUCT_DEFAULTS]).

| Product | Legs | Defaults | Behaviour |
|---|---|---|---|
| `DDA` / `MMDA` / `SAVINGS` | Built by the NMD model: `NONCORE` (overnight) + `CORE` (run-off) for EVE; `NII` (constant balance) | `PAY`, `ADMINISTERED` | Core share / maturity capped per BCBS category; beta for NII |
| `TERM_DEPOSIT` | 1 pay leg | `PAY`, `FIXED`, `ZERO`, `BULLET` | Early redemption (`runoff_rate`) × TDRR scalar |
| `LOAN` | 1 receive leg | `RECEIVE`, `FIXED`, `1M`, `BULLET` | Prepayment (`runoff_rate`) × prepayment scalar |
| `MORTGAGE` | 1 receive leg | `RECEIVE`, `1M`, `ANNUITY` | Prepayment × scalar |
| `CREDIT_CARD` | 1 receive leg | `ADMINISTERED`, `1M` | Run-off via `runoff_rate` (interim) |
| `BOND` | 1 receive leg | `6M` | — |
| `IRS` | 2 legs (fixed + float), same currency | `final_exchange = false`, `3M` | — |
| `CCS` | 2 legs, different currencies | `initial_exchange = true` | — |
| `FX_FORWARD` | 2 notional-only legs | `rate_type = NONE` | — |
| `NDF` | 2 notional-only legs (synthetic) | `rate_type = NONE` | — |
| `FX_SWAP` | 2 notional-only legs with near and far exchange | `initial_exchange = true` | — |

## Examples

=== "Floating loan"

    ```csv
    position_id,product_type,currency,notional,start_date,maturity_date,rate_type,index,spread,payment_frequency,current_rate
    LN1,LOAN,USD,1000000,2026-09-24,2031-09-24,FLOAT,USD-TERM-SOFR-3M,0.0225,3M,0.0665
    ```

=== "Pay-fixed IRS"

    ```csv
    position_id,leg_id,product_type,currency,direction,notional,start_date,maturity_date,rate_type,fixed_rate,index,payment_frequency,current_rate
    IRS1,FIX,IRS,USD,PAY,5000000,2026-05-24,2031-05-24,FIXED,0.0395,,12M,
    IRS1,FLT,IRS,USD,RECEIVE,5000000,2026-05-24,2031-05-24,FLOAT,,USD-SOFR,12M,0.0435
    ```

=== "NDF (sell BRL)"

    ```csv
    position_id,leg_id,product_type,currency,direction,notional,maturity_date,settlement_currency
    NDF1,BRL,NDF,BRL,PAY,200000000,2027-02-24,USD
    NDF1,USD,NDF,USD,RECEIVE,35400000,2027-02-24,USD
    ```

=== "FX swap"

    ```csv
    position_id,leg_id,product_type,currency,direction,notional,start_date,maturity_date
    FXS1,GBP,FX_SWAP,GBP,RECEIVE,40000000,2026-12-24,2027-06-24
    FXS1,USD,FX_SWAP,USD,PAY,50700000,2026-12-24,2027-06-24
    ```

    The `RECEIVE` GBP leg pays GBP at the near date and receives it at the far
    date; the USD leg is the mirror image.

## Notes on derivatives

* **NDFs** are two synthetic legs: the non-deliverable currency and the
  other currency of the pair (BRL and USD, or BRL and EUR). This exposes the
  position to both currencies' rates, as IRRBB requires, independently of the
  settlement currency. The BRL leg uses the offshore `BRL-NDF-IMPLIED` curve.
* **NDF settlement** is in **USD or EUR** (`settlement_currency`, checked
  against `ndf_settlement_currencies`). An NDF without one gets the reporting
  currency and a data-quality warning. Legs that disagree, or another
  currency, cause the position to be rejected. The `ndf_settlements` table
  shows, per NDF, the contract rate vs the market forward and the **expected
  settlement amount** in its settlement currency (each leg converted at the
  curve-implied forward FX rate). Its PV equals the MtM of the two legs.
* **Swaps without notional exchange** get a synthetic notional in the
  repricing view (long fixed bond / short FRN) so that the standardised EVE
  and the gap show the swap's repricing profile. Full revaluation uses
  contractual flows only.
* **Optionality** (caps, floors, swaptions) is not modelled yet; see the
  [Roadmap](../roadmap.md).
