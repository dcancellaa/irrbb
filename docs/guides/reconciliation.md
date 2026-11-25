# Contract terms vs cash flows: reconciliation

The source systems can deliver both contract terms and cash flows. The
engine uses them like this:

* **Contract terms (`positions`) drive every calculation.** Only terms let
  the engine re-project floating coupons, apply scenario-dependent prepayment
  and redemption, and build the constant-balance-sheet NII.
* **Source cash flows (`cashflows`) are an independent check** when the same
  `position_id` is present in both datasets.
* **Cash flows alone** remain supported for systems that cannot send terms;
  those flows are valued as fixed, scenario-invariant flows.

## Modes (`external_cashflow_mode`)

| Mode | Position in both datasets | Position only in `cashflows` |
|---|---|---|
| `AUTO` (default) | valued from terms, flows **reconciled** | valued from flows |
| `RECONCILE` | valued from terms, flows reconciled | **not valued** |
| `VALUE` | valued **twice** (warning logged; only for special cases) | valued from flows |

`AUTO` makes double counting impossible by construction.

## What is compared

The engine's **contractual** flows (no prepayment or early redemption,
floating coupons projected on the run's base curves) are compared with the
source flows:

* **Principal** month by month, within `recon_tolerance_abs + recon_tolerance_rel × gross principal`.
* **Interest** in total, within `recon_tolerance_abs + recon_interest_tolerance_rel × |interest|`.
  The tolerance is looser because each system projects floating coupons on
  its own curves.

`recon_summary` holds one row per position and currency, with status
`MATCH`, `PRINCIPAL_BREAK`, `INTEREST_BREAK`, `BREAK` or `NOT_COMPARABLE`
(NMDs, whose engine flows are behavioural). `recon_detail` holds the monthly
comparison.

Typical causes of breaks:

* outstanding vs original notional;
* amortisation type (linear vs annuity);
* day-count or payment-frequency mapping;
* a missing `current_rate` on floating legs;
* maturity-date adjustments (business-day conventions).

!!! tip "Fields that matter most"
    Send `current_rate` (the current fixing) for every floating leg, and
    `notional` as the **outstanding** balance. These two fields cause most
    reconciliation breaks in practice.
