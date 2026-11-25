from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from irrbb.curves.curve_set import CurveSet, FxRates
from irrbb.curves.yield_curve import YieldCurve

AS_OF = date(2026, 11, 24)


def flat_curve(rate: float, name: str = "USD-SOFR", ccy: str = "USD") -> YieldCurve:
    return YieldCurve(name, ccy, AS_OF, np.array([0.1, 50.0]), np.array([rate, rate]))


@pytest.fixture
def as_of() -> date:
    return AS_OF


@pytest.fixture
def flat_usd() -> CurveSet:
    return CurveSet(AS_OF, {"USD-SOFR": flat_curve(0.04)}, {"USD": "USD-SOFR"})


@pytest.fixture
def two_ccy() -> CurveSet:
    return CurveSet(
        AS_OF,
        {"USD-SOFR": flat_curve(0.04), "EUR-ESTR": flat_curve(0.02, "EUR-ESTR", "EUR")},
        {"USD": "USD-SOFR", "EUR": "EUR-ESTR"},
    )


@pytest.fixture
def fx() -> FxRates:
    return FxRates("USD", {"EUR": 1.10})
