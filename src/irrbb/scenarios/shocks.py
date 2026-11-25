"""Interest rate shock scenarios.

Implements the BCBS 368 (April 2016) standardised interest rate shock
scenarios, plus user-defined parallel, tenor-based and key-rate shocks.

Standard shock shapes (``t`` in years, ``x = 4``):

* parallel:   ``ΔR(t) = ±R_parallel``
* short rate: ``ΔR(t) = ±R_short · exp(-t/x)``
* long rate:  ``ΔR(t) = ±R_long · (1 - exp(-t/x))``
* steepener:  ``ΔR(t) = -0.65 |ΔR_short(t)| + 0.9 |ΔR_long(t)|``
* flattener:  ``ΔR(t) = +0.8 |ΔR_short(t)| - 0.6 |ΔR_long(t)|``
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from irrbb.core.enums import LowerBoundPolicy, ScenarioName
from irrbb.curves.curve_set import CurveSet
from irrbb.curves.yield_curve import FloatArray, ShiftFn, YieldCurve

DECAY_X: float = 4.0
"""Decay parameter ``x`` of the short / long shock shapes (BCBS 368 Annex 2)."""


@dataclass(frozen=True)
class ShockSize:
    """Currency-specific shock magnitudes (decimal, e.g. ``0.02`` = 200bp).

    Attributes:
        parallel: Parallel shock size.
        short: Short-rate shock size.
        long: Long-rate shock size.
    """

    parallel: float
    short: float
    long: float


def _bp(par: int, short: int, long: int) -> ShockSize:
    return ShockSize(par / 1e4, short / 1e4, long / 1e4)


#: BCBS 368 Annex 2, Table 2: specified size of interest rate shocks (bp).
BCBS_SHOCK_SIZES: dict[str, ShockSize] = {
    "ARS": _bp(400, 500, 300),
    "AUD": _bp(300, 450, 200),
    "BRL": _bp(400, 500, 300),
    "CAD": _bp(200, 300, 150),
    "CHF": _bp(100, 150, 100),
    "CNY": _bp(250, 300, 150),
    "EUR": _bp(200, 250, 100),
    "GBP": _bp(250, 300, 150),
    "HKD": _bp(200, 250, 100),
    "IDR": _bp(400, 500, 350),
    "INR": _bp(400, 500, 300),
    "JPY": _bp(100, 100, 100),
    "KRW": _bp(300, 400, 200),
    "MXN": _bp(400, 500, 300),
    "RUB": _bp(400, 500, 300),
    "SAR": _bp(200, 300, 150),
    "SEK": _bp(200, 300, 150),
    "SGD": _bp(150, 200, 100),
    "TRY": _bp(400, 500, 300),
    "USD": _bp(200, 300, 150),
    "ZAR": _bp(400, 500, 300),
}

#: Fallback used for currencies not listed in :data:`BCBS_SHOCK_SIZES`.
#: Supervisors expect banks to calibrate these; 400/500/300 is a conservative default.
DEFAULT_SHOCK_SIZE: ShockSize = _bp(400, 500, 300)

#: BCBS 368 Table 7 — loan prepayment scalars per scenario.
PREPAYMENT_SCALARS: dict[ScenarioName, float] = {
    ScenarioName.BASE: 1.0,
    ScenarioName.PARALLEL_UP: 0.8,
    ScenarioName.PARALLEL_DOWN: 1.2,
    ScenarioName.STEEPENER: 0.8,
    ScenarioName.FLATTENER: 1.2,
    ScenarioName.SHORT_UP: 0.8,
    ScenarioName.SHORT_DOWN: 1.2,
}

#: BCBS 368 Table 8 — term deposit early redemption (TDRR) scalars per scenario.
TDRR_SCALARS: dict[ScenarioName, float] = {
    ScenarioName.BASE: 1.0,
    ScenarioName.PARALLEL_UP: 1.2,
    ScenarioName.PARALLEL_DOWN: 0.8,
    ScenarioName.STEEPENER: 0.8,
    ScenarioName.FLATTENER: 1.2,
    ScenarioName.SHORT_UP: 1.2,
    ScenarioName.SHORT_DOWN: 0.8,
}


# ------------------------------------------------------------------ shocks
class Shock(ABC):
    """Abstract shock: produces an additive zero-rate shift per currency."""

    @abstractmethod
    def shift_for(self, currency: str) -> ShiftFn | None:
        """Return the shift function for a currency.

        Args:
            currency: ISO currency code.

        Returns:
            The shift ``Δr(t)`` or ``None`` when the currency is not shocked.
        """


@dataclass(frozen=True)
class NoShock(Shock):
    """The base (unshocked) scenario."""

    def shift_for(self, currency: str) -> ShiftFn | None:
        """Return ``None``: nothing is shocked.

        Args:
            currency: Ignored.

        Returns:
            Always ``None``.
        """
        return None


@dataclass(frozen=True)
class StandardShock(Shock):
    """One of the six BCBS 368 standard shocks.

    Attributes:
        kind: Which standard scenario.
        sizes: Currency-specific shock sizes (defaults to BCBS Table 2).
        default_size: Size used for currencies missing from ``sizes``.
    """

    kind: ScenarioName
    sizes: Mapping[str, ShockSize] = field(default_factory=lambda: dict(BCBS_SHOCK_SIZES))
    default_size: ShockSize = DEFAULT_SHOCK_SIZE

    def shift_for(self, currency: str) -> ShiftFn | None:
        """Build the standard shock shape for a currency.

        Args:
            currency: ISO currency code.

        Returns:
            The shift function.
        """
        s = self.sizes.get(currency, self.default_size)
        kind = self.kind

        def short(t: FloatArray) -> FloatArray:
            return s.short * np.exp(-t / DECAY_X)

        def long(t: FloatArray) -> FloatArray:
            return s.long * (1.0 - np.exp(-t / DECAY_X))

        def fn(t: FloatArray) -> FloatArray:
            if kind is ScenarioName.PARALLEL_UP:
                return np.full_like(t, s.parallel)
            if kind is ScenarioName.PARALLEL_DOWN:
                return np.full_like(t, -s.parallel)
            if kind is ScenarioName.SHORT_UP:
                return short(t)
            if kind is ScenarioName.SHORT_DOWN:
                return -short(t)
            if kind is ScenarioName.STEEPENER:
                return -0.65 * short(t) + 0.9 * long(t)
            if kind is ScenarioName.FLATTENER:
                return 0.8 * short(t) - 0.6 * long(t)
            return np.zeros_like(t)

        return fn


@dataclass(frozen=True)
class ParallelShock(Shock):
    """A parallel shift, optionally currency specific.

    Attributes:
        bp: Shift in basis points applied to every currency.
        bp_by_currency: Optional per-currency overrides in basis points.
    """

    bp: float
    bp_by_currency: Mapping[str, float] = field(default_factory=dict)

    def shift_for(self, currency: str) -> ShiftFn | None:
        """Return a constant shift.

        Args:
            currency: ISO currency code.

        Returns:
            The shift function.
        """
        size = self.bp_by_currency.get(currency, self.bp) / 1e4
        return lambda t: np.full_like(t, size)


@dataclass(frozen=True)
class TenorShock(Shock):
    """A shock defined at tenor points and linearly interpolated (flat beyond).

    Useful for historical or management scenarios (e.g. a twist defined by
    ALCO) and for key-rate sensitivities.

    Attributes:
        times: Tenor points in years, increasing.
        bp: Shift in basis points at each tenor.
        currencies: Currencies the shock applies to (``None`` = all).
    """

    times: Sequence[float]
    bp: Sequence[float]
    currencies: frozenset[str] | None = None

    def shift_for(self, currency: str) -> ShiftFn | None:
        """Return the interpolated tenor shift.

        Args:
            currency: ISO currency code.

        Returns:
            The shift function, or ``None`` if the currency is excluded.
        """
        if self.currencies is not None and currency not in self.currencies:
            return None
        xs = np.asarray(self.times, dtype=np.float64)
        ys = np.asarray(self.bp, dtype=np.float64) / 1e4
        return lambda t: np.interp(t, xs, ys)


def key_rate_shock(
    pillars: Sequence[float], i: int, bp: float = 1.0, currency: str | None = None
) -> TenorShock:
    """Triangular key-rate bump at pillar ``i``.

    The bump is ``bp`` at ``pillars[i]`` and decays linearly to zero at the
    neighbouring pillars; summing all key-rate bumps yields a parallel shift.

    Args:
        pillars: Key-rate tenors in years, increasing.
        i: Index of the bumped pillar.
        bp: Bump size in basis points.
        currency: Restrict the bump to one currency (``None`` = all).

    Returns:
        A :class:`TenorShock` implementing the bump.
    """
    vals = [0.0] * len(pillars)
    vals[i] = bp
    times = list(pillars)
    # Make the first/last bump flat-extrapolated so the bumps sum to parallel.
    return TenorShock(times, vals, frozenset({currency}) if currency else None)


# --------------------------------------------------------------- scenario
def lower_bound_fn(policy: LowerBoundPolicy) -> ShiftFn | None:
    """Return the post-shock floor function for a policy.

    Args:
        policy: Lower-bound policy.

    Returns:
        The floor ``lb(t)`` or ``None`` when no floor applies.
    """
    if policy is LowerBoundPolicy.ZERO:
        return lambda t: np.zeros_like(t)
    if policy is LowerBoundPolicy.EBA:
        return lambda t: np.minimum(0.0, -0.015 + 0.0003 * t)
    return None


@dataclass(frozen=True)
class Scenario:
    """A complete scenario: curve shock plus behavioural scalars.

    Attributes:
        name: Unique scenario name.
        shock: Curve shock.
        prepayment_scalar: Multiplier on loan prepayment rates (CPR).
        tdrr_scalar: Multiplier on term-deposit early redemption rates.
        nmd_beta_scalar: Multiplier on NMD pass-through betas (management overlay).
        lower_bound: Post-shock lower bound policy.
        description: Free text.
        regulatory: Whether the scenario is one of the supervisory scenarios
            used for the outlier test.
        family: Scenario family used for reporting and limit selection
            (``BCBS``, ``US_PARALLEL``, ``US_RAMP``, ``CUSTOM``).
        ramp_months: When set, the shock is phased in linearly over this many
            months (a *gradual* or *ramp* scenario). Ramps only make sense for
            earnings, so they apply to NII only by default.
        measures: Measures the scenario applies to (``EVE`` and/or ``NII``).
        curves: Restrict the shock to these curve names (basis-risk
            scenarios); ``None`` shocks every curve of a shocked currency.
    """

    name: str
    shock: Shock = field(default_factory=NoShock)
    prepayment_scalar: float = 1.0
    tdrr_scalar: float = 1.0
    nmd_beta_scalar: float = 1.0
    lower_bound: LowerBoundPolicy = LowerBoundPolicy.NONE
    description: str = ""
    regulatory: bool = False
    family: str = "CUSTOM"
    ramp_months: int | None = None
    measures: frozenset[str] = frozenset({"EVE", "NII"})
    curves: frozenset[str] | None = None

    @property
    def for_eve(self) -> bool:
        """bool: Whether the scenario is evaluated for EVE / MtM."""
        return "EVE" in self.measures

    @property
    def for_nii(self) -> bool:
        """bool: Whether the scenario is evaluated for NII."""
        return "NII" in self.measures

    def ramp_weight(self, t: FloatArray) -> FloatArray:
        """Share of the shock in force at calendar time ``t`` (years).

        Args:
            t: Calendar times in years from the valuation date.

        Returns:
            ``min(1, 12 t / ramp_months)`` for ramps, otherwise ones.
        """
        if not self.ramp_months:
            return np.ones_like(t)
        return np.clip(np.asarray(t) * 12.0 / self.ramp_months, 0.0, 1.0)

    @property
    def is_base(self) -> bool:
        """bool: Whether this is the unshocked base scenario."""
        return isinstance(self.shock, NoShock)

    def apply_curve(self, curve: YieldCurve) -> YieldCurve:
        """Shock a single curve.

        Args:
            curve: Base curve.

        Returns:
            The shocked curve (the same object if unshocked).
        """
        if self.curves is not None and curve.name not in self.curves:
            return curve
        shift = self.shock.shift_for(curve.currency)
        if shift is None:
            return curve
        return curve.with_shift(shift, lower_bound_fn(self.lower_bound))

    def apply(self, curves: CurveSet) -> CurveSet:
        """Shock every curve of a curve set.

        Args:
            curves: Base curve set.

        Returns:
            The shocked curve set.
        """
        if self.is_base:
            return curves
        return curves.transformed(self.apply_curve)


def base_scenario() -> Scenario:
    """Return the unshocked base scenario.

    Returns:
        Scenario named ``"BASE"``.
    """
    return Scenario(ScenarioName.BASE.value, NoShock(), description="Unshocked base curves")


def standard_scenarios(
    sizes: Mapping[str, ShockSize] | None = None,
    lower_bound: LowerBoundPolicy = LowerBoundPolicy.NONE,
    include_base: bool = True,
    apply_behavioural_scalars: bool = True,
) -> list[Scenario]:
    """Build the BCBS 368 scenario set.

    Args:
        sizes: Currency shock sizes; defaults to BCBS Table 2.
        lower_bound: Post-shock lower bound policy.
        include_base: Prepend the base scenario.
        apply_behavioural_scalars: Apply BCBS prepayment / TDRR scalars.

    Returns:
        Scenarios in a stable order: base, parallel up/down, steepener,
        flattener, short up/down.
    """
    sz = dict(sizes) if sizes is not None else dict(BCBS_SHOCK_SIZES)
    out: list[Scenario] = [base_scenario()] if include_base else []
    for kind in list(ScenarioName)[1:]:
        out.append(
            Scenario(
                name=kind.value,
                shock=StandardShock(kind, sz),
                prepayment_scalar=PREPAYMENT_SCALARS[kind] if apply_behavioural_scalars else 1.0,
                tdrr_scalar=TDRR_SCALARS[kind] if apply_behavioural_scalars else 1.0,
                lower_bound=lower_bound,
                description=f"BCBS 368 {kind.value.replace('_', ' ').lower()} shock",
                regulatory=True,
                family="BCBS",
            )
        )
    return out


def us_scenarios(
    parallel_bp: Sequence[float] = (100, 200, 300, 400),
    ramp_bp: Sequence[float] = (100, 200, 300, 400),
    ramp_months: int = 12,
    lower_bound: LowerBoundPolicy = LowerBoundPolicy.ZERO,
    apply_behavioural_scalars: bool = True,
) -> list[Scenario]:
    """US supervisory-style management scenarios (no base scenario).

    US agencies (2010 Interagency Advisory on IRR Management, OCC *Interest
    Rate Risk* handbook, Fed SR 10-1) are principles based: banks are expected
    to run instantaneous parallel shocks of at least ±200bp (commonly ±100 to
    ±400bp) for EVE and NII, and gradual (ramp) scenarios for earnings.

    Args:
        parallel_bp: Sizes of the instantaneous parallel shocks (up and down).
        ramp_bp: Sizes of the ramp shocks (up and down), NII only.
        ramp_months: Months over which ramps are phased in.
        lower_bound: Post-shock floor (zero is customary in the US).
        apply_behavioural_scalars: Use the BCBS parallel up/down prepayment and
            TDRR scalars for up/down shocks.

    Returns:
        Scenarios named ``US_PARALLEL_UP_200``, ``US_RAMP_DOWN_100_12M``, etc.
    """
    out: list[Scenario] = []
    up_k, dn_k = ScenarioName.PARALLEL_UP, ScenarioName.PARALLEL_DOWN

    def scalars(up: bool) -> tuple[float, float]:
        if not apply_behavioural_scalars:
            return 1.0, 1.0
        k = up_k if up else dn_k
        return PREPAYMENT_SCALARS[k], TDRR_SCALARS[k]

    for bp in parallel_bp:
        for up in (True, False):
            pre, td = scalars(up)
            d = "UP" if up else "DOWN"
            out.append(
                Scenario(
                    name=f"US_PARALLEL_{d}_{bp:g}",
                    shock=ParallelShock(bp if up else -bp),
                    prepayment_scalar=pre,
                    tdrr_scalar=td,
                    lower_bound=lower_bound,
                    description=f"Instantaneous parallel {'+' if up else '-'}{bp:g}bp",
                    family="US_PARALLEL",
                )
            )
    for bp in ramp_bp:
        for up in (True, False):
            pre, td = scalars(up)
            d = "UP" if up else "DOWN"
            out.append(
                Scenario(
                    name=f"US_RAMP_{d}_{bp:g}_{ramp_months}M",
                    shock=ParallelShock(bp if up else -bp),
                    prepayment_scalar=pre,
                    tdrr_scalar=td,
                    lower_bound=lower_bound,
                    description=f"Gradual {'+' if up else '-'}{bp:g}bp over {ramp_months} months",
                    family="US_RAMP",
                    ramp_months=ramp_months,
                    measures=frozenset({"NII"}),
                )
            )
    return out
