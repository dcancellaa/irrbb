"""Rate sensitivities: position PV01 and key-rate DV01 by currency.

Sensitivities are computed by full revaluation of the base cash-flow skeleton
under +1bp bumps (``PV(bumped) - PV(base)``), so they capture projection and
discounting effects consistently with EVE.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl

from irrbb.curves.curve_set import CurveSet, FxRates
from irrbb.engine.frames import discount, project
from irrbb.measures.eve import fx_column
from irrbb.scenarios.shocks import ParallelShock, Scenario, key_rate_shock

_POS_KEYS = ["position_id", "product_type", "book", "currency"]


def _pv(skeleton: pl.DataFrame, ext: pl.DataFrame, curves: CurveSet, as_of: date) -> pl.DataFrame:
    flows = (
        project(skeleton, curves, as_of).select(ext.columns)
        if not skeleton.is_empty()
        else ext.clear()
    )
    return discount(pl.concat([flows, ext], how="vertical_relaxed"), curves, as_of)


def position_pv01(
    skeleton: pl.DataFrame,
    ext: pl.DataFrame,
    curves: CurveSet,
    as_of: date,
    fx: FxRates,
    base_pv: pl.DataFrame,
) -> pl.DataFrame:
    """Parallel +1bp PV01 per position.

    Args:
        skeleton: Base cash-flow skeleton.
        ext: External flows (already shaped by :func:`external_flows`).
        curves: Base curves.
        as_of: Valuation date.
        fx: Spot FX.
        base_pv: Base discounted flows (to avoid recomputing).

    Returns:
        Frame ``position_id, product_type, book, currency, pv01, pv01_rep``.
    """
    bumped = Scenario("PV01", ParallelShock(1.0)).apply(curves)
    up = _pv(skeleton, ext, bumped, as_of).group_by(_POS_KEYS).agg(pl.col("pv").sum().alias("up"))
    base = base_pv.group_by(_POS_KEYS).agg(pl.col("pv").sum().alias("base"))
    return (
        base.join(up, on=_POS_KEYS, how="full", coalesce=True, nulls_equal=True)
        .with_columns((pl.col("up").fill_null(0) - pl.col("base").fill_null(0)).alias("pv01"))
        .with_columns((pl.col("pv01") * fx_column(fx)).alias("pv01_rep"))
        .select([*_POS_KEYS, "pv01", "pv01_rep"])
    )


def key_rate_dv01(
    skeleton: pl.DataFrame,
    ext: pl.DataFrame,
    curves: CurveSet,
    as_of: date,
    fx: FxRates,
    pillars: Sequence[float],
    base_pv: pl.DataFrame,
) -> pl.DataFrame:
    """Key-rate DV01 per currency and pillar (triangular +1bp bumps).

    Args:
        skeleton: Base cash-flow skeleton.
        ext: External flows.
        curves: Base curves.
        as_of: Valuation date.
        fx: Spot FX.
        pillars: Key-rate tenors in years.
        base_pv: Base discounted flows.

    Returns:
        Frame ``currency, pillar, dv01, dv01_rep``; the DV01s of a currency
        sum to its parallel PV01.
    """
    rows: list[dict[str, object]] = []
    ccys = sorted(set(base_pv.get_column("currency").unique().to_list()))
    for ccy in ccys:
        sk = skeleton.filter(pl.col("currency") == ccy)
        ex = ext.filter(pl.col("currency") == ccy)
        base = float(base_pv.filter(pl.col("currency") == ccy).get_column("pv").sum())
        for i, p in enumerate(pillars):
            bumped = Scenario(f"KR_{ccy}_{p}", key_rate_shock(pillars, i, 1.0, ccy)).apply(curves)
            pv = float(_pv(sk, ex, bumped, as_of).get_column("pv").sum())
            rows.append({"currency": ccy, "pillar": float(p), "dv01": pv - base})
    df = pl.DataFrame(
        rows, schema={"currency": pl.String, "pillar": pl.Float64, "dv01": pl.Float64}
    )
    return df.with_columns((pl.col("dv01") * fx_column(fx)).alias("dv01_rep"))
