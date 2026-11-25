# Net Interest Income

NII is projected over a horizon \(H\) (12 months by default) under a
**constant balance sheet**: positions that mature or amortise are replaced
like-for-like, so outstanding balances stay constant.

Each leg is split into accrual **segments** within \([0, H]\):

| Segment | Base rate | Scenario rate |
|---|---|---|
| Contractual (fixed coupon until maturity; current floating period) | \(r\) | \(r\) |
| Floating reset | \(f_b + s\) | \(f_b + s + \beta (f_s - f_b)\) |
| Fixed rolled over after maturity | \(f_b + m\) | \(f_b + m + \beta (f_s - f_b)\) |
| Administered (NMD) | \(r_{client}\) | \(r_{client} + \beta\,\sigma_\beta\,(f_s - f_b)\) |

* \(f_b, f_s\): base and shocked forward rates over the segment on the leg's
  projection curve.
* \(s\): contractual spread. \(m\): current margin of a fixed instrument
  over the base forward for its remaining life, so the replacement keeps the
  customer margin.
* \(\beta\): pass-through (1 for market instruments, the deposit beta for
  NMDs). \(\sigma_\beta\): the scenario's `nmd_beta_scalar`.

\[
NII_i = \sum_{\text{segments}} \text{sign} \cdot N \cdot \text{rate}_i \cdot \tau,
\qquad \Delta NII_i = NII_i - NII_0
\]

**External cash flows:** interest falling inside the horizon is earned as
given. Principal falling inside the horizon is reinvested (or refinanced) at
the shocked forward rate until \(H\).

FX forwards, NDFs and FX swaps (`rate_type = NONE`) carry no interest, so they
contribute no NII. Their carry is reflected in the EVE and MtM.
