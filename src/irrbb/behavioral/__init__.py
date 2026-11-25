"""Behavioural models: non-maturity deposits, prepayment and early redemption.

Prepayment (CPR) and term-deposit early redemption (TDRR) are handled directly
on :class:`~irrbb.instruments.leg.Leg` via ``runoff_rate`` and scaled per
scenario by the BCBS 368 scalars (see :mod:`irrbb.scenarios.shocks`).
"""

from irrbb.behavioral.nmd import (
    BCBS_NMD_CAPS,
    DEFAULT_NMD_ASSUMPTIONS,
    DEFAULT_NMD_CATEGORY,
    NMDAssumption,
    NMDCap,
    NMDModel,
)

__all__ = [
    "BCBS_NMD_CAPS",
    "DEFAULT_NMD_ASSUMPTIONS",
    "DEFAULT_NMD_CATEGORY",
    "NMDAssumption",
    "NMDCap",
    "NMDModel",
]
