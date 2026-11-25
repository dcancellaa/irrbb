# Outputs

Every run produces the following tables
([`RunResult.tables`][irrbb.engine.runner.RunResult]). Columns suffixed `_rep`
are in the reporting currency at spot FX; the others are in the local
currency.

| Table | Grain | Key columns |
|---|---|---|
| `position_pv` | scenario × position × currency | `pv`, `pv_rep`, `delta_pv`, `delta_pv_rep` |
| `eve` | method × scenario × currency | `eve`, `eve_rep`, `delta_eve`, `delta_eve_rep` |
| `eve_regulatory` | method × scenario | `eve_loss` (positive loss after cross-currency aggregation), `is_worst` |
| `eve_slotted` | scenario × currency × bucket | standardised slotted cash flows, mid-point DF, PV |
| `nii` | scenario × currency | `nii`, `nii_rep`, `delta_nii`, `delta_nii_rep` |
| `nii_regulatory` | scenario | `nii_rep`, `delta_nii_rep`, `nii_loss`, `is_worst` |
| `nii_positions` | scenario × position × currency | position-level NII and ΔNII |
| `gap` | currency × bucket | `assets`, `liabilities`, `gap`, `cumulative_gap` (+ `_rep`) |
| `pv01` | position × currency | `pv01`, `pv01_rep` (+1bp parallel) |
| `key_rates` | currency × pillar | `dv01`, `dv01_rep` |
| `outlier_tests` | test × method | `loss`, `ratio`, `threshold`, `breach` (supervisory scenarios only) |
| `limits` | limit | `worst_scenario`, `loss`, `measure`, `utilisation`, `status` |
| `recon_summary` | position × currency | engine vs source principal / interest, `status` |
| `recon_detail` | position × currency × month | monthly comparison |
| `market_data_checks` | finding | `check`, `severity`, `curve_name`, `tenor`, `detail` (incl. `ASSUMPTION` warnings such as a defaulted NDF settlement currency) |
| `ndf_settlements` | NDF | `settlement_currency`, `settlement_date`, `contract_rate`, `market_forward`, `settlement_amount`, `pv_settlement_ccy`, `pv_rep` |
| `curves` | scenario × curve × tenor | shocked zero rates (audit trail) |
| `cashflows` | flow | projected cash flows with DF and PV: base scenario by default (`keep_cashflows`: `NONE` / `BASE` / `ALL`) |

Positions with legs in two currencies (CCS, FX) appear once per currency in
`position_pv`. Sum `pv_rep` across rows to get the trade MtM in the reporting
currency.
