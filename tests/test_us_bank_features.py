"""US-bank features: US scenarios & ramps, profile, limits, reconciliation, data
quality, deposit-study assumptions, BRL implied curves, market data, PostgreSQL."""

import os
from datetime import date

import numpy as np
import polars as pl
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from irrbb.behavioral.nmd import AssumptionTable
from irrbb.config import CustomScenarioConfig, LimitConfig, RunConfig, list_profiles
from irrbb.core.enums import NMDCategory, ProductType
from irrbb.data.market_sources import (
    BloombergConfig,
    BloombergSource,
    CompositeSource,
    QuoteMapping,
    TickerSpec,
)
from irrbb.data.quality import DataQualityError, compare_quotes
from irrbb.data.sample import (
    sample_cashflows,
    sample_curve_quotes,
    sample_fx,
    sample_positions,
    synthetic_portfolio,
)
from irrbb.data.schemas import DATASET_CONTRACTS, normalise
from irrbb.data.sources import DatabaseSource
from irrbb.db.models import Base
from irrbb.db.repository import Repository, get_engine
from irrbb.engine import run_from_frames
from irrbb.engine.frames import build_skeleton, cashflow_frame, project
from irrbb.instruments.factory import build_portfolio
from irrbb.measures.limits import evaluate_limits
from irrbb.scenarios.shocks import ParallelShock, Scenario, us_scenarios
from irrbb.service import run_from_source

from .conftest import AS_OF


def run(cfg: RunConfig, positions=None, cashflows=None, quotes=None, nmd=None):
    return run_from_frames(
        cfg,
        quotes if quotes is not None else sample_curve_quotes(),
        sample_fx(),
        sample_positions(AS_OF) if positions is None else positions,
        cashflows,
    )


# ---------------------------------------------------------------- scenarios
def test_us_scenario_set():
    scns = us_scenarios([100, 200], [200], 12)
    names = [s.name for s in scns]
    assert names == [
        "US_PARALLEL_UP_100",
        "US_PARALLEL_DOWN_100",
        "US_PARALLEL_UP_200",
        "US_PARALLEL_DOWN_200",
        "US_RAMP_UP_200_12M",
        "US_RAMP_DOWN_200_12M",
    ]
    ramp = scns[-2]
    assert ramp.measures == frozenset({"NII"}) and not ramp.for_eve
    assert ramp.ramp_weight(np.array([0.0, 0.5, 1.0, 2.0])) == pytest.approx([0, 0.5, 1, 1])


def test_ramp_is_smaller_than_instantaneous_and_nii_only():
    cfg = RunConfig(
        as_of=AS_OF,
        standard_scenarios=False,
        compute_key_rates=False,
        custom_scenarios=[
            CustomScenarioConfig(name="SHOCK", bp=200),
            CustomScenarioConfig(name="RAMP", bp=200, ramp_months=12),
        ],
    )
    res = run(cfg)
    d = dict(res["nii_regulatory"].select("scenario", "delta_nii_rep").iter_rows())
    assert 0 < d["RAMP"] < d["SHOCK"]
    assert "RAMP" not in res["eve"].get_column("scenario").to_list()


def test_outlier_test_ignores_non_regulatory_scenarios():
    huge = CustomScenarioConfig(name="HUGE_DOWN", bp=-1500)
    base = run(RunConfig(as_of=AS_OF, tier1_capital=1e9, compute_key_rates=False))
    more = run(
        RunConfig(as_of=AS_OF, tier1_capital=1e9, compute_key_rates=False, custom_scenarios=[huge])
    )
    a, b = base["outlier_tests"], more["outlier_tests"]
    assert a.get_column("worst_scenario").to_list() == b.get_column("worst_scenario").to_list()
    assert a.get_column("loss").to_list() == pytest.approx(b.get_column("loss").to_list())
    assert "HUGE_DOWN" in more["nii_regulatory"].get_column("scenario").to_list()


