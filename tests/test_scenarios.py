import numpy as np
import pytest

from irrbb.config import CustomScenarioConfig, RunConfig
from irrbb.core.enums import LowerBoundPolicy, ScenarioName
from irrbb.scenarios.shocks import (
    StandardShock,
    key_rate_shock,
    lower_bound_fn,
    standard_scenarios,
)

from .conftest import AS_OF

T = np.array([0.0, 1.0, 4.0, 30.0])


def shift(kind, ccy="USD"):
    return StandardShock(kind).shift_for(ccy)(T)


def test_parallel_usd_is_200bp():
    assert np.allclose(shift(ScenarioName.PARALLEL_UP), 0.02)
    assert np.allclose(shift(ScenarioName.PARALLEL_DOWN), -0.02)


def test_short_and_long_shapes():
    short = shift(ScenarioName.SHORT_UP)
    assert short[0] == pytest.approx(0.03)
    assert short[2] == pytest.approx(0.03 * np.exp(-1))
    steep = shift(ScenarioName.STEEPENER)
    assert steep[0] == pytest.approx(-0.65 * 0.03)
    s_l = 0.015 * (1 - np.exp(-30 / 4))
    assert steep[3] == pytest.approx(-0.65 * 0.03 * np.exp(-30 / 4) + 0.9 * s_l)
    flat = shift(ScenarioName.FLATTENER)
    assert flat[0] == pytest.approx(0.8 * 0.03)


def test_unknown_currency_uses_default_size():
    assert np.allclose(shift(ScenarioName.PARALLEL_UP, "XXX"), 0.04)


def test_standard_set_and_scalars():
    scns = standard_scenarios()
    assert scns[0].name == "BASE"
    assert len(scns) == 7
    up = next(s for s in scns if s.name == "PARALLEL_UP")
    assert (up.prepayment_scalar, up.tdrr_scalar) == (0.8, 1.2)


def test_key_rate_bumps_sum_to_parallel():
    pillars = [0.5, 1, 2, 5, 10, 30]
    t = np.linspace(0, 40, 200)
    total = sum(key_rate_shock(pillars, i).shift_for("USD")(t) for i in range(len(pillars)))
    assert np.allclose(total, 1e-4)


def test_eba_floor():
    lb = lower_bound_fn(LowerBoundPolicy.EBA)
    assert lb(np.array([0.0, 10.0, 60.0])) == pytest.approx([-0.015, -0.012, 0.0])


def test_config_custom_scenarios_and_duplicates():
    cfg = RunConfig(
        as_of=AS_OF,
        custom_scenarios=[
            CustomScenarioConfig(name="UP_100", bp=100),
            CustomScenarioConfig(name="TWIST", kind="tenor", times=[0, 10], bp_points=[50, -50]),
        ],
    )
    names = [s.name for s in cfg.scenarios()]
    assert names[-2:] == ["UP_100", "TWIST"]
    with pytest.raises(ValueError):
        RunConfig(as_of=AS_OF, custom_scenarios=[CustomScenarioConfig(name="BASE")]).scenarios()
