"""Run configuration and application settings.

:class:`RunConfig` controls a single measurement run and can be loaded from
YAML. :class:`Settings` holds process-level settings (database URL, data
directories) read from environment variables prefixed ``IRRBB_``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from irrbb.core.enums import Interpolation, LowerBoundPolicy, ProductType
from irrbb.scenarios.shocks import (
    BCBS_SHOCK_SIZES,
    ParallelShock,
    Scenario,
    ShockSize,
    TenorShock,
    standard_scenarios,
    us_scenarios,
)

PROFILE_DIR: Path = Path(__file__).parent / "profiles"
"""Directory of packaged run profiles."""


def list_profiles() -> list[str]:
    """Names of the packaged run profiles.

    Returns:
        Sorted profile names.
    """
    return sorted(p.stem for p in PROFILE_DIR.glob("*.yaml"))


class ShockSizeConfig(BaseModel):
    """Shock size override in basis points."""

    parallel: float
    short: float
    long: float


class CustomScenarioConfig(BaseModel):
    """A user-defined scenario.

    Attributes:
        name: Scenario name (must be unique).
        kind: ``parallel`` (uses ``bp``) or ``tenor`` (uses ``times`` + ``bp_points``).
        bp: Parallel shift in basis points.
        bp_by_currency: Per-currency parallel overrides.
        times: Tenor points (years) for tenor shocks.
        bp_points: Shift at each tenor point (bp).
        currencies: Restrict a tenor shock to these currencies.
        curves: Restrict the shock to these curves (basis risk), e.g.
            ``[USD-TERM-SOFR-3M]`` shocks Term SOFR against SOFR OIS.
        prepayment_scalar: Prepayment scalar for the scenario.
        tdrr_scalar: Term-deposit redemption scalar.
        nmd_beta_scalar: Multiplier on NMD betas.
        description: Free text.
    """

    name: str
    kind: Literal["parallel", "tenor"] = "parallel"
    bp: float = 0.0
    bp_by_currency: dict[str, float] = Field(default_factory=dict)
    times: list[float] = Field(default_factory=list)
    bp_points: list[float] = Field(default_factory=list)
    currencies: list[str] | None = None
    prepayment_scalar: float = 1.0
    tdrr_scalar: float = 1.0
    nmd_beta_scalar: float = 1.0
    ramp_months: int | None = Field(default=None, ge=1)
    measures: list[Literal["EVE", "NII"]] | None = None
    curves: list[str] | None = None
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> CustomScenarioConfig:
        if self.kind == "tenor" and (not self.times or len(self.times) != len(self.bp_points)):
            raise ValueError(f"Scenario {self.name}: 'times' and 'bp_points' must align")
        return self

    def build(self, lower_bound: LowerBoundPolicy) -> Scenario:
        """Create the :class:`~irrbb.scenarios.shocks.Scenario`.

        Args:
            lower_bound: Post-shock lower bound policy.

        Returns:
            The scenario.
        """
        shock = (
            ParallelShock(self.bp, self.bp_by_currency)
            if self.kind == "parallel"
            else TenorShock(
                self.times, self.bp_points, frozenset(self.currencies) if self.currencies else None
            )
        )
        measures = (
            frozenset(self.measures)
            if self.measures
            else frozenset({"NII"})
            if self.ramp_months
            else frozenset({"EVE", "NII"})
        )
        return Scenario(
            self.name,
            shock,
            self.prepayment_scalar,
            self.tdrr_scalar,
            self.nmd_beta_scalar,
            lower_bound,
            self.description,
            family="CUSTOM",
            ramp_months=self.ramp_months,
            measures=measures,
            curves=frozenset(self.curves) if self.curves else None,
        )


class NMDAssumptionConfig(BaseModel):
    """Override of product-level NMD assumptions."""

    core_share: float = Field(ge=0, le=1)
    core_avg_maturity: float = Field(gt=0)
    beta: float = Field(ge=0, le=1.5)
    reprice_months: int = Field(default=1, ge=1)


class USScenarioConfig(BaseModel):
    """US management scenario set (instantaneous parallel shocks and NII ramps).

    Attributes:
        parallel_bp: Instantaneous parallel shock sizes (applied up and down).
        ramp_bp: Gradual (ramp) shock sizes for NII (applied up and down).
        ramp_months: Months over which ramps are phased in.
        behavioural_scalars: Apply directional prepayment / TDRR scalars.
    """

    parallel_bp: list[float] = Field(default_factory=lambda: [100.0, 200.0, 300.0, 400.0])
    ramp_bp: list[float] = Field(default_factory=lambda: [100.0, 200.0, 300.0, 400.0])
    ramp_months: int = Field(default=12, ge=1)
    behavioural_scalars: bool = True


class LimitConfig(BaseModel):
    """A risk limit / early-warning trigger evaluated after each run.

    Attributes:
        name: Limit identifier (e.g. ``"ALCO_EVE_200"``).
        metric: ``EVE`` or ``NII``.
        basis: How the loss is expressed: ``ABSOLUTE`` amount, ``PCT_BASE`` of
            the base EVE / NII, or ``PCT_CAPITAL`` of ``capital``.
        threshold: Limit on the (positive) loss in ``basis`` units, e.g.
            ``0.10`` for 10% of base NII. ``None`` = not yet approved: the
            exposure is measured and reported with status ``NOT_SET``.
        warning_level: Share of the limit that triggers a warning.
        scenarios: Scenario names in scope (``None`` = every applicable one).
        families: Scenario families in scope (e.g. ``["US_PARALLEL"]``).
        method: EVE method (``FULL_REVALUATION`` or ``STANDARDISED``).
        aggregation: ``NET`` sums currencies; ``REGULATORY`` uses the
            no-offset BCBS aggregation (EVE only).
        currency: Restrict to one currency (``None`` = all, in reporting ccy).
        capital: Capital for ``PCT_CAPITAL`` (defaults to ``tier1_capital``).
    """

    name: str
    metric: Literal["EVE", "NII"]
    basis: Literal["ABSOLUTE", "PCT_BASE", "PCT_CAPITAL"] = "PCT_BASE"
    threshold: float | None = Field(default=None, gt=0)
    warning_level: float = Field(default=0.8, gt=0, le=1)
    scenarios: list[str] | None = None
    families: list[str] | None = None
    method: Literal["FULL_REVALUATION", "STANDARDISED"] = "FULL_REVALUATION"
    aggregation: Literal["NET", "REGULATORY"] = "NET"
    currency: str | None = None
    capital: float | None = None


class MarketDataCheckConfig(BaseModel):
    """Thresholds of the market-data quality checks.

    Attributes:
        max_abs_rate: Flag quotes whose absolute value exceeds this (decimal).
        max_pillar_jump_bp: Flag adjacent pillars differing by more than this.
        max_daily_move_bp: Flag day-over-day moves above this (when a previous
            date is available).
        required_tenors: Tenors every discount curve should have.
        fail_on_error: Abort the run on blocking market-data errors.
    """

    max_abs_rate: float = 0.5
    max_pillar_jump_bp: float = 150.0
    max_daily_move_bp: float = 50.0
    required_tenors: list[str] = Field(default_factory=lambda: ["1M", "3M", "1Y", "5Y", "10Y"])
    fail_on_error: bool = True


class RunConfig(BaseModel):
    """Configuration of one IRRBB measurement run.

    Attributes:
        as_of: Valuation date.
        reporting_currency: Currency results are converted to.
        nii_horizon_months: NII horizon (12 months under BCBS 368).
        lower_bound: Post-shock lower bound policy.
        standard_scenarios: Include the six BCBS 368 scenarios.
        behavioural_scalars: Apply BCBS prepayment / TDRR scalars.
        shock_sizes: Per-currency overrides of the BCBS shock sizes (bp).
        custom_scenarios: Additional management / stress scenarios.
        gain_weight: Weight of gains when aggregating ΔEVE across currencies
            (0 under BCBS 368, 0.5 under EBA GL/2022/14).
        tier1_capital: Tier 1 capital in reporting currency (outlier test).
        outlier_tests: Compute the BCBS 368 supervisory outlier test (a Basel
            concept; US supervisors use bank-defined limits instead).
        eve_sot_threshold: EVE outlier threshold as a share of Tier 1.
        nii_sot_threshold: NII outlier threshold as a share of Tier 1.
        key_rate_pillars: Pillars (years) for key-rate DV01.
        compute_key_rates: Whether to compute key-rate DV01.
        interpolation: Curve interpolation.
        discount_map: Currency → discount curve name overrides.
        discount_overrides: Product → currency → discount curve for legs that
            must not use the currency's default curve, e.g.
            ``{NDF: {BRL: BRL-NDF-IMPLIED}}``. A ``discount_curve`` on the
            position row takes precedence.
        ndf_settlement_currencies: Allowed NDF settlement currencies; NDFs
            settling in any other currency are rejected.
        ndf_default_settlement: Settlement currency assumed for NDFs without
            one (``None`` = the reporting currency). Each assumption is
            reported as a data-quality warning.
        ndf_discount_by_pair: Currency pair, written other + non-deliverable
            (``"EURBRL"``), → discount curve of the non-deliverable leg. Hook
            for a EUR-based BRL implied curve; a position-level
            ``discount_curve`` still wins.
        enforce_nmd_caps: Clip NMD assumptions to BCBS caps.
        nmd_assumptions: Product-level NMD assumption overrides.
        keep_cashflows: Cash flows kept in the result: none, base scenario only, or
            all scenarios (memory heavy for large books).
        profile: Name of the profile the configuration was built from.
        us_scenarios: US management scenarios (``None`` = not run).
        limits: Limits and early-warning triggers.
        external_cashflow_mode: Use of external cash flows: ``AUTO`` values
            only positions absent from ``positions`` and reconciles the
            others; ``VALUE`` values all of them; ``RECONCILE`` values none.
        recon_tolerance_abs: Absolute tolerance for reconciliation breaks.
        recon_tolerance_rel: Relative tolerance on principal.
        recon_interest_tolerance_rel: Relative tolerance on interest (looser,
            floating coupons are projected differently by each system).
        market_data_checks: Market-data quality thresholds.
    """

    as_of: date
    reporting_currency: str = "USD"
    nii_horizon_months: int = Field(default=12, ge=1, le=120)
    lower_bound: LowerBoundPolicy = LowerBoundPolicy.ZERO
    standard_scenarios: bool = True
    behavioural_scalars: bool = True
    shock_sizes: dict[str, ShockSizeConfig] = Field(default_factory=dict)
    custom_scenarios: list[CustomScenarioConfig] = Field(default_factory=list)
    gain_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    tier1_capital: float | None = None
    outlier_tests: bool = True
    eve_sot_threshold: float = 0.15
    nii_sot_threshold: float = 0.05
    key_rate_pillars: list[float] = Field(
        default_factory=lambda: [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30]
    )
    compute_key_rates: bool = True
    interpolation: Interpolation = Interpolation.LINEAR_ZERO
    discount_map: dict[str, str] = Field(default_factory=dict)
    discount_overrides: dict[ProductType, dict[str, str]] = Field(default_factory=dict)
    ndf_settlement_currencies: list[str] = Field(default_factory=lambda: ["USD", "EUR"])
    ndf_default_settlement: str | None = None
    ndf_discount_by_pair: dict[str, str] = Field(default_factory=dict)
    enforce_nmd_caps: bool = True
    nmd_assumptions: dict[ProductType, NMDAssumptionConfig] = Field(default_factory=dict)
    keep_cashflows: Literal["NONE", "BASE", "ALL"] = "BASE"
    profile: str | None = None
    us_scenarios: USScenarioConfig | None = None
    limits: list[LimitConfig] = Field(default_factory=list)
    external_cashflow_mode: Literal["AUTO", "VALUE", "RECONCILE"] = "AUTO"
    recon_tolerance_abs: float = Field(default=1.0, ge=0)
    recon_tolerance_rel: float = Field(default=1e-4, ge=0)
    recon_interest_tolerance_rel: float = Field(default=0.02, ge=0)
    market_data_checks: MarketDataCheckConfig = Field(default_factory=MarketDataCheckConfig)

    @field_validator("ndf_discount_by_pair")
    @classmethod
    def _check_pairs(cls, v: dict[str, str]) -> dict[str, str]:
        bad = [k for k in v if len(k) != 6 or not k.isalpha()]
        if bad:
            raise ValueError(f"ndf_discount_by_pair keys must look like 'EURBRL', got {bad}")
        return {k.upper(): c for k, c in v.items()}

    @classmethod
    def from_yaml(cls, path: Path, **overrides: object) -> RunConfig:
        """Load a configuration from a YAML file.

        Args:
            path: YAML file path.
            **overrides: Values overriding the file (e.g. ``as_of``).

        Returns:
            The configuration.
        """
        data = yaml.safe_load(path.read_text()) or {}
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(data)

    @classmethod
    def from_profile(cls, name: str, **overrides: object) -> RunConfig:
        """Load a packaged profile (see :func:`list_profiles`).

        Args:
            name: Profile name, e.g. ``"us_bank"``.
            **overrides: Values overriding the profile.

        Returns:
            The configuration.

        Raises:
            ValueError: For an unknown profile.
        """
        path = PROFILE_DIR / f"{name}.yaml"
        if not path.exists():
            raise ValueError(f"Unknown profile {name!r}; available: {list_profiles()}")
        return cls.from_yaml(path, profile=name, **overrides)

    def scenarios(self) -> list[Scenario]:
        """Materialise all scenarios of the run (base first).

        Returns:
            Ordered scenarios.

        Raises:
            ValueError: If scenario names are not unique.
        """
        sizes = dict(BCBS_SHOCK_SIZES)
        sizes.update(
            {
                c: ShockSize(s.parallel / 1e4, s.short / 1e4, s.long / 1e4)
                for c, s in self.shock_sizes.items()
            }
        )
        out = standard_scenarios(sizes, self.lower_bound, True, self.behavioural_scalars)
        if not self.standard_scenarios:
            out = out[:1]
        if self.us_scenarios is not None:
            u = self.us_scenarios
            out += us_scenarios(
                u.parallel_bp, u.ramp_bp, u.ramp_months, self.lower_bound, u.behavioural_scalars
            )
        out += [c.build(self.lower_bound) for c in self.custom_scenarios]
        names = [s.name for s in out]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate scenario names: {names}")
        return out


class Settings(BaseSettings):
    """Process-level settings (env vars ``IRRBB_*`` or a ``.env`` file).

    Attributes:
        database_url: SQLAlchemy URL. SQLite by default for development; production
            uses PostgreSQL, e.g. ``postgresql+psycopg://irrbb:***@host:5432/irrbb``.
        data_dir: Default directory for file-based inputs.
        output_dir: Default directory for file-based outputs.
        api_title: Title shown in the OpenAPI docs.
    """

    model_config = SettingsConfigDict(env_prefix="IRRBB_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///irrbb.db"
    data_dir: Path = Path("data")
    output_dir: Path = Path("output")
    api_title: str = "IRRBB Engine API"


def get_settings() -> Settings:
    """Return process settings.

    Returns:
        A fresh :class:`Settings` instance.
    """
    return Settings()
