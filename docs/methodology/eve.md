# Economic Value of Equity

EVE is the present value of all banking-book cash flows (assets − liabilities
± off-balance-sheet), excluding equity. Two methods are computed side by side.

## Full revaluation (internal measurement system)

For every scenario \(i\):

1. Generate cash flows with the scenario's behavioural scalars.
2. Project floating coupons on the **shocked** projection curve:
   \(c_k = N_k \,(F_i(t_{k-1}, t_k) + s)\,\tau_k\).
3. Discount on the shocked discount curve of the flow's currency:
   \(EVE_{i,c} = \sum_k CF_{k,c}\, DF_{i,c}(t_k)\).

The output is per currency, converted to USD at spot.

## Standardised (BCBS 368 §4)

1. Build **repricing cash flows**:
   * fixed instruments: all remaining coupons and principal;
   * floating instruments: the known coupon of the current period plus the
     notional at the next reset (commercial margins beyond the reset are
     excluded);
   * swaps: a synthetic notional on both legs.
2. Slot them into the 19 BCBS buckets (ON, ON–1M, …, 20Y+).
3. Discount at the bucket mid-point \(t_k\) with
   \(DF_{i,c}(t_k) = e^{-R_{i,c}(t_k)\,t_k}\).

## Aggregation and sign

\(\Delta EVE_{i,c} = EVE_{i,c} - EVE_{0,c}\) (negative means a loss). The
regulatory loss per scenario does not let gains in one currency offset losses
in another:

\[
\text{Loss}_i = \max\!\Big(0,\ \sum_c \big[\max(0, -\Delta EVE_{i,c}) - w\,\max(0, \Delta EVE_{i,c})\big]\Big)
\]

with \(w = 0\) under BCBS 368 and \(w = 0.5\) under EBA GL/2022/14
(`gain_weight`). The IRRBB EVE measure is the maximum loss over the six
scenarios. The outlier test flags \(\max_i \text{Loss}_i > 15\%\) of Tier 1.

!!! tip "Why the two methods differ"
    The standardised method collapses flows to bucket mid-points, drops
    floating spreads beyond the next reset and uses repricing rather than
    contractual flows. Full revaluation is the economically precise number.
    The gap between the two is a useful model-risk indicator.
