"""Run orchestration: from input frames to a complete :class:`RunResult`."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

from irrbb.behavioral.nmd import (
    AssumptionTable,
    NMDAssumption,
    NMDModel,
)
from irrbb.config import RunConfig
from irrbb.core.dates import add_months
from irrbb.curves.curve_set import CurveSet, FxRates
from irrbb.data.market import curves_from_frame, fx_from_frame
from irrbb.data.quality import (
    DataQualityError,
    check_coverage,
    check_extrapolation,
    check_quotes,
    check_raw_quotes,
    compare_quotes,
    findings_frame,
)
from irrbb.data.schemas import (
    CASHFLOWS_REQUIRED,
    CASHFLOWS_SCHEMA,
    CURVE_QUOTES_REQUIRED,
    CURVE_QUOTES_SCHEMA,
    NMD_ASSUMPTIONS_REQUIRED,
    NMD_ASSUMPTIONS_SCHEMA,
    POSITIONS_REQUIRED,
    POSITIONS_SCHEMA,
    ValidationReport,
    normalise,
)
from irrbb.engine.frames import (
    FLOW_COLUMNS,
    build_nii_frame,
    build_skeleton,
    cashflow_frame,
    discount,
    external_flows,
    project,
    repricing_frame,
)
from irrbb.instruments.factory import Portfolio, build_portfolio
from irrbb.measures.eve import (
    eve_full_revaluation,
    eve_standardised,
    fx_column,
    regulatory_eve,
    with_delta,
)
from irrbb.measures.gap import repricing_gap
from irrbb.measures.limits import evaluate_limits
from irrbb.measures.ndf import ndf_settlements
from irrbb.measures.nii import external_nii, nii_by_currency, nii_segments, regulatory_nii
from irrbb.measures.outlier import outlier_tests
from irrbb.measures.reconciliation import SUMMARY_SCHEMA as RECON_SUMMARY_SCHEMA
from irrbb.measures.reconciliation import reconcile_cashflows
from irrbb.measures.sensitivity import key_rate_dv01, position_pv01
from irrbb.scenarios.shocks import Scenario

logger = logging.getLogger(__name__)

_POS_KEYS = ["position_id", "product_type", "book", "currency"]
_CURVE_GRID = np.array([1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30], dtype=np.float64)


@dataclass
class RunInputs:
    """Market data and portfolio for one run.

    Attributes:
        curves: Base curve set.
        fx: Spot FX rates.
        positions: Raw or normalised positions frame (contract terms).
        cashflows: Raw or normalised external cash-flow frame.
        curve_quotes: Normalised raw quotes the curves were built from (enables
            quote-level data-quality checks).
        previous_quotes: Normalised quotes of a previous date (day-over-day
            checks).
        nmd_assumptions: Segment-level NMD assumptions (deposit study).
    """

    curves: CurveSet
    fx: FxRates
    positions: pl.DataFrame | None = None
    cashflows: pl.DataFrame | None = None
    curve_quotes: pl.DataFrame | None = None
    previous_quotes: pl.DataFrame | None = None
    nmd_assumptions: pl.DataFrame | None = None

    @classmethod
    def from_frames(
        cls,
        config: RunConfig,
        curve_quotes: pl.DataFrame,
        fx_rates: pl.DataFrame,
        positions: pl.DataFrame | None = None,
        cashflows: pl.DataFrame | None = None,
        previous_quotes: pl.DataFrame | None = None,
        nmd_assumptions: pl.DataFrame | None = None,
    ) -> RunInputs:
        """Build inputs from raw frames, bootstrapping the curves.

        Args:
            config: Run configuration.
            curve_quotes: Raw curve quotes.
            fx_rates: Spot FX frame.
            positions: Positions frame.
            cashflows: External cash-flow frame.
            previous_quotes: Raw quotes of a previous date for comparison.
            nmd_assumptions: Deposit-study assumptions frame.

        Returns:
            Run inputs.
        """
        quotes, _ = normalise(
            curve_quotes, CURVE_QUOTES_SCHEMA, CURVE_QUOTES_REQUIRED, "curve_quotes"
        )
        prev = None
        if previous_quotes is not None and not previous_quotes.is_empty():
            prev, _ = normalise(
                previous_quotes, CURVE_QUOTES_SCHEMA, CURVE_QUOTES_REQUIRED, "previous_curve_quotes"
            )
        nmd = None
        if nmd_assumptions is not None and not nmd_assumptions.is_empty():
            nmd, _ = normalise(
                nmd_assumptions, NMD_ASSUMPTIONS_SCHEMA, NMD_ASSUMPTIONS_REQUIRED, "nmd_assumptions"
            )
        raw = check_raw_quotes(quotes, config.market_data_checks.max_abs_rate)
        if (
            config.market_data_checks.fail_on_error
            and raw.filter(pl.col("severity") == "ERROR").height
        ):
            raise DataQualityError(raw)
        curves = curves_from_frame(quotes, config.as_of, config.discount_map, config.interpolation)
        return cls(
            curves,
            fx_from_frame(fx_rates, config.reporting_currency),
            positions,
            cashflows,
            quotes,
            prev,
            nmd,
        )


@dataclass
class RunResult:
    """All outputs of a run. Every table is a Polars frame.

    Attributes:
        run_id: Unique run identifier.
        config: Configuration used.
        created_at: UTC timestamp.
        tables: Result tables by name (see :attr:`TABLES`).
        validation: Input validation reports.
        rejected: Rejected positions ``(position_id, reason)``.
        timings: Seconds spent per stage.
    """

    run_id: str
    config: RunConfig
    created_at: datetime
    tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    validation: list[ValidationReport] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    TABLES: tuple[str, ...] = (
        "position_pv",
        "eve",
        "eve_regulatory",
        "eve_slotted",
        "nii",
        "nii_regulatory",
        "nii_positions",
        "gap",
        "pv01",
        "key_rates",
        "outlier_tests",
        "limits",
        "recon_summary",
        "recon_detail",
        "market_data_checks",
        "ndf_settlements",
        "curves",
        "cashflows",
    )

    def __getitem__(self, name: str) -> pl.DataFrame:
        return self.tables[name]

    def summary(self) -> dict[str, Any]:
        """Headline figures of the run.

        Returns:
            JSON-serialisable summary dictionary.
        """

        def worst(df: pl.DataFrame, col: str) -> dict[str, Any] | None:
            if df.is_empty():
                return None
            r = df.sort(col, descending=True).row(0, named=True)
            return {"scenario": r["scenario"], col: r[col]}

        eve_reg = self.tables["eve_regulatory"]
        return {
            "run_id": self.run_id,
            "as_of": self.config.as_of.isoformat(),
            "reporting_currency": self.config.reporting_currency,
            "positions": int(self.tables["position_pv"].get_column("position_id").n_unique()),
            "rejected": len(self.rejected),
            "base_eve": float(
                self.tables["eve"]
                .filter((pl.col("scenario") == "BASE") & (pl.col("method") == "FULL_REVALUATION"))
                .get_column("eve_rep")
                .sum()
            ),
            "worst_eve_full_revaluation": worst(
                eve_reg.filter(pl.col("method") == "FULL_REVALUATION"), "eve_loss"
            ),
            "worst_eve_standardised": worst(
                eve_reg.filter(pl.col("method") == "STANDARDISED"), "eve_loss"
            ),
            "worst_nii": worst(
                self.tables["nii_regulatory"].filter(pl.col("scenario") != "BASE"), "nii_loss"
            ),
            "outlier_tests": self.tables["outlier_tests"].to_dicts(),
            "limits": self.tables["limits"]
            .select(["limit", "worst_scenario", "measure", "utilisation", "status"])
            .to_dicts(),
            "reconciliation": dict(
                self.tables["recon_summary"].group_by("status").len().iter_rows()
            ),
            "data_quality": dict(
                self.tables["market_data_checks"].group_by("severity").len().iter_rows()
            ),
            "timings": self.timings,
        }

    def write(self, directory: Path, fmt: Literal["parquet", "csv"] = "parquet") -> list[Path]:
        """Write every table plus a JSON summary to a directory.

        Args:
            directory: Target directory (created if needed).
            fmt: File format of the tables.

        Returns:
            Paths written.
        """
        directory.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        for name, df in self.tables.items():
            path = directory / f"{name}.{fmt}"
            if fmt == "parquet":
                df.write_parquet(path)
            else:
                df.write_csv(path)
            out.append(path)
        summary = directory / "summary.json"
        summary.write_text(json.dumps(self.summary(), indent=2, default=str))
        out.append(summary)
        return out


class IRRBBEngine:
    """Computes every IRRBB measure for one valuation date.

    Examples:
        >>> engine = IRRBBEngine(RunConfig(as_of=date(2026, 11, 24)))  # doctest: +SKIP
        >>> result = engine.run(inputs)  # doctest: +SKIP
        >>> result["eve_regulatory"]  # doctest: +SKIP
    """

    def __init__(self, config: RunConfig) -> None:
        """Create an engine.

        Args:
            config: Run configuration.
        """
        self.config = config
        self._timings: dict[str, float] = {}

    @contextmanager
    def _timed(self, stage: str) -> Iterator[None]:
        t0 = time.perf_counter()
        yield
        self._timings[stage] = round(time.perf_counter() - t0, 4)
        logger.info("stage %-18s %.3fs", stage, self._timings[stage])

    # ------------------------------------------------------------- stages
    def _portfolio(
        self, inputs: RunInputs, reports: list[ValidationReport]
    ) -> tuple[Portfolio, pl.DataFrame, pl.DataFrame, set[str]]:
        """Build legs and split external flows into valued / reconciled.

        Returns:
            ``(portfolio, valued external flows, reconciliation source flows,
            position ids present in positions)``.
        """
        cfg = self.config
        pf = Portfolio()
        pos_ids: set[str] = set()
        if inputs.positions is not None and not inputs.positions.is_empty():
            pos, rep = normalise(
                inputs.positions, POSITIONS_SCHEMA, POSITIONS_REQUIRED, "positions"
            )
            reports.append(rep)
            pos_ids = set(pos.get_column("position_id").cast(pl.String).to_list())
            overrides = {
                k: NMDAssumption(v.core_share, v.core_avg_maturity, v.beta, v.reprice_months)
                for k, v in cfg.nmd_assumptions.items()
            }
            pf = build_portfolio(
                pos,
                cfg.as_of,
                NMDModel(enforce_caps=cfg.enforce_nmd_caps),
                overrides,
                AssumptionTable(inputs.nmd_assumptions),
                cfg.discount_overrides,
                cfg.ndf_settlement_currencies,
                cfg.ndf_default_settlement or cfg.reporting_currency,
                cfg.ndf_discount_by_pair,
            )
        ext = pl.DataFrame(schema=CASHFLOWS_SCHEMA)
        if inputs.cashflows is not None and not inputs.cashflows.is_empty():
            ext, rep = normalise(
                inputs.cashflows, CASHFLOWS_SCHEMA, CASHFLOWS_REQUIRED, "cashflows"
            )
            reports.append(rep)
            ext = ext.filter(pl.col("pay_date") > cfg.as_of)
        overlap = pl.col("position_id").is_in(list(pos_ids))
        mode = cfg.external_cashflow_mode
        if mode == "VALUE":
            n = ext.filter(overlap).get_column("position_id").n_unique()
            if n:
                logger.warning(
                    "%d position(s) present in both positions and cashflows are "
                    "valued twice (external_cashflow_mode=VALUE)",
                    n,
                )
            valued, recon = ext, ext.clear()
        elif mode == "RECONCILE":
            valued, recon = ext.clear(), ext.filter(overlap)
        else:  # AUTO
            valued, recon = ext.filter(~overlap), ext.filter(overlap)
        return pf, external_flows(valued), recon, pos_ids

    def _quality(self, inputs: RunInputs, pf: Portfolio, ext: pl.DataFrame) -> pl.DataFrame:
        mc = self.config.market_data_checks
        parts = [
            check_coverage(
                pf.eve_legs, ext.get_column("currency").unique().to_list(), inputs.curves, inputs.fx
            )
        ]
        if inputs.curve_quotes is not None:
            parts.append(
                check_quotes(
                    inputs.curve_quotes,
                    inputs.curves,
                    mc.max_abs_rate,
                    mc.required_tenors,
                    mc.max_pillar_jump_bp,
                )
            )
            if inputs.previous_quotes is not None:
                parts.append(
                    compare_quotes(
                        inputs.curve_quotes, inputs.previous_quotes, mc.max_daily_move_bp
                    )
                )
        if pf.assumptions:
            parts.append(
                findings_frame(
                    {
                        "check": "ASSUMPTION",
                        "severity": "WARNING",
                        "curve_name": None,
                        "currency": None,
                        "tenor": None,
                        "detail": f"{pid}: {note}",
                    }
                    for pid, note in pf.assumptions
                )
            )
        findings = pl.concat(parts, how="vertical_relaxed")
        errors = findings.filter(pl.col("severity") == "ERROR")
        coverage_errors = errors.filter(pl.col("check").str.starts_with("COVERAGE"))
        if not coverage_errors.is_empty() or (mc.fail_on_error and not errors.is_empty()):
            raise DataQualityError(findings)
        if not findings.is_empty():
            logger.warning("%d data-quality finding(s) (%d errors)", findings.height, errors.height)
        return findings

    def _reconcile(
        self, pf: Portfolio, recon_src: pl.DataFrame, base_curves: CurveSet
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        cfg = self.config
        ids = set(recon_src.get_column("position_id").unique().to_list())
        legs = [lg for lg in pf.eve_legs if lg.position_id in ids]
        nmd = {lg.position_id for lg in legs if lg.product_type.is_non_maturity_deposit}
        sk = build_skeleton(
            [lg for lg in legs if lg.position_id not in nmd], cfg.as_of, base_curves
        )
        engine = project(cashflow_frame(sk, contractual=True), base_curves, cfg.as_of)
        cols = ["position_id", "currency", "pay_date", "interest", "principal"]
        src = recon_src.with_columns(
            pl.col("interest").fill_null(0.0), pl.col("principal").fill_null(0.0)
        ).select(cols)
        return reconcile_cashflows(
            engine.select(cols),
            src,
            nmd,
            cfg.recon_tolerance_abs,
            cfg.recon_tolerance_rel,
            cfg.recon_interest_tolerance_rel,
        )

    def run(self, inputs: RunInputs) -> RunResult:
        """Execute the full run.

        Args:
            inputs: Market data and portfolio.

        Returns:
            The run result.

        Raises:
            DataQualityError: On blocking coverage / market-data errors.
        """
        cfg = self.config
        as_of = cfg.as_of
        fx = inputs.fx
        base_curves = inputs.curves
        self._timings = {}
        reports: list[ValidationReport] = []
        tables: dict[str, pl.DataFrame] = {}

        with self._timed("portfolio"):
            pf, ext, recon_src, _ = self._portfolio(inputs, reports)
            scenarios = cfg.scenarios()
            eve_scns = [s for s in scenarios if s.for_eve or s.is_base]
            nii_scns = [s for s in scenarios if s.for_nii or s.is_base]
            scen_curves = {s.name: s.apply(base_curves) for s in scenarios}

        with self._timed("data_quality"):
            tables["market_data_checks"] = self._quality(inputs, pf, ext)

        cf_cache: dict[tuple[float, float], pl.DataFrame] = {}
        rp_cache: dict[tuple[float, float], pl.DataFrame] = {}
        pos_parts: list[pl.DataFrame] = []
        flow_parts: list[pl.DataFrame] = []
        rp_parts: list[pl.DataFrame] = []
        ext_rp = ext.select(FLOW_COLUMNS)
        base_flows = pl.DataFrame()
        rp_keys = ["currency", "pay_date"]

        with self._timed("skeleton"):
            skeleton = build_skeleton(pf.eve_legs, as_of, base_curves)
            extrapolation = check_extrapolation(skeleton.periods, ext, base_curves, as_of)
            if not extrapolation.is_empty():
                logger.warning(
                    "%d curve(s) extrapolated beyond their last pillar", extrapolation.height
                )
                tables["market_data_checks"] = pl.concat(
                    [tables["market_data_checks"], extrapolation], how="vertical_relaxed"
                )

        with self._timed("valuation"):
            for s in eve_scns:
                key = (s.prepayment_scalar, s.tdrr_scalar)
                if key not in cf_cache:
                    cf_cache[key] = cashflow_frame(skeleton, *key)
                    rp = pl.concat(
                        [repricing_frame(skeleton, *key).select(FLOW_COLUMNS), ext_rp],
                        how="vertical_relaxed",
                    )
                    rp_cache[key] = rp.group_by(rp_keys).agg(
                        pl.col("interest").sum(),
                        pl.col("principal").sum(),
                        pl.col("cashflow").sum(),
                    )
                curves = scen_curves[s.name]
                flows = project(cf_cache[key], curves, as_of).select(FLOW_COLUMNS)
                flows = discount(pl.concat([flows, ext], how="vertical_relaxed"), curves, as_of)
                flows = flows.with_columns(pl.lit(s.name).alias("scenario"))
                pos_parts.append(
                    flows.group_by(["scenario", *_POS_KEYS], maintain_order=True).agg(
                        pl.col("pv").sum()
                    )
                )
                if cfg.keep_cashflows == "ALL" or (s.is_base and cfg.keep_cashflows != "NONE"):
                    flow_parts.append(flows)
                if s.is_base:
                    base_flows = flows
                rp_parts.append(rp_cache[key].with_columns(pl.lit(s.name).alias("scenario")))
            pos_all = pl.concat(pos_parts, how="vertical_relaxed")
            rp_all = pl.concat(rp_parts, how="vertical_relaxed")

        with self._timed("eve"):
            pos_pv = pos_all.with_columns((pl.col("pv") * fx_column(fx)).alias("pv_rep"))
            tables["position_pv"] = with_delta(pos_pv, _POS_KEYS, ["pv", "pv_rep"])
            eve_full = eve_full_revaluation(pos_all, fx)
            eve_std, slotted = eve_standardised(rp_all, scen_curves, as_of, fx)
            tables["eve"] = pl.concat([eve_full, eve_std], how="vertical_relaxed")
            tables["eve_regulatory"] = regulatory_eve(tables["eve"], cfg.gain_weight)
            tables["eve_slotted"] = slotted

        with self._timed("nii"):
            horizon_end = add_months(as_of, cfg.nii_horizon_months)
            nii_frame = build_nii_frame(pf.nii_legs, as_of, horizon_end, base_curves)
            seg_parts: list[pl.DataFrame] = []
            for s in nii_scns:
                seg = nii_segments(nii_frame, base_curves, scen_curves[s.name], s, as_of)
                seg_parts.append(seg.select(["scenario", *_POS_KEYS, "nii"]))
                seg_parts.append(
                    external_nii(
                        ext, base_curves, scen_curves[s.name], s, as_of, horizon_end
                    ).select(["scenario", *_POS_KEYS, "nii"])
                )
            segs = pl.concat(seg_parts, how="vertical_relaxed")
            nii_pos = (
                segs.group_by(["scenario", *_POS_KEYS], maintain_order=True)
                .agg(pl.col("nii").sum())
                .with_columns((pl.col("nii") * fx_column(fx)).alias("nii_rep"))
            )
            tables["nii_positions"] = with_delta(nii_pos, _POS_KEYS, ["nii", "nii_rep"])
            tables["nii"] = nii_by_currency(segs, fx)
            tables["nii_regulatory"] = regulatory_nii(tables["nii"])

        with self._timed("gap"):
            base_rp = rp_all.filter(pl.col("scenario") == "BASE")
            tables["gap"] = repricing_gap(base_rp, as_of, fx)

        with self._timed("sensitivities"):
            base_sk = cf_cache[(1.0, 1.0)] if (1.0, 1.0) in cf_cache else cashflow_frame(skeleton)
            base_pv = base_flows
            tables["pv01"] = position_pv01(base_sk, ext, base_curves, as_of, fx, base_pv)
            tables["key_rates"] = (
                key_rate_dv01(base_sk, ext, base_curves, as_of, fx, cfg.key_rate_pillars, base_pv)
                if cfg.compute_key_rates
                else pl.DataFrame()
            )

        reg_eve = [s.name for s in scenarios if s.regulatory]
        reg_nii = [n for n in ("PARALLEL_UP", "PARALLEL_DOWN") if n in reg_eve]
        outlier = outlier_tests(
            tables["eve_regulatory"].filter(pl.col("scenario").is_in(reg_eve)),
            tables["nii_regulatory"].filter(pl.col("scenario").is_in(["BASE", *reg_nii])),
            cfg.tier1_capital,
            cfg.eve_sot_threshold,
            cfg.nii_sot_threshold,
        )
        tables["outlier_tests"] = outlier if cfg.outlier_tests else outlier.clear()
        tables["limits"] = evaluate_limits(
            cfg.limits,
            scenarios,
            tables["eve"],
            tables["eve_regulatory"],
            tables["nii"],
            cfg.tier1_capital,
        )
        with self._timed("reconciliation"):
            if recon_src.is_empty():
                tables["recon_summary"] = pl.DataFrame(schema=RECON_SUMMARY_SCHEMA)
                tables["recon_detail"] = pl.DataFrame()
            else:
                tables["recon_summary"], tables["recon_detail"] = self._reconcile(
                    pf, recon_src, base_curves
                )
        tables["ndf_settlements"] = ndf_settlements(
            pf.eve_legs, base_curves, fx, as_of, cfg.ndf_settlement_currencies
        )
        tables["curves"] = self._curve_table(scenarios, scen_curves)
        tables["cashflows"] = (
            pl.concat(flow_parts, how="vertical_relaxed") if flow_parts else pl.DataFrame()
        )

        return RunResult(
            run_id=uuid.uuid4().hex,
            config=cfg,
            created_at=datetime.now(UTC),
            tables=tables,
            validation=reports,
            rejected=pf.rejected,
            timings=dict(self._timings),
        )

    @staticmethod
    def _curve_table(scenarios: list[Scenario], scen_curves: dict[str, CurveSet]) -> pl.DataFrame:
        rows: list[dict[str, object]] = []
        for s in scenarios:
            for c in scen_curves[s.name]:
                z = c.zero_rate(_CURVE_GRID)
                rows.extend(
                    {
                        "scenario": s.name,
                        "curve_name": c.name,
                        "currency": c.currency,
                        "tenor_years": float(t),
                        "zero_rate": float(r),
                    }
                    for t, r in zip(_CURVE_GRID, z, strict=True)
                )
        return pl.DataFrame(rows)


def run_from_frames(
    config: RunConfig,
    curve_quotes: pl.DataFrame,
    fx_rates: pl.DataFrame,
    positions: pl.DataFrame | None = None,
    cashflows: pl.DataFrame | None = None,
) -> RunResult:
    """Convenience wrapper: build inputs and run the engine.

    Args:
        config: Run configuration.
        curve_quotes: Raw curve quotes.
        fx_rates: Spot FX frame.
        positions: Positions frame.
        cashflows: External cash flows.

    Returns:
        The run result.
    """
    inputs = RunInputs.from_frames(config, curve_quotes, fx_rates, positions, cashflows)
    return IRRBBEngine(config).run(inputs)


__all__ = ["IRRBBEngine", "RunInputs", "RunResult", "run_from_frames"]
