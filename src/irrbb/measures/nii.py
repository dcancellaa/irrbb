"""Net Interest Income (NII) and ΔNII under a constant balance sheet.

For every NII segment (see :func:`irrbb.instruments.leg.nii_skeleton`) the
scenario rate is:

* contractual segments: the known rate (unaffected by the shock);
* administered (NMD) segments: ``client_rate + β · s_β · (f_s - f_b)``;
* other repricing segments: ``f_b + margin + β · (f_s - f_b)``;

where ``f_b`` / ``f_s`` are the base / shocked forward rates of the segment
on the leg's projection curve and ``s_β`` the scenario's NMD beta scalar.
For ramp scenarios the shocked forward is phased in:
``f_s ← f_b + w(t) (f_s - f_b)`` with ``w(t) = min(1, 12 t / ramp_months)``
evaluated at the segment mid-point.
ΔNII is reported against the base scenario (negative = loss).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from irrbb.curves.curve_set import CurveSet, FxRates
from irrbb.engine.frames import map_by_curve, years_from
from irrbb.measures.eve import fx_column, with_delta
from irrbb.scenarios.shocks import Scenario


def _forwards(
    df: pl.DataFrame,
    base: CurveSet,
    shocked: CurveSet,
    t1_col: str,
    t2_col: str,
    tau_col: str | None,
) -> pl.DataFrame:
    def fn(key: tuple[object, ...], part: pl.DataFrame) -> pl.DataFrame:
        ccy, index = str(key[0]), key[1]
        idx = None if index is None else str(index)
        cb = base.projection_curve(idx, ccy)
        cs = shocked.projection_curve(idx, ccy)
        t1 = part.get_column(t1_col).to_numpy()
        t2 = part.get_column(t2_col).to_numpy()
        tau = part.get_column(tau_col).to_numpy() if tau_col else None
        t2 = np.maximum(t2, t1 + 1 / 365)
        return part.with_columns(
            pl.Series("f_base", cb.forward_rate(t1, t2, tau)),
            pl.Series("f_scn", cs.forward_rate(t1, t2, tau)),
        )

    out = map_by_curve(df, ["currency", "index"], fn)
    if out.is_empty():
        out = out.with_columns(pl.lit(0.0).alias("f_base"), pl.lit(0.0).alias("f_scn"))
    return out


def _apply_ramp(df: pl.DataFrame, scenario: Scenario) -> pl.DataFrame:
    """Phase in a ramp scenario: ``f_s ← f_b + w(t_mid) · (f_s - f_b)``."""
    if not scenario.ramp_months or df.is_empty():
        return df
    mid = ((df.get_column("_t1") + df.get_column("_t2")) / 2).to_numpy()
    w = pl.Series("_w", scenario.ramp_weight(mid))
    return (
        df.with_columns(w)
        .with_columns(
            (pl.col("f_base") + pl.col("_w") * (pl.col("f_scn") - pl.col("f_base"))).alias("f_scn")
        )
        .drop("_w")
    )


def nii_segments(
    nii_frame: pl.DataFrame,
    base_curves: CurveSet,
    scenario_curves: CurveSet,
    scenario: Scenario,
    as_of: date,
) -> pl.DataFrame:
    """Evaluate NII for every segment in one scenario.

    Args:
        nii_frame: Output of :func:`irrbb.engine.frames.build_nii_frame`.
        base_curves: Base curve set.
        scenario_curves: Shocked curve set of ``scenario``.
        scenario: The scenario (for the NMD beta scalar).
        as_of: Valuation date.

    Returns:
        Segments with ``rate`` and signed ``nii`` columns plus ``scenario``.
    """
    df = nii_frame.with_columns(
        years_from("seg_start", as_of).alias("_t1"), years_from("seg_end", as_of).alias("_t2")
    )
    df = _apply_ramp(_forwards(df, base_curves, scenario_curves, "_t1", "_t2", "tau"), scenario)
    move = pl.col("f_scn") - pl.col("f_base")
    rate = (
        pl.when(~pl.col("reprices"))
        .then(pl.col("known_rate"))
        .when(pl.col("admin_rate").is_not_null())
        .then(pl.col("admin_rate") + pl.col("beta") * scenario.nmd_beta_scalar * move)
        .otherwise(pl.col("f_base") + pl.col("margin") + pl.col("beta") * move)
    )
    return (
        df.with_columns(rate.alias("rate"))
        .with_columns(
            (pl.col("sign") * pl.col("balance") * pl.col("rate") * pl.col("tau")).alias("nii"),
            pl.lit(scenario.name).alias("scenario"),
        )
        .drop(["_t1", "_t2"])
    )


def external_nii(
    flows: pl.DataFrame,
    base_curves: CurveSet,
    scenario_curves: CurveSet,
    scenario: Scenario,
    as_of: date,
    horizon_end: date,
) -> pl.DataFrame:
    """NII contribution of externally supplied cash flows.

    Interest paid within the horizon is earned as-is. Principal received or
    paid within the horizon is assumed to be reinvested / refinanced until the
    horizon end at the (shocked) forward rate of the currency's curve.

    Args:
        flows: External flows (:func:`irrbb.engine.frames.external_flows`).
        base_curves: Base curves.
        scenario_curves: Shocked curves.
        scenario: Scenario.
        as_of: Valuation date.
        horizon_end: Horizon end.

    Returns:
        Frame ``position_id, product_type, book, currency, nii, scenario``.
    """
    inside = flows.filter((pl.col("pay_date") > as_of) & (pl.col("pay_date") <= horizon_end))
    if inside.is_empty():
        return pl.DataFrame(
            schema={
                "position_id": pl.String(),
                "product_type": pl.String(),
                "book": pl.String(),
                "currency": pl.String(),
                "nii": pl.Float64(),
                "scenario": pl.String(),
            }
        )
    df = inside.with_columns(
        pl.lit(None, dtype=pl.String).alias("index"),
        years_from("pay_date", as_of).alias("_t1"),
        pl.lit((horizon_end - as_of).days / 365.0).alias("_t2"),
    )
    df = _apply_ramp(_forwards(df, base_curves, scenario_curves, "_t1", "_t2", None), scenario)
    reinvest = pl.col("principal") * pl.col("f_scn") * (pl.col("_t2") - pl.col("_t1"))
    return df.with_columns(
        (pl.col("interest") + reinvest).alias("nii"), pl.lit(scenario.name).alias("scenario")
    ).select(["position_id", "product_type", "book", "currency", "nii", "scenario"])


def nii_by_currency(segments: pl.DataFrame, fx: FxRates) -> pl.DataFrame:
    """Aggregate NII by scenario and currency and compute ΔNII.

    Args:
        segments: Concatenated segment-level NII across scenarios.
        fx: Spot FX.

    Returns:
        Frame ``scenario, currency, nii, nii_rep, delta_nii, delta_nii_rep``.
    """
    agg = (
        segments.group_by(["scenario", "currency"], maintain_order=True)
        .agg(pl.col("nii").sum())
        .with_columns((pl.col("nii") * fx_column(fx)).alias("nii_rep"))
    )
    return with_delta(agg, ["currency"], ["nii", "nii_rep"])


def regulatory_nii(nii: pl.DataFrame, base_scenario: str = "BASE") -> pl.DataFrame:
    """Total ΔNII per scenario in reporting currency.

    Unlike EVE, BCBS 368 does not floor currency-level ΔNII gains, so currency
    results are summed.

    Args:
        nii: Output of :func:`nii_by_currency`.
        base_scenario: Base scenario name.

    Returns:
        Frame ``scenario, nii_rep, delta_nii_rep, nii_loss, is_worst``.
    """
    out = (
        nii.group_by("scenario", maintain_order=True)
        .agg(pl.col("nii_rep").sum(), pl.col("delta_nii_rep").sum())
        .with_columns((-pl.col("delta_nii_rep")).clip(lower_bound=0.0).alias("nii_loss"))
    )
    shocked = pl.col("scenario") != base_scenario
    return out.with_columns(
        (shocked & (pl.col("nii_loss") == pl.col("nii_loss").filter(shocked).max())).alias(
            "is_worst"
        )
    )
