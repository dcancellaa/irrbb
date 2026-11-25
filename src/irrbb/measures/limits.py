"""Limit framework: ALCO / head-office limits and early-warning triggers.

US supervisors do not prescribe an outlier test: IRRBB is governed by
board-approved / ALCO limits (2010 Interagency Advisory), typically:

* ΔNII loss as a % of base NII (e.g. under ±200bp ramps and shocks);
* ΔEVE loss as a % of base EVE;
* ΔEVE loss as a % of Tier 1 capital.

Each :class:`~irrbb.config.LimitConfig` is evaluated against the worst
scenario in its scope and reported as ``OK`` / ``WARNING`` / ``BREACH``.
A limit without a ``threshold`` reports its measured exposure with status
``NOT_SET``, so the exposure is visible before ALCO approves the levels.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from irrbb.config import LimitConfig
from irrbb.scenarios.shocks import Scenario

LIMIT_SCHEMA: dict[str, pl.DataType] = {
    "limit": pl.String(),
    "metric": pl.String(),
    "basis": pl.String(),
    "worst_scenario": pl.String(),
    "loss": pl.Float64(),
    "base_value": pl.Float64(),
    "measure": pl.Float64(),
    "threshold": pl.Float64(),
    "utilisation": pl.Float64(),
    "status": pl.String(),
}


def _in_scope(limit: LimitConfig, scenarios: Sequence[Scenario]) -> list[str]:
    out = []
    for s in scenarios:
        if s.is_base:
            continue
        if limit.metric == "EVE" and not s.for_eve:
            continue
        if limit.metric == "NII" and not s.for_nii:
            continue
        if limit.scenarios is not None and s.name not in limit.scenarios:
            continue
        if limit.families is not None and s.family not in limit.families:
            continue
        out.append(s.name)
    return out


def _losses(
    limit: LimitConfig,
    eve: pl.DataFrame,
    eve_reg: pl.DataFrame,
    nii: pl.DataFrame,
    names: list[str],
) -> tuple[pl.DataFrame, float]:
    """Return per-scenario losses (positive = loss) and the base value."""
    ccy = pl.lit(True) if limit.currency is None else pl.col("currency") == limit.currency
    if limit.metric == "NII":
        df = nii.filter(ccy)
        base = float(df.filter(pl.col("scenario") == "BASE").get_column("nii_rep").sum())
        losses = (
            df.filter(pl.col("scenario").is_in(names))
            .group_by("scenario")
            .agg((-pl.col("delta_nii_rep").sum()).alias("loss"))
        )
        return losses, base
    df = eve.filter((pl.col("method") == limit.method) & ccy)
    base = float(df.filter(pl.col("scenario") == "BASE").get_column("eve_rep").sum())
    if limit.aggregation == "REGULATORY":
        losses = eve_reg.filter(
            (pl.col("method") == limit.method) & pl.col("scenario").is_in(names)
        ).select("scenario", pl.col("eve_loss").alias("loss"))
    else:
        losses = (
            df.filter(pl.col("scenario").is_in(names))
            .group_by("scenario")
            .agg((-pl.col("delta_eve_rep").sum()).alias("loss"))
        )
    return losses, base


def evaluate_limits(
    limits: Sequence[LimitConfig],
    scenarios: Sequence[Scenario],
    eve: pl.DataFrame,
    eve_regulatory: pl.DataFrame,
    nii: pl.DataFrame,
    tier1_capital: float | None = None,
) -> pl.DataFrame:
    """Evaluate every limit against the run results.

    Args:
        limits: Limit definitions.
        scenarios: Scenarios of the run (for family / applicability).
        eve: EVE table (``method, scenario, currency, eve_rep, delta_eve_rep``).
        eve_regulatory: Regulatory EVE table.
        nii: NII table (``scenario, currency, nii_rep, delta_nii_rep``).
        tier1_capital: Default capital for ``PCT_CAPITAL`` limits.

    Returns:
        Frame with :data:`LIMIT_SCHEMA`, one row per limit.
    """
    rows: list[dict[str, object]] = []
    for lim in limits:
        names = _in_scope(lim, scenarios)
        row: dict[str, object] = {
            "limit": lim.name,
            "metric": lim.metric,
            "basis": lim.basis,
            "threshold": lim.threshold,
            "worst_scenario": None,
            "loss": None,
            "base_value": None,
            "measure": None,
            "utilisation": None,
            "status": "N/A",
        }
        if not names:
            rows.append(row)
            continue
        losses, base = _losses(lim, eve, eve_regulatory, nii, names)
        if losses.is_empty():
            rows.append(row)
            continue
        worst = losses.sort("loss", descending=True).row(0, named=True)
        loss = max(0.0, float(worst["loss"]))
        if lim.basis == "ABSOLUTE":
            measure: float | None = loss
        elif lim.basis == "PCT_BASE":
            measure = loss / abs(base) if base else None
        else:
            capital = lim.capital if lim.capital is not None else tier1_capital
            measure = loss / capital if capital else None
        util = None if measure is None or lim.threshold is None else measure / lim.threshold
        status = (
            "NOT_SET"
            if lim.threshold is None and measure is not None
            else "N/A"
            if util is None
            else "BREACH"
            if util > 1
            else "WARNING"
            if util >= lim.warning_level
            else "OK"
        )
        row |= {
            "worst_scenario": worst["scenario"],
            "loss": loss,
            "base_value": base,
            "measure": measure,
            "utilisation": util,
            "status": status,
        }
        rows.append(row)
    return pl.DataFrame(rows, schema=LIMIT_SCHEMA)
