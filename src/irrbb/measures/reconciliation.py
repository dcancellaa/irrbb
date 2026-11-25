"""Cash-flow reconciliation between the engine and source systems.

When a source system sends both contract terms (``positions``) and its own
projected cash flows (``cashflows``) for the same ``position_id``, the engine
values the contract terms and uses the source flows as an independent check.
This catches data issues early: wrong outstanding balances, amortisation
types, maturities, day counts or missing fixings.

Principal is compared month by month with a tight tolerance. Interest is
compared in total with a looser tolerance, because each system projects
floating coupons off its own curves. Non-maturity deposits are not
comparable (the engine's flows are behavioural) and are reported as such.
"""

from __future__ import annotations

import polars as pl

SUMMARY_SCHEMA: dict[str, pl.DataType] = {
    "position_id": pl.String(),
    "currency": pl.String(),
    "engine_principal": pl.Float64(),
    "source_principal": pl.Float64(),
    "principal_diff": pl.Float64(),
    "max_month_principal_diff": pl.Float64(),
    "engine_interest": pl.Float64(),
    "source_interest": pl.Float64(),
    "interest_diff": pl.Float64(),
    "status": pl.String(),
}


def _monthly(df: pl.DataFrame, prefix: str) -> pl.DataFrame:
    return (
        df.with_columns(pl.col("pay_date").dt.truncate("1mo").alias("month"))
        .group_by(["position_id", "currency", "month"])
        .agg(
            pl.col("principal").sum().alias(f"{prefix}_principal"),
            pl.col("interest").sum().alias(f"{prefix}_interest"),
        )
    )


def reconcile_cashflows(
    engine: pl.DataFrame,
    source: pl.DataFrame,
    not_comparable: set[str] | None = None,
    tolerance_abs: float = 1.0,
    tolerance_rel: float = 1e-4,
    interest_tolerance_rel: float = 0.02,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Compare engine and source cash flows position by position.

    Args:
        engine: Engine contractual flows (signed) with ``position_id``,
            ``currency``, ``pay_date``, ``interest``, ``principal``.
        source: Source flows (signed) with the same columns.
        not_comparable: Position ids that cannot be compared (NMDs).
        tolerance_abs: Absolute tolerance (currency units).
        tolerance_rel: Relative tolerance on principal.
        interest_tolerance_rel: Relative tolerance on total interest.

    Returns:
        ``(summary, detail)``: one summary row per position and currency, and
        the monthly comparison.
    """
    keys = ["position_id", "currency", "month"]
    detail = (
        _monthly(engine, "engine")
        .join(_monthly(source, "source"), on=keys, how="full", coalesce=True)
        .with_columns(pl.col("^(engine|source)_.*$").fill_null(0.0))
        .with_columns(
            (pl.col("engine_principal") - pl.col("source_principal")).alias("principal_diff"),
            (pl.col("engine_interest") - pl.col("source_interest")).alias("interest_diff"),
        )
        .sort(keys)
    )
    summary = detail.group_by(["position_id", "currency"], maintain_order=True).agg(
        pl.col("engine_principal").sum(),
        pl.col("source_principal").sum(),
        pl.col("principal_diff").sum(),
        pl.col("principal_diff").abs().max().alias("max_month_principal_diff"),
        pl.col("engine_interest").sum(),
        pl.col("source_interest").sum(),
        pl.col("interest_diff").sum(),
        pl.col("source_principal").abs().sum().alias("_gross"),
    )
    p_tol = tolerance_abs + tolerance_rel * pl.col("_gross")
    i_tol = tolerance_abs + interest_tolerance_rel * pl.col("source_interest").abs()
    p_ok = (pl.col("principal_diff").abs() <= p_tol) & (pl.col("max_month_principal_diff") <= p_tol)
    i_ok = pl.col("interest_diff").abs() <= i_tol
    nc = list(not_comparable or set())
    status = (
        pl.when(pl.col("position_id").is_in(nc))
        .then(pl.lit("NOT_COMPARABLE"))
        .when(p_ok & i_ok)
        .then(pl.lit("MATCH"))
        .when(p_ok)
        .then(pl.lit("INTEREST_BREAK"))
        .when(i_ok)
        .then(pl.lit("PRINCIPAL_BREAK"))
        .otherwise(pl.lit("BREAK"))
    )
    summary = summary.with_columns(status.alias("status")).select(list(SUMMARY_SCHEMA))
    return summary, detail
