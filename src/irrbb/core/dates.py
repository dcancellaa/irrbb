"""Date arithmetic: tenors, day count fractions and payment schedules.

The engine deliberately keeps calendar logic simple (weekend-only adjustment,
modified following). Holiday calendars can be plugged in via
:class:`HolidayCalendar`.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise

import numpy as np
import numpy.typing as npt

from irrbb.core.enums import DayCount, Frequency

_TENOR_RE = re.compile(r"^\s*(\d+)\s*([DWMY])\s*$", re.IGNORECASE)


def add_months(d: date, months: int) -> date:
    """Add a number of calendar months to a date, clamping to month end.

    Args:
        d: Start date.
        months: Months to add (may be negative).

    Returns:
        The shifted date. If the target month is shorter than ``d.day`` the
        last day of that month is used.

    Examples:
        >>> add_months(date(2024, 1, 31), 1)
        datetime.date(2024, 2, 29)
    """
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def add_tenor(d: date, tenor: str) -> date:
    """Shift a date by a tenor string such as ``"ON"``, ``"1W"``, ``"3M"`` or ``"10Y"``.

    Args:
        d: Start date.
        tenor: Tenor code. ``"ON"``/``"O/N"`` mean one day.

    Returns:
        The shifted (unadjusted) date.

    Raises:
        ValueError: If the tenor cannot be parsed.
    """
    t = tenor.strip().upper()
    if t in {"ON", "O/N", "TN", "SN"}:
        return d + timedelta(days=1)
    m = _TENOR_RE.match(t)
    if not m:
        raise ValueError(f"Unrecognised tenor: {tenor!r}")
    n, unit = int(m.group(1)), m.group(2).upper()
    if unit == "D":
        return d + timedelta(days=n)
    if unit == "W":
        return d + timedelta(weeks=n)
    if unit == "M":
        return add_months(d, n)
    return add_months(d, 12 * n)


def year_fraction(start: date, end: date, day_count: DayCount) -> float:
    """Compute the accrual year fraction between two dates.

    Args:
        start: Accrual start date.
        end: Accrual end date.
        day_count: Day count convention.

    Returns:
        The year fraction (negative if ``end < start``).
    """
    if day_count is DayCount.ACT_360:
        return (end - start).days / 360.0
    if day_count is DayCount.ACT_365F:
        return (end - start).days / 365.0
    if day_count is DayCount.THIRTY_360:
        d1 = min(start.day, 30)
        d2 = 30 if (end.day == 31 and d1 == 30) else end.day
        return (360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)) / 360.0
    # ACT/ACT ISDA
    if end < start:
        return -year_fraction(end, start, day_count)
    total = 0.0
    cursor = start
    while cursor < end:
        year_end = date(cursor.year + 1, 1, 1)
        stop = min(year_end, end)
        days_in_year = 366 if calendar.isleap(cursor.year) else 365
        total += (stop - cursor).days / days_in_year
        cursor = stop
    return total


def time_from(as_of: date, dates: Iterable[date]) -> npt.NDArray[np.float64]:
    """Convert dates to ACT/365F times (in years) measured from ``as_of``.

    This is the time axis used by every curve in the engine.

    Args:
        as_of: Valuation date (time zero).
        dates: Dates to convert.

    Returns:
        Array of year fractions.
    """
    return np.array([(d - as_of).days / 365.0 for d in dates], dtype=np.float64)


@dataclass(frozen=True)
class HolidayCalendar:
    """A simple business-day calendar.

    Attributes:
        name: Calendar identifier, e.g. ``"USNY"``.
        holidays: Explicit non-business dates (weekends are always holidays).
    """

    name: str = "WEEKEND"
    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_business_day(self, d: date) -> bool:
        """Check whether a date is a business day.

        Args:
            d: Date to test.

        Returns:
            ``True`` when ``d`` is neither a weekend nor a listed holiday.
        """
        return d.weekday() < 5 and d not in self.holidays

    def modified_following(self, d: date) -> date:
        """Adjust a date with the modified-following convention.

        Args:
            d: Unadjusted date.

        Returns:
            The next business day, unless that falls in the next month, in which
            case the previous business day.
        """
        adj = d
        while not self.is_business_day(adj):
            adj += timedelta(days=1)
        if adj.month != d.month:
            adj = d
            while not self.is_business_day(adj):
                adj -= timedelta(days=1)
        return adj


@dataclass(frozen=True)
class Period:
    """One accrual period of a schedule.

    Attributes:
        start: Accrual start date.
        end: Accrual end date (also the payment date).
    """

    start: date
    end: date


def build_schedule(
    start: date,
    end: date,
    frequency: Frequency,
    calendar_: HolidayCalendar | None = None,
) -> list[Period]:
    """Generate an accrual schedule rolling backwards from maturity.

    Rolling backwards keeps regular periods aligned to maturity and places any
    stub at the front, which is the market convention for loans and swaps.

    Args:
        start: Effective / start date of the leg.
        end: Maturity date of the leg.
        frequency: Payment frequency. :attr:`Frequency.ZERO` yields one period.
        calendar_: Optional calendar used to adjust period end dates.

    Returns:
        Ordered list of periods covering ``[start, end]``.

    Raises:
        ValueError: If ``end`` is not after ``start``.
    """
    if end <= start:
        raise ValueError(f"Schedule end {end} must be after start {start}")
    if frequency is Frequency.ZERO:
        return [Period(start, end)]
    dates: list[date] = [end]
    k = 1
    while True:
        d = add_months(end, -frequency.months * k)
        if d <= start:
            break
        dates.append(d)
        k += 1
    dates.append(start)
    dates.reverse()
    if calendar_ is not None:
        dates = [dates[0], *[calendar_.modified_following(d) for d in dates[1:]]]
    return [Period(a, b) for a, b in pairwise(dates)]


def first_on_or_after(dates: Sequence[date], target: date) -> int:
    """Return the index of the first date that is on or after ``target``.

    Args:
        dates: Sorted dates.
        target: Date to search for.

    Returns:
        Index into ``dates``; ``len(dates)`` if every date is before ``target``.
    """
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return len(dates)
