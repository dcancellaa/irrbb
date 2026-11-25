"""Supervisory outlier tests (SOT).

* EVE SOT (BCBS 368 §12): worst ΔEVE loss above 15% of Tier 1 capital.
* NII SOT (EBA GL/2022/14): worst ΔNII loss above 5% of Tier 1 capital
  (configurable; US supervisors use bank-defined limits instead).
"""

from __future__ import annotations

import polars as pl


def outlier_tests(
    eve_regulatory: pl.DataFrame,
    nii_regulatory: pl.DataFrame,
    tier1_capital: float | None,
    eve_threshold: float = 0.15,
    nii_threshold: float = 0.05,
) -> pl.DataFrame:
    """Evaluate the outlier tests.

    Args:
        eve_regulatory: Output of :func:`irrbb.measures.eve.regulatory_eve`.
        nii_regulatory: Output of :func:`irrbb.measures.nii.regulatory_nii`.
        tier1_capital: Tier 1 capital in reporting currency (``None`` = skip ratios).
        eve_threshold: EVE threshold as share of Tier 1.
        nii_threshold: NII threshold as share of Tier 1.

    Returns:
        Frame ``test, method, worst_scenario, loss, tier1_capital, ratio,
        threshold, breach``.
    """
    rows: list[dict[str, object]] = []
    for (method,), grp in eve_regulatory.group_by(["method"], maintain_order=True):
        worst = grp.sort("eve_loss", descending=True).row(0, named=True)
        rows.append(
            {
                "test": "EVE",
                "method": method,
                "worst_scenario": worst["scenario"],
                "loss": worst["eve_loss"],
                "threshold": eve_threshold,
            }
        )
    shocked = nii_regulatory.filter(pl.col("scenario") != "BASE")
    if not shocked.is_empty():
        worst = shocked.sort("nii_loss", descending=True).row(0, named=True)
        rows.append(
            {
                "test": "NII",
                "method": "CONSTANT_BALANCE_SHEET",
                "worst_scenario": worst["scenario"],
                "loss": worst["nii_loss"],
                "threshold": nii_threshold,
            }
        )
    df = pl.DataFrame(
        rows,
        schema={
            "test": pl.String,
            "method": pl.String,
            "worst_scenario": pl.String,
            "loss": pl.Float64,
            "threshold": pl.Float64,
        },
    )
    t1 = pl.lit(tier1_capital, dtype=pl.Float64)
    return (
        df.with_columns(t1.alias("tier1_capital"))
        .with_columns((pl.col("loss") / pl.col("tier1_capital")).alias("ratio"))
        .with_columns((pl.col("ratio") > pl.col("threshold")).alias("breach"))
    )
