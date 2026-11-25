# REST API

Start with `irrbb serve`. Interactive docs are served at `/docs` (Swagger) and
`/redoc`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/scenarios/shock-sizes` | BCBS 368 shock sizes |
| `GET` | `/profiles` | Packaged run profiles |
| `GET` | `/data/{as_of}/market-checks` | Data-quality checks of staged curves (`compare_to` for day-over-day) |
| `POST` | `/data/{dataset}/{as_of}/file` | Stage a CSV / Parquet upload |
| `POST` | `/data/{dataset}/{as_of}/records` | Stage a JSON array of records |
| `GET` | `/data/{as_of}` | Staged row counts per dataset |
| `POST` | `/runs` | Run all measures on staged data (persists by default) |
| `GET` | `/runs` | List runs |
| `GET` | `/runs/{run_id}` | Run summary |
| `GET` | `/runs/{run_id}/tables/{table}` | Result table as JSON or CSV (`fmt=csv`), with optional `scenario` / `currency` filters |
| `DELETE` | `/runs/{run_id}` | Delete a run |
| `POST` | `/curves/bootstrap` | Bootstrap quotes into zero rates (market-data check) |

`dataset` is one of `positions`, `cashflows`, `curve_quotes`, `fx_rates`,
`nmd_assumptions`. `table` is one of `eve`, `eve_regulatory`, `nii`,
`nii_regulatory`, `position_pv`, `gap`, `key_rates`, `pv01`, `outlier_tests`,
`limits`, `recon_summary`, `market_data_checks`.

`POST /runs` accepts `profile` (e.g. `us_bank`) and `compare_to`. Fields set
explicitly in the request override the profile. Blocking data-quality errors
return **422** with the findings.

### Example

```bash
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' \
  -d '{"as_of":"2026-11-24","profile":"us_bank","compare_to":"2026-11-23"}' \
  | jq .summary.limits

curl -s "localhost:8000/runs/$RUN/tables/eve?scenario=PARALLEL_UP&currency=USD"
```

!!! note "Production hardening"
    Runs are executed synchronously in the request. For large books put them
    behind a job queue (see [Roadmap](../roadmap.md)) and add authentication
    (OAuth2 / mTLS) in front of the service.
