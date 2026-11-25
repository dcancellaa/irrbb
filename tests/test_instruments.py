from datetime import date

import numpy as np
import polars as pl
import pytest

from irrbb.behavioral.nmd import NMDAssumption, NMDModel
from irrbb.core.dates import add_months, build_schedule, year_fraction
from irrbb.core.enums import (
    Amortization,
    BehaviouralKind,
    DayCount,
    Direction,
    Frequency,
    NMDCategory,
    ProductType,
    RateType,
)
from irrbb.engine.frames import (
    build_cashflow_skeleton,
    build_repricing_frame,
    discount,
    project,
)
from irrbb.instruments.factory import build_portfolio
from irrbb.instruments.leg import Leg

from .conftest import AS_OF


def leg(**kw) -> Leg:
    base = dict(
        position_id="P",
        leg_id="1",
        product_type=ProductType.LOAN,
        currency="USD",
        direction=Direction.RECEIVE,
        notional=100.0,
        start_date=AS_OF,
        maturity_date=add_months(AS_OF, 24),
    )
    base.update(kw)
    return Leg(**base)


def pv(legs, curves) -> float:
    sk = build_cashflow_skeleton(legs, AS_OF, curves)
    return float(discount(project(sk, curves, AS_OF), curves, AS_OF).get_column("pv").sum())


def test_floating_leg_at_reset_prices_at_par(flat_usd):
    flt = leg(rate_type=RateType.FLOAT, index="USD-SOFR", payment_frequency=Frequency.QUARTERLY)
    assert pv([flt], flat_usd) == pytest.approx(100.0, rel=1e-10)


def test_zero_coupon_deposit(flat_usd):
    mat = add_months(AS_OF, 12)
    td = leg(
        product_type=ProductType.TERM_DEPOSIT,
        direction=Direction.PAY,
        maturity_date=mat,
        fixed_rate=0.05,
    )
    tau = year_fraction(AS_OF, mat, DayCount.ACT_360)
    df = np.exp(-0.04 * (mat - AS_OF).days / 365)
    assert pv([td], flat_usd) == pytest.approx(-100 * (1 + 0.05 * tau) * df)


def test_at_market_irs_is_worth_zero(flat_usd):
    mat = add_months(AS_OF, 60)
    curve = flat_usd.discount_curve("USD")
    periods = build_schedule(AS_OF, mat, Frequency.ANNUAL)
    taus = np.array([year_fraction(p.start, p.end, DayCount.ACT_360) for p in periods])
    dfs = curve.discount_factor([(p.end - AS_OF).days / 365 for p in periods])
    par = (1 - dfs[-1]) / np.sum(taus * dfs)
    common = dict(
        product_type=ProductType.IRS,
        maturity_date=mat,
        final_exchange=False,
        payment_frequency=Frequency.ANNUAL,
    )
    fixed = leg(leg_id="FIX", direction=Direction.PAY, fixed_rate=par, **common)
    flt = leg(leg_id="FLT", rate_type=RateType.FLOAT, index="USD-SOFR", **common)
    assert pv([fixed, flt], flat_usd) == pytest.approx(0.0, abs=1e-9)


def test_fx_forward_at_cip_fair_value_is_zero(two_ccy, fx):
    mat = add_months(AS_OF, 6)
    t = (mat - AS_OF).days / 365
    fwd = fx.forward("EUR", t, two_ccy)
    eur = leg(
        product_type=ProductType.FX_FORWARD,
        currency="EUR",
        rate_type=RateType.NONE,
        maturity_date=mat,
        notional=10.0,
    )
    usd = leg(
        product_type=ProductType.FX_FORWARD,
        leg_id="2",
        direction=Direction.PAY,
        rate_type=RateType.NONE,
        maturity_date=mat,
        notional=10.0 * fwd,
    )
    sk = build_cashflow_skeleton([eur, usd], AS_OF, two_ccy)
    flows = discount(project(sk, two_ccy, AS_OF), two_ccy, AS_OF)
    total = sum(float(r["pv"]) * fx.rate(r["currency"]) for r in flows.iter_rows(named=True))
    assert total == pytest.approx(0.0, abs=1e-9)


