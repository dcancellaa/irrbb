"""The :class:`Leg` — the universal building block of every product.

Every banking-book product is decomposed into one or more legs:

=================  ==========================================================
Product            Legs
=================  ==========================================================
Loan / mortgage    1 receive leg (fixed or float, amortising, prepayable)
Term deposit       1 pay leg (fixed, bullet, early-redeemable)
DDA / MMDA / SAV   Behavioural run-off legs (see :mod:`irrbb.behavioral.nmd`)
IRS                1 fixed + 1 float leg, no notional exchange
CCS                2 legs in different currencies with notional exchanges
FX forward / NDF   2 notional-only legs (``RateType.NONE``)
FX swap            2 notional-only legs with near and far exchanges
=================  ==========================================================

Legs do not price themselves directly. Instead they emit *skeletons*: plain
columnar records describing periods, balances and rate sources. The engine
stacks the skeletons of the whole portfolio into Polars frames and evaluates
every scenario in a vectorised way (projection, discounting, aggregation).

Two skeletons are produced:

* :func:`leg_skeleton` — remaining accrual periods with *scheduled* balances
  and behavioural inputs, plus exchange and floating-repricing records. The
  engine derives from it, per scenario and fully vectorised, both the full
  cash flows (MtM, full-revaluation EVE) and the repricing cash flows
  (standardised EVE, gap).
* :func:`nii_skeleton` — accrual segments within the NII horizon under a
  constant balance sheet. Used for NII and ΔNII.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from irrbb.core.dates import Period, add_months, build_schedule, year_fraction
from irrbb.core.enums import (
    Amortization,
    BehaviouralKind,
    DayCount,
    Direction,
    Frequency,
    ProductType,
    RateType,
)
from irrbb.curves.curve_set import CurveSet

Record = dict[str, Any]


@dataclass(frozen=True)
class Leg:
    """One leg of a position.

    Attributes:
        position_id: Identifier of the parent position / trade.
        leg_id: Identifier of the leg within the position.
        product_type: Product of the parent position.
        currency: Leg currency.
        direction: Receive (asset) or pay (liability) from the bank's view.
        notional: Outstanding notional / balance at the valuation date (> 0).
        start_date: Effective date of the leg.
        maturity_date: Final maturity of the leg.
        rate_type: Fixed, float, administered or none.
        fixed_rate: Fixed coupon or administered (client) rate, decimal.
        index: Projection curve name for floating legs.
        spread: Spread over the index for floating legs, decimal.
        payment_frequency: Coupon / reset frequency.
        day_count: Accrual day count.
        amortization: Principal profile.
        initial_exchange: Whether notional is exchanged at ``start_date``.
        final_exchange: Whether principal is exchanged at maturity / amortised.
        current_rate: All-in coupon of the period in progress (floating legs).
        behavioural_kind: Which BCBS scalar family scales ``runoff_rate``.
        runoff_rate: Annual behavioural run-off rate (CPR / TDRR), decimal.
        beta: Pass-through of market rate changes to the leg rate (NII).
        reprice_months: Repricing frequency for administered rates in NII.
        book: Book / portfolio label used for reporting.
        discount_curve: Explicit discount curve (``None`` = the currency's
            default), e.g. the NDF-implied curve for BRL NDF legs.
        settlement_currency: Cash-settlement currency of NDFs (USD or EUR).
        attributes: Free-form extra attributes carried into results.
    """

    position_id: str
    leg_id: str
    product_type: ProductType
    currency: str
    direction: Direction
    notional: float
    start_date: date
    maturity_date: date
    rate_type: RateType = RateType.FIXED
    fixed_rate: float = 0.0
    index: str | None = None
    spread: float = 0.0
    payment_frequency: Frequency = Frequency.ZERO
    day_count: DayCount = DayCount.ACT_360
    amortization: Amortization = Amortization.BULLET
    initial_exchange: bool = False
    final_exchange: bool = True
    current_rate: float | None = None
    behavioural_kind: BehaviouralKind = BehaviouralKind.NONE
    runoff_rate: float = 0.0
    beta: float = 1.0
    reprice_months: int = 1
    book: str = "BANKING"
    discount_curve: str | None = None
    settlement_currency: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.notional < 0:
            raise ValueError(f"{self.key}: notional must be non-negative; use direction for sign")
        if self.maturity_date <= self.start_date:
            raise ValueError(f"{self.key}: maturity must be after start date")

    @property
    def key(self) -> str:
        """str: ``position_id/leg_id`` identifier."""
        return f"{self.position_id}/{self.leg_id}"

    @property
    def sign(self) -> int:
        """int: ``+1`` for receive legs, ``-1`` for pay legs."""
        return self.direction.sign

    def effective_runoff(self, prepayment_scalar: float, tdrr_scalar: float) -> float:
        """Behavioural run-off rate after applying scenario scalars.

        Args:
            prepayment_scalar: Scenario prepayment scalar.
            tdrr_scalar: Scenario term-deposit redemption scalar.

        Returns:
            Scaled annual run-off rate, capped at 100%.
        """
        if self.behavioural_kind is BehaviouralKind.PREPAYMENT:
            return min(1.0, self.runoff_rate * prepayment_scalar)
        if self.behavioural_kind is BehaviouralKind.EARLY_REDEMPTION:
            return min(1.0, self.runoff_rate * tdrr_scalar)
        return min(1.0, self.runoff_rate)


# --------------------------------------------------------------- helpers
def _tau_y(as_of: date, d: date) -> float:
    return (d - as_of).days / 365.0


def _remaining_periods(leg: Leg, as_of: date, runoff: float) -> list[Period]:
    """Periods whose payment date is after ``as_of``.

    Behavioural legs (``runoff > 0``) with coarse frequencies are re-gridded
    monthly so that run-off is spread through time instead of being lumped at
    the (possibly single) contractual payment date.
    """
    freq = leg.payment_frequency
    if runoff > 0 and (freq is Frequency.ZERO or freq.months > 1):
        freq = Frequency.MONTHLY
    periods = build_schedule(leg.start_date, leg.maturity_date, freq)
    return [p for p in periods if p.end > as_of]


def _scheduled_balances(leg: Leg, periods: list[Period], coupon: float) -> np.ndarray:
    """End-of-period scheduled balances, before behavioural run-off."""
    n = len(periods)
    notional = leg.notional
    if n == 0:
        return np.zeros(0)
    if leg.amortization is Amortization.BULLET or n == 1:
        bal = np.full(n, notional)
        bal[-1] = 0.0
        return bal
    if leg.amortization is Amortization.LINEAR:
        return notional * (1.0 - np.arange(1, n + 1) / n)
    # Annuity: level payment at the average periodic rate.
    taus = np.array([year_fraction(p.start, p.end, leg.day_count) for p in periods])
    i = coupon * float(np.mean(taus))
    pmt = notional / n if abs(i) < 1e-12 else notional * i / (1.0 - (1.0 + i) ** (-n))
    out = np.empty(n)
    b = notional
    for k in range(n):
        b = max(0.0, b - (pmt - b * coupon * taus[k]))
        out[k] = b
    out[-1] = 0.0
    return out


def _leg_base(leg: Leg) -> Record:
    return {
        "position_id": leg.position_id,
        "leg_id": leg.leg_id,
        "product_type": leg.product_type.value,
        "book": leg.book,
        "currency": leg.currency,
        "sign": leg.sign,
        "rate_type": leg.rate_type.value,
        "index": leg.index,
        "discount_curve": leg.discount_curve,
    }


def _current_coupon(leg: Leg, as_of: date, period: Period, curves: CurveSet | None) -> float:
    """All-in coupon for the period in progress of a floating leg."""
    if leg.current_rate is not None:
        return leg.current_rate
    if curves is None:
        return leg.spread
    curve = curves.projection_curve(leg.index, leg.currency)
    fix_start = max(as_of, period.start)
    t1, t2 = _tau_y(as_of, fix_start), _tau_y(as_of, period.end)
    accrual = year_fraction(fix_start, period.end, leg.day_count)
    return float(curve.forward_rate(t1, t2, accrual)[0]) + leg.spread


# --------------------------------------------------------------- skeletons
@dataclass
class LegSkeleton:
    """Scenario-independent building blocks of one leg.

    Attributes:
        periods: One record per remaining accrual period, carrying the
            *scheduled* (contractual) end balance plus the leg's run-off rate
            and behavioural kind. Behavioural balances are applied later,
            vectorised, per scenario (see :mod:`irrbb.engine.frames`).
        exchanges: Initial notional exchange records (forward-starting legs).
        float_repricing: Repricing cash flows of floating legs (known coupon
            plus balance at the next reset), which are scenario-independent.
    """

    periods: list[Record] = field(default_factory=list)
    exchanges: list[Record] = field(default_factory=list)
    float_repricing: list[Record] = field(default_factory=list)


def leg_skeleton(leg: Leg, as_of: date, curves: CurveSet | None, leg_no: int = 0) -> LegSkeleton:
    """Build the scenario-independent skeleton of a leg.

    For each remaining period the record holds the accrual dates, the accrual
    fraction, the scheduled end balance, the rate source (``known_rate`` or
    ``projected`` for unfixed floating periods) and the leg-level behavioural
    inputs. Legs with a run-off rate are re-gridded monthly.

    Repricing view (BCBS 368 §4): fixed / administered / notional-only legs
    reprice on every flow and receive a synthetic principal when they have no
    final exchange; floating legs collapse to the known coupon of the current
    period plus the balance at the next reset (spread beyond the reset is
    excluded, i.e. commercial margin excluded).

    Args:
        leg: The leg.
        as_of: Valuation date.
        curves: Base curves, only used to fill a missing current fixing.
        leg_no: Unique integer key of the leg within the portfolio.

    Returns:
        The leg skeleton.
    """
    out = LegSkeleton()
    periods = _remaining_periods(leg, as_of, leg.runoff_rate)
    if not periods:
        return out
    base = {**_leg_base(leg), "leg_no": leg_no}
    is_float = leg.rate_type is RateType.FLOAT
    coupon = _current_coupon(leg, as_of, periods[0], curves) if is_float else leg.fixed_rate
    sched_end = _scheduled_balances(leg, periods, coupon)

    if leg.initial_exchange and leg.start_date > as_of:
        out.exchanges.append({**base, "pay_date": leg.start_date, "principal": -leg.notional})

    for k, p in enumerate(periods):
        projected = is_float and p.start > as_of
        if is_float:
            rate = 0.0 if projected else coupon
        elif leg.rate_type in (RateType.FIXED, RateType.ADMINISTERED):
            rate = leg.fixed_rate
        else:
            rate = 0.0
        tau = (
            0.0 if leg.rate_type is RateType.NONE else year_fraction(p.start, p.end, leg.day_count)
        )
        out.periods.append(
            {
                **base,
                "period_no": k,
                "pay_date": p.end,
                "accrual_start": p.start,
                "accrual_end": p.end,
                "tau": tau,
                "notional": leg.notional,
                "sched_end": float(sched_end[k]),
                "runoff_rate": leg.runoff_rate,
                "behaviour": leg.behavioural_kind.value,
                "final_exchange": leg.final_exchange,
                "known_rate": rate,
                "spread": leg.spread if projected else 0.0,
                "projected": projected,
            }
        )

    if is_float:
        p0 = periods[0]
        if p0.start > as_of:  # forward starting: reprices at start
            out.float_repricing.append(
                {**base, "pay_date": p0.start, "interest": 0.0, "principal": leg.notional}
            )
        else:
            interest = leg.notional * coupon * year_fraction(p0.start, p0.end, leg.day_count)
            out.float_repricing.append(
                {**base, "pay_date": p0.end, "interest": interest, "principal": leg.notional}
            )
    return out


def nii_skeleton(
    leg: Leg,
    as_of: date,
    horizon_end: date,
    curves: CurveSet,
) -> Iterator[Record]:
    """Yield NII accrual segments under a constant balance sheet.

    The outstanding balance is held constant over the horizon: maturing or
    amortising positions are replaced like-for-like. Each segment either has a
    ``known_rate`` (contractual, already fixed) or is a *repricing* segment
    whose base rate is ``forward_base + margin`` and whose scenario rate is
    ``base + beta × (forward_scenario - forward_base)``.

    For fixed legs maturing inside the horizon the margin is the leg's current
    margin over the base forward rate for its remaining life; for floating legs
    it is the contractual spread. Administered-rate legs (NMDs) keep their
    client rate in the base and pass through ``beta`` of the market move.

    Args:
        leg: The leg.
        as_of: Valuation date (horizon start).
        horizon_end: End of the NII horizon.
        curves: Base curves (to compute margins and missing fixings).

    Yields:
        Records with ``seg_start``, ``seg_end``, ``tau``, ``balance``,
        ``known_rate`` (``None`` for repricing segments), ``margin``, ``beta``,
        ``reprices`` plus leg identifiers.
    """
    if leg.rate_type is RateType.NONE or leg.notional == 0 or leg.maturity_date <= as_of:
        return
    start = max(as_of, leg.start_date)
    if start >= horizon_end:
        return
    base = _leg_base(leg)
    bal = leg.notional

    def seg(s: date, e: date, known: float | None, margin: float, beta: float) -> Record:
        return {
            **base,
            "seg_start": s,
            "seg_end": e,
            "tau": year_fraction(s, e, leg.day_count),
            "balance": bal,
            "known_rate": known,
            "margin": margin,
            "beta": beta,
            "reprices": known is None,
            "admin_rate": None,
        }

    if leg.rate_type is RateType.ADMINISTERED:
        cursor = start
        while cursor < horizon_end:
            nxt = min(add_months(cursor, max(1, leg.reprice_months)), horizon_end)
            # Repricing segment whose *base* rate is the client rate.
            r = seg(cursor, nxt, None, 0.0, leg.beta)
            r["admin_rate"] = leg.fixed_rate
            yield r
            cursor = nxt
        return

    curve = curves.projection_curve(leg.index, leg.currency)
    if leg.rate_type is RateType.FIXED:
        fixed_end = min(leg.maturity_date, horizon_end)
        yield seg(start, fixed_end, leg.fixed_rate, 0.0, 1.0)
        if leg.maturity_date < horizon_end:
            t0, t1 = _tau_y(as_of, start), _tau_y(as_of, leg.maturity_date)
            margin = leg.fixed_rate - float(curve.forward_rate(t0, max(t1, t0 + 1 / 365))[0])
            yield seg(leg.maturity_date, horizon_end, None, margin, leg.beta)
        return

    # Floating leg: current period known, then resets every period until horizon.
    months = leg.payment_frequency.months or 3
    periods = build_schedule(leg.start_date, leg.maturity_date, leg.payment_frequency)
    live = [p for p in periods if p.end > as_of]
    cursor = start
    if live and live[0].start <= as_of:
        p0 = live[0]
        end0 = min(p0.end, horizon_end)
        yield seg(cursor, end0, _current_coupon(leg, as_of, p0, curves), 0.0, 1.0)
        cursor = end0
    while cursor < horizon_end:
        nxt = min(add_months(cursor, months), horizon_end)
        if nxt <= cursor:
            nxt = cursor + timedelta(days=1)
        yield seg(cursor, nxt, None, leg.spread, leg.beta)
        cursor = nxt
