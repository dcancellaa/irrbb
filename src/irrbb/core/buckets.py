"""Regulatory time buckets.

Implements the 19 repricing time buckets of the BCBS 368 standardised framework
(Table 3), with the bucket mid-points used for discounting slotted cash flows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import polars as pl


@dataclass(frozen=True)
class TimeBucket:
    """A single time bucket.

    Attributes:
        index: Zero-based ordinal of the bucket.
        label: Human-readable label, e.g. ``"3M-6M"``.
        lower: Lower bound in years (exclusive, except for the first bucket).
        upper: Upper bound in years (inclusive). ``inf`` for the last bucket.
        midpoint: Representative time used for discounting, in years.
    """

    index: int
    label: str
    lower: float
    upper: float
    midpoint: float


#: BCBS 368 Table 3 buckets. Bounds are in years; one month = 1/12 year.
BCBS_BUCKETS: tuple[TimeBucket, ...] = tuple(
    TimeBucket(i, label, lo, hi, mid)
    for i, (label, lo, hi, mid) in enumerate(
        [
            ("ON", 0.0, 1 / 365, 0.0028),
            ("ON-1M", 1 / 365, 1 / 12, 0.0417),
            ("1M-3M", 1 / 12, 3 / 12, 0.1667),
            ("3M-6M", 3 / 12, 6 / 12, 0.375),
            ("6M-9M", 6 / 12, 9 / 12, 0.625),
            ("9M-1Y", 9 / 12, 1.0, 0.875),
            ("1Y-1.5Y", 1.0, 1.5, 1.25),
            ("1.5Y-2Y", 1.5, 2.0, 1.75),
            ("2Y-3Y", 2.0, 3.0, 2.5),
            ("3Y-4Y", 3.0, 4.0, 3.5),
            ("4Y-5Y", 4.0, 5.0, 4.5),
            ("5Y-6Y", 5.0, 6.0, 5.5),
            ("6Y-7Y", 6.0, 7.0, 6.5),
            ("7Y-8Y", 7.0, 8.0, 7.5),
            ("8Y-9Y", 8.0, 9.0, 8.5),
            ("9Y-10Y", 9.0, 10.0, 9.5),
            ("10Y-15Y", 10.0, 15.0, 12.5),
            ("15Y-20Y", 15.0, 20.0, 17.5),
            ("20Y+", 20.0, float("inf"), 25.0),
        ]
    )
)


def bucket_index(
    times: npt.ArrayLike, buckets: tuple[TimeBucket, ...] = BCBS_BUCKETS
) -> npt.NDArray[np.int64]:
    """Map times (years) to bucket indices.

    Times at or before zero map to the first bucket.

    Args:
        times: Times in years from the valuation date.
        buckets: Bucket definition to use.

    Returns:
        Array of bucket indices, same shape as ``times``.
    """
    uppers = np.array([b.upper for b in buckets[:-1]], dtype=np.float64)
    t = np.asarray(times, dtype=np.float64)
    return np.searchsorted(uppers, t, side="left").astype(np.int64)


def buckets_frame(buckets: tuple[TimeBucket, ...] = BCBS_BUCKETS) -> pl.DataFrame:
    """Return the bucket definition as a Polars frame.

    Args:
        buckets: Bucket definition to use.

    Returns:
        Frame with columns ``bucket``, ``bucket_label``, ``lower``, ``upper``, ``midpoint``.
    """
    return pl.DataFrame(
        {
            "bucket": [b.index for b in buckets],
            "bucket_label": [b.label for b in buckets],
            "lower": [b.lower for b in buckets],
            "upper": [b.upper for b in buckets],
            "midpoint": [b.midpoint for b in buckets],
        },
        schema={
            "bucket": pl.Int64,
            "bucket_label": pl.String,
            "lower": pl.Float64,
            "upper": pl.Float64,
            "midpoint": pl.Float64,
        },
    )
