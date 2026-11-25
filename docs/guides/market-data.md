# Market data: group curve tool, Bloomberg and BRL implied curves

Raw quotes come from outside. Bootstrapping, shocks and discounting happen in
the engine. Market data can be sourced in three ways.

## 1. Group curve tool (internal)

The group's curve tool already covers every currency and curve in use,
including the BRL implied curves. Export the tool's EOD curves to a file and map its
columns and codes with a YAML mapping
([`QuoteMapping`][irrbb.data.market_sources.QuoteMapping]):

```yaml
# examples/head_office_curves_mapping.yaml
dataset: curve_quotes
date_column: BUSINESS_DATE
columns: {CURVE_ID: curve_name, CCY: currency, TENOR: tenor, INSTRUMENT: quote_type,
          MID_RATE: value, DAY_COUNT: day_count}
value_scale: 0.01                       # percent → decimal
quote_type_map: {DEP: DEPOSIT, OIS: OIS_SWAP, ZC: ZERO_CC}
curve_name_map: {USD_SOFR_OIS: USD-SOFR, EUR_ESTR_OIS: EUR-ESTR}
discount_curves: [USD-SOFR, EUR-ESTR]
```

If the tool already produces zero curves, send them as `ZERO_CC` (or
`DISCOUNT_FACTOR`); they are used as-is.

## BRL: offshore NDF-implied curve

The bank has **no onshore BRL business**. Its BRL risk is entirely in
**NDFs** against USD and EUR, which price off the **offshore** (NDF-implied)
BRL curve. That curve can differ materially from onshore CDI / DI
(convertibility, IOF, funding basis). The `us_bank` profile therefore makes
`BRL-NDF-IMPLIED` **the** BRL discount curve
(`discount_map: {BRL: BRL-NDF-IMPLIED}`), and no CDI curve is needed.

The implied curve can be supplied as zero rates (`ZERO_CC`) if the group tool
provides it as yields. It can also be **bootstrapped from NDF prices** by
covered interest parity against a base curve:

\[
DF_{BRL}(T) = DF_{base}(T)\,\frac{S}{F(T)}
\]

S and F are quoted in BRL per unit of the base currency (USD/BRL against
`USD-SOFR`, EUR/BRL against `EUR-ESTR`):

```csv
curve_name,currency,tenor,quote_type,value,base_curve
BRL-NDF-IMPLIED,BRL,SPOT,FX_SPOT,5.5556,USD-SOFR
BRL-NDF-IMPLIED,BRL,1M,FX_OUTRIGHT,5.5932,USD-SOFR
BRL-NDF-IMPLIED,BRL,3M,FX_OUTRIGHT,5.6680,USD-SOFR
BRL-NDF-IMPLIED,BRL,1Y,FX_POINTS,0.4321,USD-SOFR
```

`FX_POINTS` are in price units (outright − spot). Use
`value_scale_by_quote_type: {FX_POINTS: 0.0001}` in a mapping when points are
quoted in pips. The spot settlement lag (T+2) is ignored.

!!! note "EUR/BRL NDFs"
    For now one offshore curve, implied against USD, is also used for EUR/BRL
    NDFs. This ignores the EUR/USD cross-currency basis, which is small next
    to BRL rate risk. When the group tool publishes a EUR-based BRL implied
    curve, load it under its own name (e.g. built from EURBRL outrights with
    `base_curve: EUR-ESTR`) and switch it on with one setting:

    ```yaml
    ndf_discount_by_pair:
      EURBRL: BRL-NDF-IMPLIED-EUR   # other currency + non-deliverable currency
    ```

    Precedence for an NDF's BRL leg: position `discount_curve` →
    `ndf_discount_by_pair` → `discount_overrides` → the BRL discount curve.
    If the configured curve is missing from market data, the run stops with
    a coverage error.

## 2. Bloomberg

[`BloombergSource`][irrbb.data.market_sources.BloombergSource] reads a ticker
map (`examples/bloomberg_tickers.yaml`) and requests `PX_LAST` for the run date
with a `HistoricalDataRequest`, so re-runs are reproducible. It needs the
Bloomberg Desktop API or B-PIPE and the `blpapi` package:

```bash
uv pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

!!! warning
    Check every ticker on a terminal before production. Tickers missing from
    the response are listed in `BloombergSource.missing`, and the
    `MISSING_TENORS` check flags discount curves that lack required pillars.

## 3. Routing with `sources.yaml`

```yaml
default:          {type: files, root: /data/irrbb}          # positions, cashflows
curve_quotes:     {type: mapped_file, path: /feeds/ho/curves_{as_of}.csv,
                   mapping: head_office_curves_mapping.yaml}
fx_rates:         {type: bloomberg, config: bloomberg_tickers.yaml}
nmd_assumptions:  {type: database}
```

```bash
irrbb run --as-of 2026-11-24 --sources sources.yaml --profile us_bank
```

In code, use [`CompositeSource`][irrbb.data.market_sources.CompositeSource]
directly.

## Data-quality checks

Every run checks its inputs before valuation and stores the findings in the
`market_data_checks` table.

| Check | Severity | Meaning |
|---|---|---|
| `COVERAGE_DISCOUNT_CURVE` / `COVERAGE_FX` | ERROR (always blocks) | A currency held has no discount curve / FX rate |
| `COVERAGE_INDEX_CURVE` | WARNING | A floating index has no curve (falls back to the discount curve) |
| `QUOTE_IMPLAUSIBLE` / `QUOTE_NOT_FINITE` | ERROR | e.g. quotes sent in percent; checked **before** bootstrapping |
| `MISSING_TENORS` | WARNING | A discount curve lacks a required pillar (`us_bank`: 1M, 3M, 6M, 1Y, 2Y, 3Y) |
| `CURVE_EXTRAPOLATION` | WARNING | Cash flows (or projected coupons) run beyond the last pillar of their curve and are extrapolated flat |
| `PILLAR_JUMP` | WARNING | Adjacent zero rates differ by more than `max_pillar_jump_bp` |
| `DAILY_MOVE` / `QUOTE_ADDED` / `QUOTE_DROPPED` | WARNING | Versus `--compare-to` date |

Thresholds are set under `market_data_checks` in the configuration.
`fail_on_error: false` records quote errors instead of stopping the run;
coverage errors always stop it. The staged curves can also be checked on
their own with `GET /data/{as_of}/market-checks?compare_to=...`.