def test_linear_amortisation_and_prepayment(flat_usd):
    plain = leg(
        amortization=Amortization.LINEAR, payment_frequency=Frequency.MONTHLY, fixed_rate=0.05
    )
    prepay = leg(
        amortization=Amortization.LINEAR,
        payment_frequency=Frequency.MONTHLY,
        fixed_rate=0.05,
        runoff_rate=0.2,
        behavioural_kind=BehaviouralKind.PREPAYMENT,
    )
    for lg in (plain, prepay):
        sk = build_cashflow_skeleton([lg], AS_OF, flat_usd)
        assert sk.get_column("principal").sum() == pytest.approx(100.0)

    def wal(lg, scalar=1.0):
        sk = build_cashflow_skeleton([lg], AS_OF, flat_usd, prepayment_scalar=scalar)
        t = sk.select((pl.col("pay_date") - AS_OF).dt.total_days() / 365).to_series()
        return float((t * sk.get_column("principal")).sum() / 100)

    assert wal(prepay) < wal(plain)
    assert wal(prepay, 1.2) < wal(prepay, 0.8)  # parallel-down prepays faster


def test_annuity_balances_reach_zero(flat_usd):
    mtg = leg(
        amortization=Amortization.ANNUITY,
        payment_frequency=Frequency.MONTHLY,
        fixed_rate=0.06,
        maturity_date=add_months(AS_OF, 360),
    )
    sk = build_cashflow_skeleton([mtg], AS_OF, flat_usd)
    pay = (
        sk.get_column("principal") + sk.get_column("balance") * 0.06 * sk.get_column("tau")
    ).to_numpy()
    assert sk.get_column("principal").sum() == pytest.approx(100.0)
    assert np.std(pay[:-1]) / np.mean(pay[:-1]) < 0.05  # roughly level payments


def test_repricing_view_of_float_leg(flat_usd):
    flt = leg(
        rate_type=RateType.FLOAT,
        index="USD-SOFR",
        payment_frequency=Frequency.QUARTERLY,
        start_date=add_months(AS_OF, -1),
        current_rate=0.045,
    )
    rp = build_repricing_frame([flt], AS_OF, flat_usd)
    assert rp.height == 1
    row = rp.row(0, named=True)
    assert row["principal"] == pytest.approx(100.0)
    assert row["pay_date"] == add_months(AS_OF, 2) or row["pay_date"] > AS_OF


def test_nmd_caps_and_split(as_of):
    model = NMDModel()
    eve, nii = model.legs(
        position_id="D",
        product_type=ProductType.DDA,
        currency="USD",
        balance=1000.0,
        client_rate=0.001,
        as_of=as_of,
        category=NMDCategory.WHOLESALE,
        assumption=NMDAssumption(core_share=0.9, core_avg_maturity=6, beta=0.2),
    )
    core = next(lg for lg in eve if lg.leg_id == "CORE")
    assert core.notional == pytest.approx(500.0)  # capped at 50%
    assert core.maturity_date == add_months(as_of, 96)  # 2 x 4y cap
    assert sum(lg.notional for lg in eve) == pytest.approx(1000.0)
    assert nii[0].notional == 1000.0 and nii[0].rate_type is RateType.ADMINISTERED


def test_factory_defaults_and_rejections(as_of):
    df = pl.DataFrame(
        {
            "position_id": ["TD", "BAD", "OLD"],
            "product_type": ["TERM_DEPOSIT", "LOAN", "LOAN"],
            "currency": ["USD", "USD", "USD"],
            "notional": [10.0, 10.0, 10.0],
            "maturity_date": [add_months(as_of, 6), None, date(2020, 1, 1)],
            "fixed_rate": [0.04, 0.05, 0.05],
        }
    )
    pf = build_portfolio(df, as_of)
    assert [lg.position_id for lg in pf.eve_legs] == ["TD"]
    td = pf.eve_legs[0]
    assert td.direction is Direction.PAY
    assert td.behavioural_kind is BehaviouralKind.EARLY_REDEMPTION
    assert [p for p, _ in pf.rejected] == ["BAD"]
