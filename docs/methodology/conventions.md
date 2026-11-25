# Sign conventions

| Quantity | Convention |
|---|---|
| `notional` input | Always positive; `direction` gives the sign |
| `RECEIVE` / `PAY` | +1 / −1 from the bank's perspective (assets and receive legs are +) |
| Cash flows, PV, EVE | Positive = value to the bank |
| `delta_*` columns | Shocked − base: **negative = loss** |
| `eve_loss`, `nii_loss` | Positive numbers = losses (regulatory presentation) |
| External cash flows | Already signed (+ = inflow) |
| FX `rate` | Reporting-currency units per one unit of foreign currency |
| Time axis | ACT/365F years from the valuation date |
| Rates | Decimals; zero rates are continuously compounded |
