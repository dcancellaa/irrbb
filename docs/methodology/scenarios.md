# Shock scenarios

## BCBS 368 standard scenarios

With \(x = 4\) and currency-specific sizes \(\bar R_{parallel}, \bar R_{short}, \bar R_{long}\):

\[
\begin{aligned}
\Delta R_{parallel}(t) &= \pm \bar R_{parallel} \\
\Delta R_{short}(t) &= \pm \bar R_{short}\, e^{-t/x} \\
\Delta R_{long}(t) &= \pm \bar R_{long}\,(1 - e^{-t/x}) \\
\Delta R_{steepener}(t) &= -0.65\,|\Delta R_{short}(t)| + 0.9\,|\Delta R_{long}(t)| \\
\Delta R_{flattener}(t) &= +0.8\,|\Delta R_{short}(t)| - 0.6\,|\Delta R_{long}(t)|
\end{aligned}
\]

The shift is added to the continuously compounded zero rate at **every**
maturity; it is not interpolated from pillars.

### Shock sizes (bp, BCBS 368 Annex 2)

Run `irrbb scenarios` or `GET /scenarios/shock-sizes` for the full table. For
example: USD 200/300/150, EUR 200/250/100, GBP 250/300/150, MXN and BRL
400/500/300. Currencies not in the table use **400/500/300** unless
overridden in `shock_sizes`.

## Post-shock lower bound

| Policy | Floor \(lb(t)\) |
|---|---|
| `NONE` | none |
| `ZERO` | 0 |
| `EBA` | \(\min(0, -150\text{bp} + 3\text{bp} \cdot t)\) |

The shocked rate is \(\max(r + \Delta r,\ \min(r, lb))\): the floor never
pushes up a rate that is already below it.

## Behavioural scalars

| Scenario | Prepayment (CPR) | Term-deposit redemption (TDRR) |
|---|---|---|
| Parallel up | 0.8 | 1.2 |
| Parallel down | 1.2 | 0.8 |
| Steepener | 0.8 | 0.8 |
| Flattener | 1.2 | 1.2 |
| Short up | 0.8 | 1.2 |
| Short down | 1.2 | 0.8 |

Cash flows are regenerated per distinct scalar set, so EVE reflects
scenario-dependent behaviour.

## Custom scenarios

* `parallel`: uniform shift, optionally per currency.
* `tenor`: shift defined at tenor points, linearly interpolated (historical
  or ALCO scenarios, twists).
* Every scenario can also scale prepayment, TDRR and **NMD betas**
  (`nmd_beta_scalar`).

Key-rate DV01 uses triangular bumps
([`key_rate_shock`][irrbb.scenarios.shocks.key_rate_shock]) that add up to a
parallel shift. The tests verify that key-rate DV01s sum to the parallel PV01.