# ---------------------------------------------------------------- profiles
def test_us_bank_profile():
    assert list_profiles() == ["us_bank"]
    cfg = RunConfig.from_profile("us_bank", as_of=AS_OF)
    families = {s.family for s in cfg.scenarios()}
    assert {"BCBS", "US_PARALLEL", "US_RAMP", "CUSTOM"} <= families
    assert cfg.outlier_tests is False
    assert cfg.discount_map == {"BRL": "BRL-NDF-IMPLIED"}
    assert cfg.ndf_settlement_currencies == ["USD", "EUR"]
    assert all(lim.threshold is None for lim in cfg.limits)
    res = run(cfg.model_copy(update={"compute_key_rates": False, "tier1_capital": 9e8}))
    assert res["outlier_tests"].is_empty()
    assert set(res["limits"].get_column("status")) == {"NOT_SET"}
    assert res["limits"].get_column("measure").is_not_null().all()
    with pytest.raises(ValueError):
        RunConfig.from_profile("nope", as_of=AS_OF)


# ---------------------------------------------------------------- limits
def test_limit_statuses():
    scns = [
        Scenario("BASE"),
        Scenario("UP", ParallelShock(200)),
        Scenario("DN", ParallelShock(-200)),
    ]
    nii = pl.DataFrame(
        {
            "scenario": ["BASE", "UP", "DN"],
            "currency": ["USD"] * 3,
            "nii_rep": [100.0, 110.0, 91.0],
            "delta_nii_rep": [0.0, 10.0, -9.0],
        }
    )
    eve = pl.DataFrame(
        {
            "method": ["FULL_REVALUATION"] * 3,
            "scenario": ["BASE", "UP", "DN"],
            "currency": ["USD"] * 3,
            "eve_rep": [1000.0, 950.0, 1010.0],
            "delta_eve_rep": [0.0, -50.0, 10.0],
        }
    )
    reg = pl.DataFrame(
        {"method": ["FULL_REVALUATION"] * 2, "scenario": ["UP", "DN"], "eve_loss": [50.0, 0.0]}
    )
    limits = [
        LimitConfig(name="NII_OK", metric="NII", threshold=0.20),
        LimitConfig(name="NII_WARN", metric="NII", threshold=0.10),
        LimitConfig(name="NII_BREACH", metric="NII", threshold=0.05),
        LimitConfig(name="EVE_ABS", metric="EVE", basis="ABSOLUTE", threshold=40),
        LimitConfig(name="EVE_CAP", metric="EVE", basis="PCT_CAPITAL", threshold=0.1),
    ]
    out = evaluate_limits(limits, scns, eve, reg, nii, tier1_capital=None)
    status = dict(out.select("limit", "status").iter_rows())
    assert status == {
        "NII_OK": "OK",
        "NII_WARN": "WARNING",
        "NII_BREACH": "BREACH",
        "EVE_ABS": "BREACH",
        "EVE_CAP": "N/A",
    }
    assert out.filter(pl.col("limit") == "NII_OK").get_column("worst_scenario").item() == "DN"


# ---------------------------------------------------------------- reconciliation
def _engine_flows_as_source(positions: pl.DataFrame) -> pl.DataFrame:
    cfg = RunConfig(as_of=AS_OF)
    from irrbb.data.market import curves_from_frame

    curves = curves_from_frame(sample_curve_quotes(), AS_OF)
    pos, _ = normalise(positions, *DATASET_CONTRACTS["positions"], "positions")
    pf = build_portfolio(pos, cfg.as_of)
    flows = project(
        cashflow_frame(build_skeleton(pf.eve_legs, AS_OF, curves), contractual=True), curves, AS_OF
    )
    return flows.select(
        "position_id", "product_type", "currency", "pay_date", "interest", "principal"
    )


