"""Interest rate shock scenarios (BCBS 368 standard and custom)."""

from irrbb.scenarios.shocks import (
    BCBS_SHOCK_SIZES,
    PREPAYMENT_SCALARS,
    TDRR_SCALARS,
    NoShock,
    ParallelShock,
    Scenario,
    Shock,
    ShockSize,
    StandardShock,
    TenorShock,
    base_scenario,
    key_rate_shock,
    lower_bound_fn,
    standard_scenarios,
    us_scenarios,
)

__all__ = [
    "BCBS_SHOCK_SIZES",
    "PREPAYMENT_SCALARS",
    "TDRR_SCALARS",
    "NoShock",
    "ParallelShock",
    "Scenario",
    "Shock",
    "ShockSize",
    "StandardShock",
    "TenorShock",
    "base_scenario",
    "key_rate_shock",
    "lower_bound_fn",
    "standard_scenarios",
    "us_scenarios",
]
