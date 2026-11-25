import polars as pl
import pytest

from irrbb.core.dates import add_months, year_fraction
from irrbb.core.enums import DayCount, Direction, Frequency, ProductType, RateType
from irrbb.engine.frames import build_nii_frame
from irrbb.instruments.leg import Leg
from irrbb.measures.eve import regulatory_eve
from irrbb.measures.nii import nii_segments
from irrbb.scenarios.shocks import ParallelShock, Scenario, base_scenario

from .conftest import AS_OF

UP = Scenario("UP200", ParallelShock(200))


def delta_nii(legs, curves):
    horizon = add_months(AS_OF, 12)
    frame = build_nii_frame(legs, AS_OF, horizon, curves)
    base = nii_segments(frame, curves, curves, base_scenario(), AS_OF)
    up = nii_segments(frame, curves, UP.apply(curves), UP, AS_OF)
    return float(up.get_column("nii").sum() - base.get_column("nii").sum())


def test_fixed_liability_reprices_at_maturity(flat_usd):
    mat = add_months(AS_OF, 6)
    td = Leg(
        "TD",
        "1",
        ProductType.TERM_DEPOSIT,
        "USD",
        Direction.PAY,
        100.0,
        AS_OF,
        mat,
        fixed_rate=0.04,
    )
    expected = -100 * 0.02 * year_fraction(mat, add_months(AS_OF, 12), DayCount.ACT_360)
    assert delta_nii([td], flat_usd) == pytest.approx(expected, rel=0.03)


def test_float_asset_reprices_after_current_period(flat_usd):
    ln = Leg(
        "L",
        "1",
        ProductType.LOAN,
        "USD",
        Direction.RECEIVE,
        100.0,
        add_months(AS_OF, -1),
        add_months(AS_OF, 59),
        rate_type=RateType.FLOAT,
        index="USD-SOFR",
        payment_frequency=Frequency.QUARTERLY,
        current_rate=0.05,
    )
    reset = add_months(AS_OF, 2)
    expected = 100 * 0.02 * year_fraction(reset, add_months(AS_OF, 12), DayCount.ACT_360)
    assert delta_nii([ln], flat_usd) == pytest.approx(expected, rel=0.03)


def test_nmd_beta_pass_through(flat_usd):
    nmd = Leg(
        "D",
        "NII",
        ProductType.MMDA,
        "USD",
        Direction.PAY,
        100.0,
        AS_OF,
        add_months(AS_OF, 360),
        rate_type=RateType.ADMINISTERED,
        fixed_rate=0.02,
        beta=0.5,
        payment_frequency=Frequency.MONTHLY,
    )
    expected = -100 * 0.5 * 0.02 * year_fraction(AS_OF, add_months(AS_OF, 12), DayCount.ACT_360)
    assert delta_nii([nmd], flat_usd) == pytest.approx(expected, rel=0.03)


def test_fixed_to_maturity_has_no_nii_sensitivity(flat_usd):
    ln = Leg(
        "L",
        "1",
        ProductType.LOAN,
        "USD",
        Direction.RECEIVE,
        100.0,
        AS_OF,
        add_months(AS_OF, 36),
        fixed_rate=0.06,
    )
    assert delta_nii([ln], flat_usd) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(("w", "expected"), [(0.0, 10.0), (0.5, 7.5), (1.0, 5.0)])
def test_regulatory_eve_gain_weight(w, expected):
    eve = pl.DataFrame(
        {
            "method": ["M"] * 4,
            "scenario": ["BASE", "BASE", "UP", "UP"],
            "currency": ["USD", "EUR", "USD", "EUR"],
            "eve_rep": [100.0, 50.0, 90.0, 55.0],
            "delta_eve_rep": [0.0, 0.0, -10.0, 5.0],
        }
    )
    reg = regulatory_eve(eve, gain_weight=w)
    assert reg.get_column("eve_loss").item() == pytest.approx(expected)


def test_regulatory_eve_floored_at_zero():
    eve = pl.DataFrame(
        {
            "method": ["M", "M"],
            "scenario": ["BASE", "UP"],
            "currency": ["USD"] * 2,
            "eve_rep": [1.0, 2.0],
            "delta_eve_rep": [0.0, 1.0],
        }
    )
    assert regulatory_eve(eve, 1.0).get_column("eve_loss").item() == 0.0