def test_reconciliation_match_break_and_no_double_count():
    positions = sample_positions(AS_OF).filter(pl.col("product_type").is_in(["LOAN", "IRS"]))
    source = _engine_flows_as_source(positions)
    cfg = RunConfig(as_of=AS_OF, compute_key_rates=False)
    alone = run(cfg, positions)
    both = run(cfg, positions, source)
    # AUTO mode: overlapping flows are reconciled, not valued twice.
    e1 = alone["eve"].filter(pl.col("scenario") == "BASE").get_column("eve_rep").sum()
    e2 = both["eve"].filter(pl.col("scenario") == "BASE").get_column("eve_rep").sum()
    assert e1 == pytest.approx(e2)
    status = set(both["recon_summary"].get_column("status"))
    assert status == {"MATCH"}
    # Break one loan's principal.
    bad = source.with_columns(
        pl.when((pl.col("position_id") == "LN-USD-CRE-FIX") & (pl.col("principal") != 0))
        .then(pl.col("principal") * 1.1)
        .otherwise(pl.col("principal"))
        .alias("principal")
    )
    rec = run(cfg, positions, bad)["recon_summary"]
    row = rec.filter(pl.col("position_id") == "LN-USD-CRE-FIX").row(0, named=True)
    assert row["status"] in {"PRINCIPAL_BREAK", "BREAK"}


def test_value_mode_double_counts_by_design():
    positions = sample_positions(AS_OF).filter(pl.col("position_id") == "LN-GBP-FIX")
    source = _engine_flows_as_source(positions)
    cfg = RunConfig(as_of=AS_OF, compute_key_rates=False, external_cashflow_mode="VALUE")
    res = run(cfg, positions, source)
    base = res["position_pv"].filter(pl.col("scenario") == "BASE")
    alone = run(RunConfig(as_of=AS_OF, compute_key_rates=False), positions)["position_pv"]
    assert base.get_column("pv").sum() == pytest.approx(
        2 * alone.filter(pl.col("scenario") == "BASE").get_column("pv").sum()
    )


