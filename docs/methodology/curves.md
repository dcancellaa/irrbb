# Curves

## Representation

A [`YieldCurve`][irrbb.curves.yield_curve.YieldCurve] stores **continuously
compounded zero rates** \(r(t)\) at pillar times \(t\) (ACT/365F years from the
valuation date):

\[
DF(t) = e^{-r(t)\,t}, \qquad
F(t_1,t_2) = \frac{1}{\tau}\left(\frac{DF(t_1)}{DF(t_2)} - 1\right)
\]

where \(\tau\) is the accrual fraction in the leg's day count. Projecting a
floating coupon on the same accrual it pays on makes a floating leg at reset
price exactly at par.

Interpolation is either **linear on zero rates** (default) or **log-linear on
discount factors** (piecewise-flat forwards). Extrapolation is flat.

## Bootstrapping

| Quote | Treatment |
|---|---|
| `DEPOSIT` | \(DF(T) = 1/(1 + q\,\tau)\) |
| `OIS_SWAP` ≤ 1Y | single period, same as deposit |
| `OIS_SWAP` > 1Y | annual fixed vs compounded overnight: \(q \sum_i \tau_i DF(t_i) = 1 - DF(T)\), solved pillar by pillar with Brent's method. Intermediate DFs come from the interpolation. |
| `ZERO_CC`, `ZERO_ANNUAL`, `DISCOUNT_FACTOR` | direct conversion |

The test-suite checks that the bootstrapped curve reprices every input quote
to within \(10^{-10}\).

## Multi-curve setup

* Each currency has one **discount curve**: the risk-free / OIS curve
  (SOFR, €STR, SONIA, TIIE, CDI, …). It is chosen from `is_discount` flags or
  `discount_map`.
* Floating legs project off the curve named in their `index` (e.g.
  `USD-TERM-SOFR-3M`). If that curve is missing they fall back to the
  discount curve.
* Shocks are applied **per currency**: every curve of a currency moves by the
  same amount, so basis spreads are preserved.
