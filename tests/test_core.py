import itertools
from datetime import date

import numpy as np
import pytest

from irrbb.core.buckets import BCBS_BUCKETS, bucket_index
from irrbb.core.dates import add_months, add_tenor, build_schedule, year_fraction
from irrbb.core.enums import DayCount, Frequency


def test_add_months_clamps_to_month_end():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 3, 31), -1) == date(2025, 2, 28)


@pytest.mark.parametrize(
    ("tenor", "expected"),
    [
        ("ON", date(2026, 1, 2)),
        ("1W", date(2026, 1, 8)),
        ("3M", date(2026, 4, 1)),
        ("10Y", date(2036, 1, 1)),
    ],
)
def test_add_tenor(tenor, expected):
    assert add_tenor(date(2026, 1, 1), tenor) == expected


def test_add_tenor_rejects_garbage():
    with pytest.raises(ValueError):
        add_tenor(date(2026, 1, 1), "3Q")


def test_year_fractions():
    s, e = date(2026, 1, 1), date(2027, 1, 1)
    assert year_fraction(s, e, DayCount.ACT_360) == pytest.approx(365 / 360)
    assert year_fraction(s, e, DayCount.ACT_365F) == pytest.approx(1.0)
    assert year_fraction(s, e, DayCount.THIRTY_360) == pytest.approx(1.0)
    assert year_fraction(date(2027, 7, 1), date(2028, 7, 1), DayCount.ACT_ACT) == pytest.approx(
        184 / 365 + 182 / 366
    )


def test_schedule_rolls_back_from_maturity_with_front_stub():
    periods = build_schedule(date(2026, 1, 15), date(2027, 1, 1), Frequency.QUARTERLY)
    assert periods[0].start == date(2026, 1, 15)
    assert periods[0].end == date(2026, 4, 1)  # short front stub
    assert [p.end for p in periods][-1] == date(2027, 1, 1)
    assert all(a.end == b.start for a, b in itertools.pairwise(periods))


def test_zero_frequency_single_period():
    assert len(build_schedule(date(2026, 1, 1), date(2030, 1, 1), Frequency.ZERO)) == 1


def test_bcbs_buckets():
    assert len(BCBS_BUCKETS) == 19
    idx = bucket_index(np.array([0.001, 0.05, 0.9, 1.0, 12.0, 40.0]))
    assert [BCBS_BUCKETS[i].label for i in idx] == [
        "ON",
        "ON-1M",
        "9M-1Y",
        "9M-1Y",
        "10Y-15Y",
        "20Y+",
    ]
