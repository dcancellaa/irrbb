"""Zero-coupon yield curve representation.

Curves are stored as continuously compounded zero rates on an ACT/365F time
axis measured from the curve's ``as_of`` date. Shocks are applied lazily as a
function of time, so a shocked curve is exact at *every* maturity, not only at
the pillars.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import numpy.typing as npt

from irrbb.core.dates import time_from
from irrbb.core.enums import Interpolation

FloatArray = npt.NDArray[np.float64]
ShiftFn = Callable[[FloatArray], FloatArray]
"""A function mapping times (years) to additive zero-rate shifts (decimal)."""


@dataclass(frozen=True)
class YieldCurve:
    """An immutable zero curve with optional lazily-applied shift.

    Attributes:
        name: Curve identifier, e.g. ``"USD-SOFR"``.
        currency: ISO currency code of the curve.
        as_of: Curve date (time zero).
        times: Pillar times in years, strictly increasing and positive.
        zero_rates: Continuously compounded zero rates at ``times`` (decimal).
        interpolation: Interpolation scheme between pillars.
        shift: Optional additive shift ``Δr(t)`` applied to the zero rate.
        lower_bound: Optional floor function ``lb(t)``. The shocked rate is
            ``max(r(t) + Δr(t), min(r(t), lb(t)))`` so a floor never pushes a
            rate that is already below it upwards (EBA convention).
    """

    name: str
    currency: str
    as_of: date
    times: FloatArray
    zero_rates: FloatArray
    interpolation: Interpolation = Interpolation.LINEAR_ZERO
    shift: ShiftFn | None = field(default=None, compare=False)
    lower_bound: ShiftFn | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        t = np.asarray(self.times, dtype=np.float64)
        z = np.asarray(self.zero_rates, dtype=np.float64)
        if t.ndim != 1 or t.shape != z.shape or t.size == 0:
            raise ValueError("times and zero_rates must be non-empty 1-D arrays of equal length")
        if np.any(np.diff(t) <= 0) or t[0] <= 0:
            raise ValueError(f"Curve {self.name}: pillar times must be positive and increasing")
        object.__setattr__(self, "times", t)
        object.__setattr__(self, "zero_rates", z)

    # ------------------------------------------------------------------ rates
    def base_zero_rate(self, t: npt.ArrayLike) -> FloatArray:
        """Interpolate the *unshifted* zero rate.

        Args:
            t: Times in years.

        Returns:
            Continuously compounded zero rates. Extrapolation is flat on both
            ends.
        """
        tt = np.atleast_1d(np.asarray(t, dtype=np.float64))
        if self.interpolation is Interpolation.LINEAR_ZERO:
            return np.interp(tt, self.times, self.zero_rates)
        # Log-linear discount factors == linear interpolation of r*t through (0, 0).
        rt_nodes = np.concatenate(([0.0], self.times * self.zero_rates))
        t_nodes = np.concatenate(([0.0], self.times))
        out = np.empty_like(tt)
        inside = tt <= self.times[-1]
        safe_t = np.where(tt > 0, tt, 1.0)
        rt = np.interp(tt, t_nodes, rt_nodes)
        out[inside] = np.where(tt[inside] > 0, rt[inside] / safe_t[inside], self.zero_rates[0])
        out[~inside] = self.zero_rates[-1]
        return out

    def zero_rate(self, t: npt.ArrayLike) -> FloatArray:
        """Zero rate including any shift and lower bound.

        Args:
            t: Times in years.

        Returns:
            Continuously compounded (possibly shocked) zero rates.
        """
        tt = np.atleast_1d(np.asarray(t, dtype=np.float64))
        base = self.base_zero_rate(tt)
        if self.shift is None:
            return base
        shocked = base + self.shift(tt)
        if self.lower_bound is not None:
            shocked = np.maximum(shocked, np.minimum(base, self.lower_bound(tt)))
        return shocked

    def discount_factor(self, t: npt.ArrayLike) -> FloatArray:
        """Discount factors ``exp(-r(t) t)``.

        Args:
            t: Times in years. Non-positive times return 1.

        Returns:
            Discount factors.
        """
        tt = np.atleast_1d(np.asarray(t, dtype=np.float64))
        pos = np.maximum(tt, 0.0)
        return np.exp(-self.zero_rate(pos) * pos)

    def discount_factor_dates(self, dates: Iterable[date]) -> FloatArray:
        """Discount factors for calendar dates.

        Args:
            dates: Payment dates.

        Returns:
            Discount factors from ``as_of`` to each date.
        """
        return self.discount_factor(time_from(self.as_of, dates))

    def forward_rate(
        self, t1: npt.ArrayLike, t2: npt.ArrayLike, accrual: npt.ArrayLike | None = None
    ) -> FloatArray:
        """Simply-compounded forward rate between two times.

        Args:
            t1: Period start times (years).
            t2: Period end times (years).
            accrual: Accrual fractions in the leg's day count. Defaults to
                ``t2 - t1`` (ACT/365F).

        Returns:
            Forward rates ``(DF(t1)/DF(t2) - 1) / accrual``.
        """
        a1 = np.atleast_1d(np.asarray(t1, dtype=np.float64))
        a2 = np.atleast_1d(np.asarray(t2, dtype=np.float64))
        tau = a2 - a1 if accrual is None else np.atleast_1d(np.asarray(accrual, dtype=np.float64))
        tau = np.where(np.abs(tau) < 1e-12, 1e-12, tau)
        return (self.discount_factor(a1) / self.discount_factor(a2) - 1.0) / tau

    # ----------------------------------------------------------- transforms
    def with_shift(self, shift: ShiftFn, lower_bound: ShiftFn | None = None) -> YieldCurve:
        """Return a new curve with an additional additive shift.

        Shifts compose: applying a shift to an already shifted curve adds them.

        Args:
            shift: Additive shift ``Δr(t)`` in decimal.
            lower_bound: Optional floor applied after the shift.

        Returns:
            A new, shifted curve sharing this curve's pillars.
        """
        existing = self.shift
        combined: ShiftFn = shift if existing is None else (lambda t: existing(t) + shift(t))
        return replace(
            self, shift=combined, lower_bound=lower_bound if lower_bound else self.lower_bound
        )

    def pillar_table(self) -> list[tuple[float, float]]:
        """Return ``(time, shocked zero rate)`` at each pillar.

        Returns:
            List of tuples, convenient for reporting.
        """
        return list(zip(self.times.tolist(), self.zero_rate(self.times).tolist(), strict=True))
