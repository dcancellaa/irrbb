# Regulatory set-up: US bank

The entity is a **US full-licence bank**. It is a subsidiary of a foreign
group, but IRRBB is measured and governed **under US requirements only**,
against the bank's **own capital**. Group reporting is out of scope at this
level.

## US supervisory expectations

US agencies do not prescribe a standardised IRRBB calculation. The framework
is principles based:

* 2010 *Interagency Advisory on Interest Rate Risk Management* (and the 2012 FAQ);
* OCC Comptroller's Handbook, *Interest Rate Risk*;
* Federal Reserve SR 10-1; FDIC / FFIEC guidance on sensitivity to market risk.

| Expectation | How the engine covers it |
|---|---|
| Earnings **and** economic-value perspectives | NII / ΔNII (12-month, constant balance sheet) and EVE / ΔEVE (full revaluation; standardised kept as a challenger) |
| Instantaneous parallel shocks of at least ±200bp, larger where meaningful | `US_PARALLEL_{UP,DOWN}_{100,200,300,400}` |
| Gradual rate changes for earnings | `US_RAMP_{UP,DOWN}_{100..400}_12M` (NII only) |
| Non-parallel shifts: twists, short and long end | Steepener, flattener, short up / down shapes (`BCBS` family) |
| Basis risk | Curve-specific scenarios, e.g. Term SOFR vs SOFR |
| Behavioural assumptions (deposits, prepayments) that are documented and validated | NMD model + deposit-study table, prepayment / redemption scalars |
| Board-approved limits | Limit framework (% of base NII, % of base EVE, % of Tier 1) |

There is **no supervisory outlier test** in the US, so the `us_bank` profile
switches it off (`outlier_tests: false`).

## The `us_bank` profile

```bash
irrbb run --as-of 2026-11-24 --profile us_bank --tier1 1.25e9
```

| Setting | Value |
|---|---|
| Floor on down shocks | Zero |
| Scenarios | 6 curve shapes + 8 US parallel (EVE and NII) + 8 ramps (NII) + Term SOFR basis |
| Capital | `tier1_capital`: the bank's own Tier 1, passed per run |
| BRL | No onshore business: the offshore `BRL-NDF-IMPLIED` curve is the BRL discount curve |
| NDF settlement | USD or EUR; expected settlement amounts reported per NDF |
| EUR/BRL NDFs | USD-implied BRL curve for now; `ndf_discount_by_pair` hook for a EUR-based curve |
| Required tenors | 1M–3Y (book mostly below 3Y); longer cash flows flagged as `CURVE_EXTRAPOLATION` |
| Limits | Structure defined, **levels pending ALCO** |

## Limits before ALCO approval

Every limit in the profile has no `threshold` yet. The engine still measures
the exposure (worst scenario, loss, % of base NII, % of base EVE or % of Tier 1)
and reports it with status **`NOT_SET`**. ALCO can therefore calibrate levels
on actual measurements. Once levels are approved, add them to a copy of the
profile (or your own YAML):

```yaml
limits:
  - name: NII_RAMP_200_PCT_BASE
    metric: NII
    basis: PCT_BASE
    threshold: 0.10            # ΔNII loss ≤ 10% of base NII
    warning_level: 0.80        # early warning at 80% utilisation
    scenarios: [US_RAMP_UP_200_12M, US_RAMP_DOWN_200_12M]
  - name: EVE_SHOCK_200_PCT_TIER1
    metric: EVE
    basis: PCT_CAPITAL         # uses tier1_capital
    threshold: 0.15
    scenarios: [US_PARALLEL_UP_200, US_PARALLEL_DOWN_200]
```

Statuses: `OK`, `WARNING` (utilisation ≥ `warning_level`), `BREACH`,
`NOT_SET` (no level yet), `N/A` (e.g. `PCT_CAPITAL` without `tier1_capital`).

## Ramps

In a ramp the shocked forward over each NII segment is phased in linearly:

\[
f_s(t) = f_b(t) + \min\!\left(1, \frac{12\,t}{n}\right)\,\big(f_s^{full}(t) - f_b(t)\big)
\]

\(t\) is the segment mid-point and \(n\) the ramp length in months. Any custom
scenario can be a ramp (`ramp_months`).

## Basis-risk scenarios

`curves:` limits a scenario to named curves, so one curve moves against the
others of the same currency:

```yaml
custom_scenarios:
  - { name: BASIS_TERM_SOFR_UP_25, bp: 25, curves: [USD-TERM-SOFR-3M] }
```
