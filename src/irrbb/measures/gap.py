"""Repricing gap report.

Slots signed repricing *principal* (plus optionally interest) into the BCBS
time buckets per currency: positive amounts are rate-sensitive assets (or
receive legs), negative amounts rate-sensitive liabilities (or pay legs).
"""

from __future__ import annotations

from datetime import date

import polars as pl

from irrbb.core.buckets import BCBS_BUCKETS, bucket_index, buckets_frame
from irrbb.curves.curve_set import FxRates
from irrbb.engine.frames import years_from
from irrbb.measures.eve import fx_column


def repricing_gap(
    repricing: pl.DataFrame, as_of: date, fx: FxRates, include_interest: bool = False
) -> pl.DataFrame:
    """Build the repricing gap by currency and bucket.

    Args:
        repricing: Signed base-scenario repricing flows.
        as_of: Valuation date.
        fx: Spot FX.
        include_interest: Add interest flows to the gap amounts.

    Returns:
        Frame ``currency, bucket, bucket_label, assets, liabilities, gap,
        cumulative_gap, gap_rep, cumulative_gap_rep``.
    """
    amount = pl.col("principal") + (pl.col("interest") if include_interest else pl.lit(0.0))
    df = repricing.with_columns(years_from("pay_date", as_of).alias("t"), amount.alias("amt"))
    df = df.with_columns(
        pl.Series("bucket", bucket_index(df.get_column("t").to_numpy(), BCBS_BUCKETS))
    )
    agg = df.group_by(["currency", "bucket"]).agg(
        pl.col("amt").filter(pl.col("amt") > 0).sum().alias("assets"),
        pl.col("amt").filter(pl.col("amt") < 0).sum().alias("liabilities"),
        pl.col("amt").sum().alias("gap"),
    )
    grid = pl.DataFrame({"currency": agg.get_column("currency").unique()}).join(
        buckets_frame().select(["bucket", "bucket_label"]), how="cross"
    )
    out = (
        grid.join(agg, on=["currency", "bucket"], how="left")
        .with_columns(pl.col(["assets", "liabilities", "gap"]).fill_null(0.0))
        .sort(["currency", "bucket"])
    )
    return out.with_columns(
        pl.col("gap").cum_sum().over("currency").alias("cumulative_gap"),
        (pl.col("gap") * fx_column(fx)).alias("gap_rep"),
    ).with_columns(pl.col("gap_rep").cum_sum().over("currency").alias("cumulative_gap_rep"))
