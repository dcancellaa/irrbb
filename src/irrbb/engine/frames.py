"""Vectorised skeleton, behaviour, projection and discounting on Polars frames.

Pipeline for one run:

1. :func:`build_skeleton` — called **once**: every leg emits its remaining
   periods with *scheduled* balances (see :func:`irrbb.instruments.leg.leg_skeleton`).
2. :func:`cashflow_frame` / :func:`repricing_frame` — per distinct set of
   behavioural scalars: survival factors ``(1 - γ·rate)^t`` are applied as
   Polars expressions to obtain behavioural balances and principal flows.
3. :func:`project` / :func:`discount` — per scenario: forward projection and
   discounting with numpy, partitioned by curve.

No Python loop over positions happens after step 1, so a scenario costs a
handful of vectorised operations whatever the portfolio size.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from irrbb.curves.curve_set import CurveSet
from irrbb.instruments.leg import Leg, Record, leg_skeleton, nii_skeleton

_ID_SCHEMA: dict[str, pl.DataType] = {
    "position_id": pl.String(),
    "leg_id": pl.String(),
    "product_type": pl.String(),
    "book": pl.String(),
    "currency": pl.String(),
    "sign": pl.Int8(),
    "rate_type": pl.String(),
    "index": pl.String(),
    "discount_curve": pl.String(),
    "leg_no": pl.Int64(),
}

PERIODS_SCHEMA: dict[str, pl.DataType] = {
    **_ID_SCHEMA,
    "period_no": pl.Int32(),
    "pay_date": pl.Date(),
    "accrual_start": pl.Date(),
    "accrual_end": pl.Date(),
    "tau": pl.Float64(),
    "notional": pl.Float64(),
    "sched_end": pl.Float64(),
    "runoff_rate": pl.Float64(),
    "behaviour": pl.String(),
    "final_exchange": pl.Boolean(),
    "known_rate": pl.Float64(),
    "spread": pl.Float64(),
    "projected": pl.Boolean(),
}

EXCHANGE_SCHEMA: dict[str, pl.DataType] = {
    **_ID_SCHEMA,
    "pay_date": pl.Date(),
    "principal": pl.Float64(),
}

FLOAT_REPRICING_SCHEMA: dict[str, pl.DataType] = {
    **_ID_SCHEMA,
    "pay_date": pl.Date(),
    "interest": pl.Float64(),
    "principal": pl.Float64(),
}

#: Columns of a cash-flow frame ready for :func:`project`.
CASHFLOW_SKELETON_SCHEMA: dict[str, pl.DataType] = {
    **_ID_SCHEMA,
    "pay_date": pl.Date(),
    "accrual_start": pl.Date(),
    "accrual_end": pl.Date(),
    "tau": pl.Float64(),
    "balance": pl.Float64(),
    "principal": pl.Float64(),
    "known_rate": pl.Float64(),
    "spread": pl.Float64(),
    "projected": pl.Boolean(),
}

NII_SCHEMA: dict[str, pl.DataType] = {
    **{k: v for k, v in _ID_SCHEMA.items() if k != "leg_no"},
    "seg_start": pl.Date(),
    "seg_end": pl.Date(),
    "tau": pl.Float64(),
    "balance": pl.Float64(),
    "known_rate": pl.Float64(),
    "margin": pl.Float64(),
    "beta": pl.Float64(),
    "reprices": pl.Boolean(),
    "admin_rate": pl.Float64(),
}

#: Columns of an evaluated (signed) cash-flow frame.
FLOW_COLUMNS: tuple[str, ...] = (
    "position_id",
    "leg_id",
    "product_type",
    "book",
    "currency",
    "discount_curve",
    "pay_date",
    "interest",
    "principal",
    "cashflow",
)


def _frame(rows: Iterable[Record], schema: dict[str, pl.DataType]) -> pl.DataFrame:
    data = list(rows)
    if not data:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(data, schema=schema)


@dataclass(frozen=True)
class Skeleton:
    """Scenario-independent skeleton of a whole portfolio.

    Attributes:
        periods: Remaining periods of every leg (:data:`PERIODS_SCHEMA`).
        exchanges: Initial notional exchanges (:data:`EXCHANGE_SCHEMA`).
        float_repricing: Floating-leg repricing flows (:data:`FLOAT_REPRICING_SCHEMA`).
        as_of: Valuation date.
    """

    periods: pl.DataFrame
    exchanges: pl.DataFrame
    float_repricing: pl.DataFrame
    as_of: date

    def filter(self, predicate: pl.Expr) -> Skeleton:
        """Restrict every component frame with the same predicate.

        Args:
            predicate: Expression on identifier columns (e.g. currency).

        Returns:
            A filtered skeleton.
        """
        return Skeleton(
            self.periods.filter(predicate),
            self.exchanges.filter(predicate),
            self.float_repricing.filter(predicate),
            self.as_of,
        )


def build_skeleton(legs: Sequence[Leg], as_of: date, curves: CurveSet) -> Skeleton:
    """Build the portfolio skeleton (the only per-leg Python loop of a run).

    Args:
        legs: Portfolio legs.
        as_of: Valuation date.
        curves: Base curves (to fill missing current fixings).

    Returns:
        The skeleton.
    """
    periods: list[Record] = []
    exchanges: list[Record] = []
    float_rp: list[Record] = []
    for i, leg in enumerate(legs):
        sk = leg_skeleton(leg, as_of, curves, i)
        periods.extend(sk.periods)
        exchanges.extend(sk.exchanges)
        float_rp.extend(sk.float_repricing)
    return Skeleton(
        _frame(periods, PERIODS_SCHEMA).sort(["leg_no", "period_no"]),
        _frame(exchanges, EXCHANGE_SCHEMA),
        _frame(float_rp, FLOAT_REPRICING_SCHEMA),
        as_of,
    )


def years_from(col: str, as_of: date) -> pl.Expr:
    """Expression converting a date column into ACT/365F years from ``as_of``.

    Args:
        col: Date column name.
        as_of: Time zero.

    Returns:
        Float expression.
    """
    return (pl.col(col) - pl.lit(as_of)).dt.total_days().cast(pl.Float64) / 365.0


def behavioural_periods(
    sk: Skeleton,
    prepayment_scalar: float = 1.0,
    tdrr_scalar: float = 1.0,
    contractual: bool = False,
) -> pl.DataFrame:
    """Apply behavioural run-off to the scheduled balances.

    ``end = sched_end · (1 - min(1, γ · runoff_rate))^t_end`` where ``γ`` is the
    prepayment scalar for ``PREPAYMENT`` legs, the TDRR scalar for
    ``EARLY_REDEMPTION`` legs and 1 otherwise. Start balances are the previous
    period's end balance (the notional for the first period).

    Args:
        sk: Portfolio skeleton.
        prepayment_scalar: Scenario prepayment scalar.
        tdrr_scalar: Scenario TDRR scalar.
        contractual: Ignore all run-off (contractual cash flows).

    Returns:
        Periods with unsigned ``balance`` (start of period), ``end_balance``
        and ``amortisation`` columns.
    """
    gamma = (
        pl.when(pl.col("behaviour") == "PREPAYMENT")
        .then(pl.lit(prepayment_scalar))
        .when(pl.col("behaviour") == "EARLY_REDEMPTION")
        .then(pl.lit(tdrr_scalar))
        .otherwise(pl.lit(1.0))
    )
    eff = (pl.col("runoff_rate") * gamma).clip(0.0, 1.0)
    t_end = years_from("pay_date", sk.as_of).clip(lower_bound=0.0)
    surv = (
        pl.lit(1.0) if contractual else pl.when(eff > 0).then((1.0 - eff).pow(t_end)).otherwise(1.0)
    )
    df = sk.periods.with_columns((pl.col("sched_end") * surv).alias("end_balance"))
    df = df.with_columns(
        pl.col("end_balance").shift(1).over("leg_no").fill_null(pl.col("notional")).alias("balance")
    )
    return df.with_columns((pl.col("balance") - pl.col("end_balance")).alias("amortisation"))


def cashflow_frame(
    sk: Skeleton,
    prepayment_scalar: float = 1.0,
    tdrr_scalar: float = 1.0,
    contractual: bool = False,
) -> pl.DataFrame:
    """Contractual / behavioural cash flows ready for :func:`project`.

    Args:
        sk: Portfolio skeleton.
        prepayment_scalar: Scenario prepayment scalar.
        tdrr_scalar: Scenario TDRR scalar.
        contractual: Ignore all run-off (used for reconciliation).

    Returns:
        Frame with :data:`CASHFLOW_SKELETON_SCHEMA` (principal unsigned and
        zero for legs without final exchange).
    """
    per = (
        behavioural_periods(sk, prepayment_scalar, tdrr_scalar, contractual)
        .with_columns(
            pl.when(pl.col("final_exchange"))
            .then(pl.col("amortisation"))
            .otherwise(0.0)
            .alias("principal")
        )
        .select(list(CASHFLOW_SKELETON_SCHEMA))
    )
    exch = sk.exchanges.with_columns(
        pl.col("pay_date").alias("accrual_start"),
        pl.col("pay_date").alias("accrual_end"),
        pl.lit(0.0).alias("tau"),
        pl.lit(0.0).alias("balance"),
        pl.lit(0.0).alias("known_rate"),
        pl.lit(0.0).alias("spread"),
        pl.lit(False).alias("projected"),
    ).select(list(CASHFLOW_SKELETON_SCHEMA))
    return pl.concat([exch, per], how="vertical_relaxed")


def repricing_frame(
    sk: Skeleton, prepayment_scalar: float = 1.0, tdrr_scalar: float = 1.0
) -> pl.DataFrame:
    """Signed repricing cash flows (standardised EVE and gap).

    Args:
        sk: Portfolio skeleton.
        prepayment_scalar: Scenario prepayment scalar.
        tdrr_scalar: Scenario TDRR scalar.

    Returns:
        Frame with :data:`FLOW_COLUMNS` plus identifier columns.
    """
    fixed = behavioural_periods(
        sk.filter(pl.col("rate_type") != "FLOAT"), prepayment_scalar, tdrr_scalar
    ).with_columns(
        (pl.col("balance") * pl.col("known_rate") * pl.col("tau")).alias("interest"),
        pl.col("amortisation").alias("principal"),  # synthetic notional for swaps
    )
    cols = [*_ID_SCHEMA, "pay_date", "interest", "principal"]
    parts = [
        fixed.select(cols),
        sk.exchanges.with_columns(pl.lit(0.0).alias("interest")).select(cols),
        sk.float_repricing.select(cols),
    ]
    df = pl.concat(parts, how="vertical_relaxed")
    return df.with_columns(
        (pl.col("interest") * pl.col("sign")).alias("interest"),
        (pl.col("principal") * pl.col("sign")).alias("principal"),
    ).with_columns((pl.col("interest") + pl.col("principal")).alias("cashflow"))


# --------------------------------------------------------- compatibility API
def build_cashflow_skeleton(
    legs: Sequence[Leg],
    as_of: date,
    curves: CurveSet,
    prepayment_scalar: float = 1.0,
    tdrr_scalar: float = 1.0,
) -> pl.DataFrame:
    """Convenience: skeleton + behaviour in one call (small portfolios / tests).

    Args:
        legs: Legs.
        as_of: Valuation date.
        curves: Base curves.
        prepayment_scalar: Prepayment scalar.
        tdrr_scalar: TDRR scalar.

    Returns:
        Frame with :data:`CASHFLOW_SKELETON_SCHEMA`.
    """
    return cashflow_frame(build_skeleton(legs, as_of, curves), prepayment_scalar, tdrr_scalar)


def build_repricing_frame(
    legs: Sequence[Leg],
    as_of: date,
    curves: CurveSet,
    prepayment_scalar: float = 1.0,
    tdrr_scalar: float = 1.0,
) -> pl.DataFrame:
    """Convenience: skeleton + repricing flows in one call.

    Args:
        legs: Legs.
        as_of: Valuation date.
        curves: Base curves.
        prepayment_scalar: Prepayment scalar.
        tdrr_scalar: TDRR scalar.

    Returns:
        Signed repricing flows.
    """
    return repricing_frame(build_skeleton(legs, as_of, curves), prepayment_scalar, tdrr_scalar)


def build_nii_frame(
    legs: Sequence[Leg], as_of: date, horizon_end: date, curves: CurveSet
) -> pl.DataFrame:
    """Stack NII segments of all legs.

    Args:
        legs: NII legs (constant balance sheet view).
        as_of: Valuation date.
        horizon_end: End of the NII horizon.
        curves: Base curves.

    Returns:
        Frame with :data:`NII_SCHEMA`.
    """
    rows = (r for leg in legs for r in nii_skeleton(leg, as_of, horizon_end, curves))
    return _frame(rows, NII_SCHEMA)


# ------------------------------------------------------ projection/discount
def map_by_curve(
    df: pl.DataFrame,
    keys: list[str],
    fn: Callable[[tuple[object, ...], pl.DataFrame], pl.DataFrame],
) -> pl.DataFrame:
    """Apply ``fn`` to each partition of ``df`` and re-stack the results.

    Args:
        df: Input frame.
        keys: Partition columns (e.g. ``["currency", "index"]``).
        fn: Function of ``(partition key, partition frame)`` returning a frame.

    Returns:
        Concatenated results (empty input returns ``df`` unchanged).
    """
    if df.is_empty():
        return df
    parts = [
        fn(k, part) for k, part in df.partition_by(keys, as_dict=True, maintain_order=True).items()
    ]
    return pl.concat(parts, how="vertical_relaxed")


def project(skeleton: pl.DataFrame, curves: CurveSet, as_of: date) -> pl.DataFrame:
    """Project floating coupons and compute signed cash flows.

    Args:
        skeleton: Cash-flow frame (:func:`cashflow_frame`).
        curves: (Possibly shocked) curve set.
        as_of: Valuation date.

    Returns:
        Input plus ``rate``, signed ``interest``, ``principal`` and ``cashflow``.
    """
    df = skeleton.with_columns(
        years_from("accrual_start", as_of).clip(lower_bound=0.0).alias("_t1"),
        years_from("accrual_end", as_of).alias("_t2"),
    )

    def _fwd(key: tuple[object, ...], part: pl.DataFrame) -> pl.DataFrame:
        ccy, index = str(key[0]), key[1]
        curve = curves.projection_curve(None if index is None else str(index), ccy)
        proj = part.get_column("projected").to_numpy()
        fwd = np.zeros(part.height)
        if proj.any():
            t1 = part.get_column("_t1").to_numpy()[proj]
            t2 = part.get_column("_t2").to_numpy()[proj]
            tau = part.get_column("tau").to_numpy()[proj]
            fwd[proj] = curve.forward_rate(t1, t2, tau)
        return part.with_columns(pl.Series("_fwd", fwd))

    df = map_by_curve(df, ["currency", "index"], _fwd)
    if df.is_empty():
        df = df.with_columns(pl.lit(0.0).alias("_fwd"))
    return (
        df.with_columns(
            pl.when(pl.col("projected"))
            .then(pl.col("_fwd") + pl.col("spread"))
            .otherwise(pl.col("known_rate"))
            .alias("rate")
        )
        .with_columns(
            (pl.col("sign") * pl.col("balance") * pl.col("rate") * pl.col("tau")).alias("interest"),
            (pl.col("sign") * pl.col("principal")).alias("principal"),
        )
        .with_columns((pl.col("interest") + pl.col("principal")).alias("cashflow"))
        .drop(["_t1", "_t2", "_fwd"])
    )


def discount(
    flows: pl.DataFrame, curves: CurveSet, as_of: date, date_col: str = "pay_date"
) -> pl.DataFrame:
    """Discount signed cash flows on their discount curve.

    Each flow uses its ``discount_curve`` when set (e.g. the NDF-implied BRL
    curve), otherwise its currency's discount curve.

    Args:
        flows: Frame with ``currency``, ``cashflow`` and a date column.
        curves: (Possibly shocked) curve set.
        as_of: Valuation date.
        date_col: Payment date column.

    Returns:
        ``flows`` plus ``t``, ``df`` and ``pv`` columns.
    """
    df = flows.with_columns(years_from(date_col, as_of).alias("t"))

    def _df(key: tuple[object, ...], part: pl.DataFrame) -> pl.DataFrame:
        curve = curves.discounting(None if key[1] is None else str(key[1]), str(key[0]))
        return part.with_columns(
            pl.Series("df", curve.discount_factor(part.get_column("t").to_numpy()))
        )

    if "discount_curve" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias("discount_curve"))
    df = map_by_curve(df, ["currency", "discount_curve"], _df)
    if "df" not in df.columns:
        df = df.with_columns(pl.lit(1.0).alias("df"))
    return df.with_columns((pl.col("cashflow") * pl.col("df")).alias("pv"))


def external_flows(cashflows: pl.DataFrame) -> pl.DataFrame:
    """Shape externally supplied cash flows like evaluated flows.

    External amounts are already signed from the bank's perspective
    (``+`` = inflow) and are treated as scenario-invariant.

    Args:
        cashflows: Normalised external cash-flow frame.

    Returns:
        Frame with :data:`FLOW_COLUMNS`.
    """
    if cashflows.is_empty():
        return pl.DataFrame(
            schema={c: CASHFLOW_SKELETON_SCHEMA.get(c, pl.Float64()) for c in FLOW_COLUMNS}
        )
    return (
        cashflows.with_columns(
            pl.lit("EXT").alias("leg_id"),
            pl.lit(None, dtype=pl.String).alias("discount_curve"),
            pl.col("product_type").fill_null("EXTERNAL"),
            pl.col("book").fill_null("BANKING"),
            pl.col("interest").fill_null(0.0),
            pl.col("principal").fill_null(0.0),
        )
        .with_columns((pl.col("interest") + pl.col("principal")).alias("cashflow"))
        .select(FLOW_COLUMNS)
    )
