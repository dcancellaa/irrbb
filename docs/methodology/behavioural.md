# Behavioural models

## Non-maturity deposits

Each NMD balance \(B\) is split into:

* **Non-core** \((1-c)B\): reprices overnight.
* **Core** \(cB\): amortises linearly every month over \(2\bar M\) years, so
  its weighted-average life equals \(\bar M\).

The caps of BCBS 368 Table 4 are enforced (a warning is logged when they
bind):

| Category | Max core share | Max avg. core maturity |
|---|---|---|
| Retail transactional | 90% | 5.0y |
| Retail non-transactional | 70% | 4.5y |
| Wholesale | 50% | 4.0y |

For NII the full balance is kept constant. The client rate moves by
\(\beta \cdot \Delta\text{market}\), repricing monthly.

!!! warning "Default assumptions are placeholders"
    The defaults in
    [`DEFAULT_NMD_ASSUMPTIONS`][irrbb.behavioral.nmd.DEFAULT_NMD_ASSUMPTIONS]
    (DDA 70% / 3y / β 0.10, savings 60% / 2.5y / β 0.35, MMDA 40% / 1.5y /
    β 0.65) are illustrative only. Replace them with assumptions calibrated in
    the bank's deposit studies, either per position (`core_share`,
    `core_avg_maturity`, `beta` columns) or per product in the run
    configuration.

## Prepayment and early redemption

Loans and mortgages (`runoff_rate` = annual CPR) and term deposits
(`runoff_rate` = annual early-redemption rate) carry a survival factor:

\[
B^{beh}(t) = B^{sched}(t)\,(1 - \gamma_i \cdot \text{rate})^{t}
\]

\(\gamma_i\) is the scenario scalar (see [Scenarios](scenarios.md)). Legs with
run-off and coarse payment frequencies are re-gridded monthly so that the
run-off is spread over time.
