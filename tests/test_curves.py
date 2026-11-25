import numpy as np
import pytest

from irrbb.core.dates import add_tenor, build_schedule, year_fraction
from irrbb.core.enums import DayCount, Frequency, Interpolation, QuoteType
from irrbb.curves.bootstrap import CurveQuote, bootstrap_curve
from irrbb.curves.yield_curve import YieldCurve

from .conftest import AS_OF, flat_curve


def test_flat_curve_discount_factor():
    c = flat_curve(0.05)
    assert c.discount_factor(2.0)[0] == pytest.approx(np.exp(-0.1))
    assert c.discount_factor(0.0)[0] == 1.0


def test_forward_rate_consistent_with_discount_factors():
    c = flat_curve(0.03)
    f = c.forward_rate(1.0, 2.0)[0]
    assert f == pytest.approx(np.exp(0.03) - 1.0)


@pytest.mark.parametrize("interp", list(Interpolation))
def test_bootstrap_reprices_inputs(interp):
    dep, ois = QuoteType.DEPOSIT, QuoteType.OIS_SWAP
    quotes = [
        CurveQuote("3M", dep, 0.043),
        CurveQuote("6M", dep, 0.042),
        CurveQuote("1Y", ois, 0.041),
        CurveQuote("2Y", ois, 0.040),
        CurveQuote("5Y", ois, 0.039),
        CurveQuote("10Y", ois, 0.040),
    ]
    c = bootstrap_curve("USD-SOFR", "USD", AS_OF, quotes, DayCount.ACT_360, interp)
    for q in quotes:
        mat = add_tenor(AS_OF, q.tenor)
        t = (mat - AS_OF).days / 365
        if q.quote_type is QuoteType.DEPOSIT or t <= 1:
            tau = year_fraction(AS_OF, mat, DayCount.ACT_360)
            assert 1 / c.discount_factor(t)[0] == pytest.approx(1 + q.value * tau, rel=1e-10)
        else:
            periods = build_schedule(AS_OF, mat, Frequency.ANNUAL)
            taus = np.array([year_fraction(p.start, p.end, DayCount.ACT_360) for p in periods])
            dfs = c.discount_factor([(p.end - AS_OF).days / 365 for p in periods])
            par = (1 - dfs[-1]) / np.sum(taus * dfs)
            assert par == pytest.approx(q.value, abs=1e-10)


def test_shift_and_lower_bound():
    c = YieldCurve("X", "USD", AS_OF, np.array([1.0, 10.0]), np.array([0.005, 0.03]))
    down = c.with_shift(lambda t: np.full_like(t, -0.02), lambda t: np.zeros_like(t))
    z = down.zero_rate([1.0, 10.0])
    assert z[0] == pytest.approx(0.0)  # floored at zero
    assert z[1] == pytest.approx(0.01)
    neg = YieldCurve("N", "EUR", AS_OF, np.array([1.0]), np.array([-0.005]))
    # A rate already below the floor is not pushed up (EBA convention).
    assert neg.with_shift(lambda t: -0.01 + 0 * t, lambda t: 0 * t).zero_rate(1.0)[
        0
    ] == pytest.approx(-0.005)


def test_invalid_pillars_rejected():
    with pytest.raises(ValueError):
        YieldCurve("X", "USD", AS_OF, np.array([2.0, 1.0]), np.array([0.01, 0.01]))
