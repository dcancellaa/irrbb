"""Bootstrapping zero curves from raw market quotes.

Raw curves are sourced externally (market data vendor, treasury system). This
module turns them into :class:`~irrbb.curves.yield_curve.YieldCurve` objects.

Supported quotes (see :class:`~irrbb.core.enums.QuoteType`):

* ``ZERO_CC`` / ``ZERO_ANNUAL`` / ``DISCOUNT_FACTOR`` are converted directly.
* ``DEPOSIT`` quotes are simple-interest rates: ``DF = 1 / (1 + r τ)``.
* ``FX_SPOT`` + ``FX_OUTRIGHT`` / ``FX_POINTS`` quotes build an FX-implied
  curve against a ``base_curve`` (see :func:`implied_curve`).
* ``OIS_SWAP`` quotes are par rates of annual-fixed vs compounded-overnight swaps.
  Under single-curve OIS discounting the floating leg is worth ``1 - DF(T)``, so
  the par condition is ``S Σ τ_i DF(t_i) = 1 - DF(T)``, solved pillar by pillar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy.optimize import brentq

from irrbb.core.dates import add_tenor, build_schedule, year_fraction
from irrbb.core.enums import DayCount, Frequency, Interpolation, QuoteType
from irrbb.curves.yield_curve import YieldCurve


@dataclass(frozen=True)
class CurveQuote:
    """One raw market quote.

    Attributes:
        tenor: Tenor code (``"ON"``, ``"1M"``, ``"10Y"``...). Ignored when
            ``maturity`` is given.
        quote_type: How ``value`` is to be interpreted.
        value: Quote value in decimal (``0.045`` for 4.5%) or a discount factor.
        maturity: Optional explicit maturity date.
    """

    tenor: str
    quote_type: QuoteType
    value: float
    maturity: date | None = None

    def maturity_from(self, as_of: date) -> date:
        """Resolve the quote's maturity date.

        Args:
            as_of: Curve date.

        Returns:
            The explicit maturity, or ``as_of`` shifted by ``tenor``.
        """
        return self.maturity or add_tenor(as_of, self.tenor)


def bootstrap_curve(
    name: str,
    currency: str,
    as_of: date,
    quotes: Sequence[CurveQuote],
    day_count: DayCount = DayCount.ACT_360,
    interpolation: Interpolation = Interpolation.LINEAR_ZERO,
) -> YieldCurve:
    """Build a zero curve from raw quotes.

    Args:
        name: Curve identifier.
        currency: Curve currency.
        as_of: Curve date.
        quotes: Raw quotes in any order; they are sorted by maturity.
        day_count: Money-market day count used for deposit/OIS accruals.
        interpolation: Interpolation used during and after bootstrapping.

    Returns:
        The bootstrapped curve.

    Raises:
        ValueError: If no quotes are supplied or two quotes share a maturity.
    """
    if not quotes:
        raise ValueError(f"No quotes supplied for curve {name}")
    resolved = sorted(((q.maturity_from(as_of), q) for q in quotes), key=lambda x: x[0])
    mats = [m for m, _ in resolved]
    if len(set(mats)) != len(mats):
        raise ValueError(f"Curve {name}: duplicate pillar maturities")

    times: list[float] = []
    zeros: list[float] = []

    def curve_with(extra_t: float | None = None, extra_z: float | None = None) -> YieldCurve:
        t = [*times, extra_t] if extra_t is not None else times
        z = [*zeros, extra_z] if extra_z is not None else zeros
        return YieldCurve(name, currency, as_of, np.array(t), np.array(z), interpolation)

    for mat, q in resolved:
        t = (mat - as_of).days / 365.0
        if t <= 0:
            raise ValueError(f"Curve {name}: quote {q.tenor} matures on or before as_of")
        v = q.value
        qt = q.quote_type
        if qt is QuoteType.ZERO_CC:
            z = v
        elif qt is QuoteType.ZERO_ANNUAL:
            z = float(np.log1p(v))
        elif qt is QuoteType.DISCOUNT_FACTOR:
            z = -float(np.log(v)) / t
        elif qt is QuoteType.DEPOSIT or (qt is QuoteType.OIS_SWAP and t <= 1.0 + 1e-9):
            tau = year_fraction(as_of, mat, day_count)
            z = float(np.log1p(v * tau)) / t
        else:  # multi-period OIS swap
            periods = build_schedule(as_of, mat, Frequency.ANNUAL)
            taus = np.array([year_fraction(p.start, p.end, day_count) for p in periods])
            pay_t = np.array([(p.end - as_of).days / 365.0 for p in periods])

            def residual(
                z_try: float,
                taus: np.ndarray = taus,
                pay_t: np.ndarray = pay_t,
                t: float = t,
                v: float = v,
            ) -> float:
                c = curve_with(t, z_try)
                dfs = c.discount_factor(pay_t)
                return float(v * np.sum(taus * dfs) - (1.0 - dfs[-1]))

            try:
                z = float(brentq(residual, -0.5, 1.0, xtol=1e-14))
            except ValueError as exc:
                raise ValueError(
                    f"Curve {name}: cannot bootstrap {q.tenor} OIS quote {v!r} "
                    "(is it in percent instead of decimal?)"
                ) from exc
        times.append(t)
        zeros.append(z)

    return curve_with()


def implied_curve(
    name: str,
    currency: str,
    as_of: date,
    spot: float,
    forwards: Sequence[tuple[date, float]],
    base: YieldCurve,
    interpolation: Interpolation = Interpolation.LINEAR_ZERO,
) -> YieldCurve:
    """Build an FX-implied (e.g. offshore NDF-implied) curve by covered parity.

    With ``S`` and ``F(T)`` quoted as curve-currency units per one unit of the
    base currency (``USDBRL`` = BRL per USD):

    ``DF_ccy(T) = DF_base(T) · S / F(T)``

    The spot settlement lag is ignored (spot is treated as of ``as_of``).

    Args:
        name: Implied curve name, e.g. ``"BRL-NDF-IMPLIED"``.
        currency: Implied currency.
        as_of: Curve date.
        spot: FX spot.
        forwards: ``(maturity, outright)`` pairs.
        base: Discount curve of the base currency (e.g. USD SOFR).
        interpolation: Interpolation scheme.

    Returns:
        The implied zero curve.

    Raises:
        ValueError: If inputs are empty, non-positive or produce duplicate pillars.
    """
    if not forwards or spot <= 0:
        raise ValueError(f"Curve {name}: implied curve needs a positive spot and forwards")
    pts = sorted(forwards)
    if len({m for m, _ in pts}) != len(pts):
        raise ValueError(f"Curve {name}: duplicate pillar maturities")
    times, zeros = [], []
    for mat, fwd in pts:
        t = (mat - as_of).days / 365.0
        if t <= 0 or fwd <= 0:
            raise ValueError(f"Curve {name}: invalid forward {fwd} for {mat}")
        df = float(base.discount_factor(t)[0]) * spot / fwd
        times.append(t)
        zeros.append(-float(np.log(df)) / t)
    return YieldCurve(name, currency, as_of, np.array(times), np.array(zeros), interpolation)
