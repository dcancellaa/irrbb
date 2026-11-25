# Roadmap

## Near term

- [ ] **Automatic interest rate options**: caps and floors on floating
  loans, deposit floors (KAo add-on of BCBS 368 §4.5). Needs a vol surface
  input and Black / Bachelier pricing.
- [ ] **Credit cards**: dedicated revolving model (balance decay, transactor
  vs revolver split, promotional APRs).
- [ ] **Mortgages**: rate-dependent prepayment (refinancing incentive S-curve)
  instead of a constant CPR with scenario scalars.
- [ ] **Holiday calendars** (USNY, TARGET, GBLO) wired into schedules.
- [ ] **Compounded-in-arrears** RFR coupons with lookback/lockout.
- [ ] **Basis risk scenarios**: shock projection curves independently of
  discount curves.

## Done in v0.2

- [x] US bank profile: US parallel shocks, NII ramps, non-parallel and basis scenarios, limit framework.
- [x] Outlier test restricted to supervisory scenarios and optional (off for the US bank).
- [x] Engine vs source cash-flow reconciliation; double counting prevented.
- [x] Bloomberg / head-office curve adapters, `sources.yaml`, data-quality checks.
- [x] Effective-dated deposit-study assumptions.
- [x] PostgreSQL: `COPY` bulk loading, `DOUBLE PRECISION`, three Alembic migrations, drift check.
- [x] BRL offshore curve implied from NDF outrights / points; per-leg discount curves.
- [x] Offshore curve as the BRL discount curve; NDF settlement (USD / EUR) validation and report.
- [x] Short-end required tenors (1M–3Y), curve extrapolation check, `EURBRL` pair hook.
- [ ] Load the EUR-based BRL implied curve once the group tool publishes it.
- [x] Skeleton built once per run with vectorised behaviour (about 4× faster, bounded memory).

## Platform

- [ ] Asynchronous runs (job queue such as Celery/RQ/Arq) with run status
  `QUEUED/RUNNING/FAILED`.
- [ ] AuthN/Z on the API (OAuth2 / mTLS), audit log of uploads.
- [ ] Vectorised skeleton generation for very large retail books
  (≥ 10⁶ accounts), e.g. Polars-native schedule expansion. At about 80k
  rows the Python skeleton takes about 11 s of a 23 s run.
- [ ] `res_position_pv` partitioning / retention policy.
- [ ] Set ALCO limit levels (currently `NOT_SET`).
- [ ] Run the database test-suite against the production PostgreSQL 19 server.
- [ ] Dynamic balance sheet NII (growth / mix assumptions), EaR simulations.
- [ ] Regulatory templates (EBA IRRBB ITS, Basel Pillar 3 IRRBA/IRRBB1 tables)
  and a BI-friendly reporting mart.
- [ ] Results dashboard.