# ---------------------------------------------------------------- data quality
def test_percent_quotes_are_blocked():
    q = sample_curve_quotes().with_columns(
        pl.when(pl.col("curve_name") == "USD-SOFR")
        .then(pl.col("value") * 100)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    with pytest.raises(DataQualityError, match="QUOTE_IMPLAUSIBLE"):
        run(RunConfig(as_of=AS_OF), quotes=q)


def test_missing_fx_is_blocking_even_without_fail_on_error():
    cfg = RunConfig.model_validate({"as_of": AS_OF, "market_data_checks": {"fail_on_error": False}})
    with pytest.raises(DataQualityError, match="COVERAGE_FX"):
        run_from_frames(
            cfg,
            sample_curve_quotes(),
            sample_fx().filter(pl.col("currency") != "BRL"),
            sample_positions(AS_OF),
        )


def test_day_over_day_moves():
    today = sample_curve_quotes()
    prev = today.with_columns(
        pl.when(pl.col("tenor") == "5Y")
        .then(pl.col("value") - 0.01)
        .otherwise(pl.col("value"))
        .alias("value")
    ).filter(pl.col("tenor") != "30Y")
    f = compare_quotes(today, prev, 50)
    assert set(f.get_column("check")) == {"DAILY_MOVE", "QUOTE_ADDED"}


# ---------------------------------------------------------------- NMD study
def test_assumption_table_precedence():
    tbl = AssumptionTable(
        pl.DataFrame(
            {
                "product_type": ["MMDA", "MMDA", "MMDA"],
                "currency": [None, "USD", "USD"],
                "nmd_category": [None, None, "WHOLESALE"],
                "core_share": [0.1, 0.2, 0.3],
                "core_avg_maturity": [1.0, 1.0, 1.0],
                "beta": [0.5, 0.5, 0.5],
                "reprice_months": [1, 1, 1],
            }
        )
    )
    look = tbl.lookup
    assert look(ProductType.MMDA, "USD", NMDCategory.WHOLESALE).core_share == 0.3
    assert look(ProductType.MMDA, "USD", NMDCategory.RETAIL_NON_TRANSACTIONAL).core_share == 0.2
    assert look(ProductType.MMDA, "EUR", NMDCategory.WHOLESALE).core_share == 0.1
    assert look(ProductType.DDA, "USD", NMDCategory.WHOLESALE) is None


def test_duplicate_legs_rejected():
    df = pl.concat([sample_positions(AS_OF).head(1)] * 2)
    pos, _ = normalise(df, *DATASET_CONTRACTS["positions"], "positions")
    pf = build_portfolio(pos, AS_OF)
    assert len(pf.rejected) == 1 and "duplicate" in pf.rejected[0][1]


# ---------------------------------------------------------------- market data adapters
def test_quote_mapping_head_office_export():
    raw = pl.DataFrame(
        {
            "CURVE": ["SOFR", "SOFR"],
            "CCY": ["USD", "USD"],
            "TERM": ["12M", "5Y"],
            "TYPE": ["SW", "SW"],
            "MID": [4.10, 3.95],
            "ASOF": ["2026-11-24", "2026-11-24"],
        }
    )
    m = QuoteMapping(
        columns={
            "CURVE": "curve_name",
            "CCY": "currency",
            "TERM": "tenor",
            "TYPE": "quote_type",
            "MID": "value",
        },
        value_scale=0.01,
        quote_type_map={"SW": "OIS_SWAP"},
        curve_name_map={"SOFR": "USD-SOFR"},
        tenor_map={"12M": "1Y"},
        date_column="ASOF",
        discount_curves=["USD-SOFR"],
    )
    out = m.apply(raw, AS_OF)
    assert out.get_column("value").to_list() == pytest.approx([0.041, 0.0395])
    assert out.get_column("tenor").to_list() == ["1Y", "5Y"]
    assert out.get_column("curve_name").unique().to_list() == ["USD-SOFR"]
    assert out.get_column("is_discount").all()


class FakeFetcher:
    def __init__(self, values):
        self.values = values

    def fetch(self, securities, field, as_of):
        return {s: self.values[s] for s in securities if s in self.values}


def test_bloomberg_source_with_fake_fetcher():
    cfg = BloombergConfig(
        curves=[
            TickerSpec(ticker="USOSFR1 Curncy", curve_name="USD-SOFR", currency="USD", tenor="1Y"),
            TickerSpec(ticker="USOSFR5 Curncy", curve_name="USD-SOFR", currency="USD", tenor="5Y"),
            TickerSpec(ticker="MISSING Curncy", curve_name="USD-SOFR", currency="USD", tenor="10Y"),
        ],
        fx=[TickerSpec(ticker="USDMXN Curncy", currency="MXN", scale=1.0, invert=True)],
    )
    src = BloombergSource(
        cfg, FakeFetcher({"USOSFR1 Curncy": 4.1, "USOSFR5 Curncy": 3.9, "USDMXN Curncy": 20.0})
    )
    q = src.load("curve_quotes", AS_OF)
    assert q.height == 2 and q.get_column("value").to_list() == pytest.approx([0.041, 0.039])
    assert src.missing == ["MISSING Curncy"]
    assert src.load("fx_rates", AS_OF).get_column("rate").item() == pytest.approx(0.05)
    comp = CompositeSource({"curve_quotes": src})
    assert comp.load("curve_quotes", AS_OF).height == 2 and comp.load("positions", AS_OF) is None


# ---------------------------------------------------------------- databases
def _db_urls(tmp_path):
    urls = [f"sqlite:///{tmp_path / 'x.sqlite'}"]
    if os.environ.get("IRRBB_TEST_PG_URL"):
        urls.append(os.environ["IRRBB_TEST_PG_URL"])
    return urls


def test_database_round_trip_all_backends(tmp_path):
    nmd = pl.DataFrame(
        {
            "product_type": ["MMDA"],
            "currency": ["USD"],
            "nmd_category": [None],
            "core_share": [0.45],
            "core_avg_maturity": [1.75],
            "beta": [0.6],
            "reprice_months": [1],
            "source": ["2026 deposit study"],
        }
    )
    big_book = pl.concat(
        [sample_positions(AS_OF), synthetic_portfolio(2500, AS_OF)], how="diagonal_relaxed"
    )
    data = {
        "positions": big_book,
        "cashflows": sample_cashflows(AS_OF),
        "curve_quotes": sample_curve_quotes(),
        "fx_rates": sample_fx(),
    }
    for url in _db_urls(tmp_path):
        engine = get_engine(url)
        Base.metadata.drop_all(engine)
        repo = Repository(engine)
        repo.create_all()
        for name, df in data.items():
            norm, _ = normalise(df, *DATASET_CONTRACTS[name], name)
            repo.stage(name, norm, AS_OF)
        # deposit study effective earlier than the run date
        norm, _ = normalise(nmd, *DATASET_CONTRACTS["nmd_assumptions"], "nmd")
        repo.stage("nmd_assumptions", norm, date(2026, 1, 1))
        assert repo.load("nmd_assumptions", AS_OF).height == 1
        cfg = RunConfig.from_profile("us_bank", as_of=AS_OF, compute_key_rates=False)
        res = run_from_source(DatabaseSource(repo), cfg)
        assert repo.load("positions", AS_OF).height == big_book.height  # COPY on PG
        run_id = repo.save_run(res)
        assert repo.run_table(run_id, "position_pv").height == res["position_pv"].height
        assert repo.run_table(run_id, "limits").height == len(cfg.limits)
        stored = repo.run_table(run_id, "eve").filter(pl.col("scenario") == "BASE")
        expected = res["eve"].filter(pl.col("scenario") == "BASE")
        assert stored.get_column("eve").sum() == pytest.approx(
            expected.get_column("eve").sum(), rel=1e-12
        ), url
        assert repo.delete_run(run_id)
        Base.metadata.drop_all(engine)


def test_postgres_ddl_uses_double_precision():
    ddl = "\n".join(
        str(CreateTable(t).compile(dialect=postgresql.dialect()))
        for t in Base.metadata.sorted_tables
    )
    assert "DOUBLE PRECISION" in ddl and " FLOAT" not in ddl


# ---------------------------------------------------------------- scale
def test_synthetic_book_runs_without_rejections():
    pos = synthetic_portfolio(3000, AS_OF)
    res = run(RunConfig(as_of=AS_OF, compute_key_rates=False), pos)
    assert res.rejected == []
    assert res["position_pv"].filter(pl.col("scenario") == "BASE").height >= 2900


# ---------------------------------------------------------------- templates
EXAMPLES = __import__("pathlib").Path(__file__).parents[1] / "examples"


def test_example_templates_parse():
    from irrbb.data.market_sources import BloombergConfig, QuoteMapping

    bbg = BloombergConfig.from_yaml(EXAMPLES / "bloomberg_tickers.yaml")
    assert len(bbg.curves) >= 12 and {s.currency for s in bbg.fx} >= {"EUR", "MXN"}
    m = QuoteMapping.from_yaml(EXAMPLES / "head_office_curves_mapping.yaml")
    assert m.value_scale == 0.01
    nmd = pl.read_csv(EXAMPLES / "nmd_assumptions.csv")
    norm, _ = normalise(nmd, *DATASET_CONTRACTS["nmd_assumptions"], "nmd")
    assert len(AssumptionTable(norm)) == nmd.height
    RunConfig.from_yaml(EXAMPLES / "run_config.yaml", as_of=AS_OF).scenarios()


def test_sources_yaml_routing(tmp_path):
    from irrbb.data.market_sources import build_sources
    from irrbb.data.sample import write_sample_data

    write_sample_data(tmp_path / "data", AS_OF)
    ho = (
        sample_curve_quotes()
        .rename({"curve_name": "CURVE_ID", "value": "MID_RATE"})
        .with_columns(
            pl.when(pl.col("quote_type").str.starts_with("FX_"))
            .then(pl.col("MID_RATE"))
            .otherwise(pl.col("MID_RATE") * 100)
            .alias("MID_RATE")
        )
    )
    ho.write_csv(tmp_path / f"ho_curves_{AS_OF}.csv")
    (tmp_path / "map.yaml").write_text(
        "columns: {CURVE_ID: curve_name, MID_RATE: value}\nvalue_scale: 0.01\n"
    )
    (tmp_path / "nmd.csv").write_text((EXAMPLES / "nmd_assumptions.csv").read_text())
    (tmp_path / "sources.yaml").write_text(
        "default: {type: files, root: data}\n"
        "curve_quotes: {type: mapped_file, path: 'ho_curves_{as_of}.csv', mapping: map.yaml}\n"
        "nmd_assumptions: {type: mapped_file, path: nmd.csv}\n"
    )
    src = build_sources(tmp_path / "sources.yaml")
    q = src.load("curve_quotes", AS_OF)
    rates = q.filter(~pl.col("quote_type").str.starts_with("FX_"))
    assert rates.get_column("value").max() < 0.2
    fx = q.filter(pl.col("quote_type") == "FX_SPOT").get_column("value").item()
    assert fx == pytest.approx(1 / 0.18, rel=1e-5)  # FX prices are not rescaled
    res = run_from_source(src, RunConfig(as_of=AS_OF, compute_key_rates=False))
    assert res.summary()["positions"] > 10


# ---------------------------------------------------------------- BRL implied curves
def test_implied_curve_reprices_outrights():
    from irrbb.core.dates import add_tenor
    from irrbb.data.market import curves_from_frame

    quotes = sample_curve_quotes()
    cs = curves_from_frame(quotes, AS_OF)
    imp, usd = cs.curves["BRL-NDF-IMPLIED"], cs.curves["USD-SOFR"]
    rows = quotes.filter(pl.col("curve_name") == "BRL-NDF-IMPLIED")
    spot = rows.filter(pl.col("quote_type") == "FX_SPOT").get_column("value").item()
    for r in rows.filter(pl.col("quote_type") == "FX_OUTRIGHT").iter_rows(named=True):
        t = (add_tenor(AS_OF, r["tenor"]) - AS_OF).days / 365
        fwd = spot * usd.discount_factor(t)[0] / imp.discount_factor(t)[0]
        assert fwd == pytest.approx(r["value"], rel=1e-12)
    assert cs.discount_map["BRL"] == "BRL-NDF-IMPLIED"  # offshore curve is the BRL curve
    assert "BRL-CDI" not in cs.curves


def test_fx_points_equal_outrights():
    from irrbb.data.market import curves_from_frame

    q = sample_curve_quotes()
    spot = q.filter(pl.col("quote_type") == "FX_SPOT").get_column("value").item()
    outright = pl.col("quote_type") == "FX_OUTRIGHT"
    pts = q.with_columns(
        pl.when(outright).then(pl.col("value") - spot).otherwise(pl.col("value")).alias("value"),
        pl.when(outright)
        .then(pl.lit("FX_POINTS"))
        .otherwise(pl.col("quote_type"))
        .alias("quote_type"),
    )
    a = curves_from_frame(q, AS_OF).curves["BRL-NDF-IMPLIED"]
    b = curves_from_frame(pts, AS_OF).curves["BRL-NDF-IMPLIED"]
    assert np.allclose(a.zero_rates, b.zero_rates, atol=1e-10)


def test_brl_discounted_on_offshore_curve_and_explicit_curve_checked():
    ndf = sample_positions(AS_OF).filter(pl.col("product_type") == "NDF")
    cfg = RunConfig(as_of=AS_OF, compute_key_rates=False, standard_scenarios=False)
    flows = run(cfg, ndf)["cashflows"].filter(pl.col("currency") == "BRL")
    from irrbb.data.market import curves_from_frame

    implied = curves_from_frame(sample_curve_quotes(), AS_OF).curves["BRL-NDF-IMPLIED"]
    assert flows.get_column("df").to_list() == pytest.approx(
        implied.discount_factor(flows.get_column("t").to_numpy()).tolist()
    )
    bad = ndf.with_columns(pl.lit("BRL-MISSING").alias("discount_curve"))
    with pytest.raises(DataQualityError, match="BRL-MISSING"):
        run(cfg, bad)
    no_brl_curve = sample_curve_quotes().filter(pl.col("currency") != "BRL")
    with pytest.raises(DataQualityError, match="COVERAGE_DISCOUNT_CURVE"):
        run(cfg, ndf, quotes=no_brl_curve)


def test_ndf_settlement_report_matches_two_leg_mtm():
    res = run(RunConfig(as_of=AS_OF, compute_key_rates=False, standard_scenarios=False))
    rep = res["ndf_settlements"]
    assert dict(rep.select("position_id", "settlement_currency").iter_rows()) == {
        "NDF-EURBRL-6M": "EUR",
        "NDF-USDBRL-3M": "USD",
    }
    mtm = (
        res["position_pv"]
        .filter((pl.col("scenario") == "BASE") & (pl.col("product_type") == "NDF"))
        .group_by("position_id")
        .agg(pl.col("pv_rep").sum())
    )
    joined = rep.join(mtm, on="position_id", suffix="_legs")
    for r in joined.iter_rows(named=True):
        assert r["pv_rep"] == pytest.approx(r["pv_rep_legs"], rel=1e-10)
    usd = rep.filter(pl.col("position_id") == "NDF-USDBRL-3M").row(0, named=True)
    assert usd["nd_currency"] == "BRL" and usd["other_currency"] == "USD"
    assert usd["contract_rate"] == pytest.approx(200e6 / 35.4e6)
    assert 5.0 < usd["market_forward"] < 7.0  # BRL per USD


def _ndf_rows(settle_brl, settle_usd):
    m = __import__("irrbb.core.dates", fromlist=["add_months"]).add_months
    return pl.DataFrame(
        {
            "position_id": ["N1", "N1"],
            "leg_id": ["BRL", "USD"],
            "product_type": ["NDF", "NDF"],
            "currency": ["BRL", "USD"],
            "direction": ["PAY", "RECEIVE"],
            "notional": [55.0e6, 10.0e6],
            "maturity_date": [m(AS_OF, 3), m(AS_OF, 3)],
            "settlement_currency": [settle_brl, settle_usd],
        }
    )


@pytest.mark.parametrize(
    ("brl", "usd", "outcome"),
    [
        ("USD", "USD", "ok"),
        ("EUR", "EUR", "ok"),
        (None, None, "assumed"),
        ("GBP", "GBP", "rejected"),
        ("USD", "EUR", "rejected"),
    ],
)
def test_ndf_settlement_validation(brl, usd, outcome):
    pos, _ = normalise(_ndf_rows(brl, usd), *DATASET_CONTRACTS["positions"], "positions")
    pf = build_portfolio(pos, AS_OF)
    if outcome == "rejected":
        assert [p for p, _ in pf.rejected] == ["N1"] and not pf.eve_legs
        return
    assert {lg.settlement_currency for lg in pf.eve_legs} == {brl or "USD"}
    assert bool(pf.assumptions) is (outcome == "assumed")
    if outcome == "assumed":
        res = run(RunConfig(as_of=AS_OF, compute_key_rates=False), _ndf_rows(brl, usd))
        checks = res["market_data_checks"]
        assert "ASSUMPTION" in checks.get_column("check").to_list()


def test_basis_scenario_shocks_only_listed_curve():
    from irrbb.data.market import curves_from_frame

    cs = curves_from_frame(sample_curve_quotes(), AS_OF)
    scn = CustomScenarioConfig(name="B", bp=25, curves=["USD-TERM-SOFR-3M"]).build(
        RunConfig(as_of=AS_OF).lower_bound
    )
    shocked = scn.apply(cs)
    t = np.array([1.0, 5.0])

    def move(name):
        return shocked.curves[name].zero_rate(t) - cs.curves[name].zero_rate(t)

    assert move("USD-TERM-SOFR-3M") == pytest.approx([0.0025, 0.0025])
    assert move("USD-SOFR") == pytest.approx([0.0, 0.0])


# ---------------------------------------------------------------- tenors / EUR hook
def test_us_bank_sample_run_has_no_data_quality_findings():
    cfg = RunConfig.from_profile("us_bank", as_of=AS_OF, compute_key_rates=False)
    assert cfg.market_data_checks.required_tenors == ["1M", "3M", "6M", "1Y", "2Y", "3Y"]
    assert run(cfg)["market_data_checks"].is_empty()


def test_long_ndf_beyond_brl_curve_is_flagged():
    from irrbb.core.dates import add_months

    long_ndf = _ndf_rows("USD", "USD").with_columns(
        pl.lit(add_months(AS_OF, 84)).alias("maturity_date")
    )
    res = run(RunConfig(as_of=AS_OF, compute_key_rates=False), long_ndf)
    f = res["market_data_checks"].filter(pl.col("check") == "CURVE_EXTRAPOLATION")
    assert f.get_column("curve_name").to_list() == ["BRL-NDF-IMPLIED"]
    assert "beyond last pillar 5.0y" in f.get_column("detail").item()


def _with_eur_based_brl_curve() -> pl.DataFrame:
    """Sample quotes plus a BRL curve implied from EURBRL outrights vs €STR."""
    q = sample_curve_quotes()
    usd = q.filter(pl.col("curve_name") == "BRL-NDF-IMPLIED")
    eurbrl_spot = 1.08 / 0.18
    scale = eurbrl_spot / usd.filter(pl.col("quote_type") == "FX_SPOT").get_column("value").item()
    eur = usd.with_columns(
        pl.lit("BRL-NDF-IMPLIED-EUR").alias("curve_name"),
        pl.lit("EUR-ESTR").alias("base_curve"),
        pl.lit(False).alias("is_discount"),
        (pl.col("value") * scale * 0.995).alias("value"),  # some EUR/USD basis
    ).with_columns(
        pl.when(pl.col("quote_type") == "FX_SPOT")
        .then(pl.lit(eurbrl_spot))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    return pl.concat([q, eur], how="diagonal_relaxed")


def test_eurbrl_pair_hook_and_precedence():
    quotes = _with_eur_based_brl_curve()
    hooked = RunConfig(
        as_of=AS_OF,
        compute_key_rates=False,
        standard_scenarios=False,
        ndf_discount_by_pair={"eurbrl": "BRL-NDF-IMPLIED-EUR"},
    )
    pos = sample_positions(AS_OF).filter(pl.col("product_type") == "NDF")
    res = run(hooked, pos, quotes=quotes)
    brl = res["cashflows"].filter(pl.col("currency") == "BRL")
    curve_of = dict(brl.select("position_id", "discount_curve").iter_rows())
    assert curve_of == {"NDF-EURBRL-6M": "BRL-NDF-IMPLIED-EUR", "NDF-USDBRL-3M": None}
    # settlement report still reconciles with the two-leg MtM
    mtm = (
        res["position_pv"]
        .filter(pl.col("scenario") == "BASE")
        .group_by("position_id")
        .agg(pl.col("pv_rep").sum())
    )
    for r in (
        res["ndf_settlements"].join(mtm, on="position_id", suffix="_legs").iter_rows(named=True)
    ):
        assert r["pv_rep"] == pytest.approx(r["pv_rep_legs"], rel=1e-10)
    # an explicit position-level curve beats the pair hook
    explicit = pos.with_columns(
        pl.when(pl.col("currency") == "BRL")
        .then(pl.lit("BRL-NDF-IMPLIED"))
        .otherwise(None)
        .alias("discount_curve")
    )
    res2 = run(hooked, explicit, quotes=quotes)
    assert set(
        res2["cashflows"].filter(pl.col("currency") == "BRL").get_column("discount_curve")
    ) == {"BRL-NDF-IMPLIED"}
    # hook pointing to a curve not in market data → blocking coverage error
    with pytest.raises(DataQualityError, match="BRL-NDF-IMPLIED-EUR"):
        run(hooked, pos)


def test_pair_hook_key_validation():
    with pytest.raises(ValueError, match="EURBRL"):
        RunConfig(as_of=AS_OF, ndf_discount_by_pair={"EUR/BRL": "X"})
