"""Economic Value of Equity (EVE) measures.

Sign convention used throughout the engine: ``delta = shocked - base``, so a
**negative** ΔEVE is a **loss**. Regulatory aggregates (``eve_loss``) are
reported as positive losses, as in BCBS 368.

Two approaches are provided:

* **Full revaluation** (internal measurement system): every cash flow is
  re-projected and re-discounted on the shocked curves, and behavioural
  assumptions follow the scenario scalars.
* **Standardised** (BCBS 368 §4): repricing cash flows are slotted into the 19
  time buckets and discounted at the bucket mid-points with
  ``DF = exp(-R(t_k) · t_k)`` using the shocked risk-free curve.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import polars as pl

from irrbb.core.buckets import BCBS_BUCKETS, bucket_index, buckets_frame
from irrbb.curves.curve_set import CurveSet, FxRates
from irrbb.engine.frames import years_from


def fx_column(fx: FxRates, currency_col: str = "currency") -> pl.Expr:
    """Expression mapping a currency column to its FX rate.

    Args:
        fx: FX rates.
        currency_col: Currency column name.

    Returns:
        Float expression of reporting units per currency unit.
    """
    mapping = {c: fx.rate(c) for c in fx.spot} | {fx.reporting_currency: 1.0}
    return pl.col(currency_col).replace_strict(mapping, default=None, return_dtype=pl.Float64)


def with_delta(
    df: pl.DataFrame, keys: list[str], value_cols: list[str], base_scenario: str = "BASE"
) -> pl.DataFrame:
    """Add ``delta_<col>`` columns relative to the base scenario.

    Args:
        df: Frame with a ``scenario`` column.
        keys: Grouping keys (excluding ``scenario``).
        value_cols: Value columns to difference.
        base_scenario: Name of the base scenario.

    Returns:
        ``df`` with one ``delta_<col>`` per value column.
    """
    base = df.filter(pl.col("scenario") == base_scenario).select(
        [*keys, *[pl.col(c).alias(f"_base_{c}") for c in value_cols]]
    )
    out = df.join(base, on=keys, how="left", nulls_equal=True)
    return out.with_columns(
        [(pl.col(c) - pl.col(f"_base_{c}").fill_null(0.0)).alias(f"delta_{c}") for c in value_cols]
    ).drop([f"_base_{c}" for c in value_cols])


def eve_full_revaluation(pv: pl.DataFrame, fx: FxRates) -> pl.DataFrame:
    """Aggregate scenario PVs into EVE by currency.

    Args:
        pv: Discounted flows with ``scenario``, ``currency`` and ``pv``.
        fx: Spot FX used to convert to reporting currency.

    Returns:
        Frame ``scenario, currency, eve, eve_rep, delta_eve, delta_eve_rep``.
    """
    agg = (
        pv.group_by(["scenario", "currency"], maintain_order=True)
        .agg(pl.col("pv").sum().alias("eve"))
        .with_columns((pl.col("eve") * fx_column(fx)).alias("eve_rep"))
    )
    return with_delta(agg, ["currency"], ["eve", "eve_rep"]).with_columns(
        pl.lit("FULL_REVALUATION").alias("method")
    )


def eve_standardised(
    repricing: pl.DataFrame,
    scenario_curves: Mapping[str, CurveSet],
    as_of: date,
    fx: FxRates,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """BCBS 368 standardised EVE.

    Args:
        repricing: Signed repricing flows with ``scenario``, ``currency``,
            ``pay_date`` and ``cashflow`` (flows may differ by scenario through
            the behavioural scalars).
        scenario_curves: Shocked curve set per scenario name.
        as_of: Valuation date.
        fx: Spot FX.

    Returns:
        ``(eve_by_currency, slotted)`` where ``slotted`` holds the bucketed
        cash flows and discount factors per scenario / currency / bucket.
    """
    df = repricing.with_columns(years_from("pay_date", as_of).alias("t"))
    df = df.with_columns(
        pl.Series("bucket", bucket_index(df.get_column("t").to_numpy(), BCBS_BUCKETS))
    )
    slotted = (
        df.group_by(["scenario", "currency", "bucket"])
        .agg(pl.col("cashflow").sum())
        .join(buckets_frame().select(["bucket", "bucket_label", "midpoint"]), on="bucket")
        .sort(["scenario", "currency", "bucket"])
    )
    parts: list[pl.DataFrame] = []
    for (scn, ccy), part in slotted.partition_by(
        ["scenario", "currency"], as_dict=True, maintain_order=True
    ).items():
        curve = scenario_curves[str(scn)].discount_curve(str(ccy))
        dfs = curve.discount_factor(part.get_column("midpoint").to_numpy())
        parts.append(part.with_columns(pl.Series("df", dfs)))
    slotted = pl.concat(parts) if parts else slotted.with_columns(pl.lit(1.0).alias("df"))
    slotted = slotted.with_columns((pl.col("cashflow") * pl.col("df")).alias("pv"))
    agg = (
        slotted.group_by(["scenario", "currency"], maintain_order=True)
        .agg(pl.col("pv").sum().alias("eve"))
        .with_columns((pl.col("eve") * fx_column(fx)).alias("eve_rep"))
    )
    eve = with_delta(agg, ["currency"], ["eve", "eve_rep"]).with_columns(
        pl.lit("STANDARDISED").alias("method")
    )
    return eve, slotted


def regulatory_eve(
    eve: pl.DataFrame, gain_weight: float = 0.0, base_scenario: str = "BASE"
) -> pl.DataFrame:
    """Aggregate ΔEVE across currencies into a regulatory loss per scenario.

    Per BCBS 368, losses in one currency are not offset by gains in another:
    ``loss_i = max(0, Σ_c [max(0, -ΔEVE_ic) - w · max(0, ΔEVE_ic)])`` with
    ``w = 0`` (BCBS) or ``w = 0.5`` (EBA).

    Args:
        eve: Output of :func:`eve_full_revaluation` / :func:`eve_standardised`.
        gain_weight: Weight ``w`` of gains.
        base_scenario: Base scenario name (excluded).

    Returns:
        Frame ``method, scenario, eve_loss, base_eve_rep, is_worst``.
    """
    d = pl.col("delta_eve_rep")
    contrib = pl.when(d < 0).then(-d).otherwise(-gain_weight * d)
    shocked = eve.filter(pl.col("scenario") != base_scenario)
    base = (
        eve.filter(pl.col("scenario") == base_scenario)
        .group_by("method")
        .agg(pl.col("eve_rep").sum().alias("base_eve_rep"))
    )
    out = (
        shocked.group_by(["method", "scenario"], maintain_order=True)
        .agg(
            contrib.sum().clip(lower_bound=0.0).alias("eve_loss"),
            d.sum().alias("delta_eve_rep_net"),
        )
        .join(base, on="method", how="left")
    )
    return out.with_columns(
        (pl.col("eve_loss") == pl.col("eve_loss").max().over("method")).alias("is_worst")
    )
