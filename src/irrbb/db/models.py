"""SQLAlchemy 2.0 ORM models.

Two families of tables:

* **Staging (inputs)** — data received from upstream systems, partitioned by
  ``as_of_date``: ``stg_position``, ``stg_cashflow``, ``stg_curve_quote``,
  ``stg_fx_rate``.
* **Results** — outputs of each run, keyed by ``run_id``: ``run`` plus one
  ``res_*`` table per result frame.

Schema changes are managed with Alembic (``migrations/``).

Target database: PostgreSQL (19). SQLite is supported for development and
tests. Amounts use ``Double`` (``DOUBLE PRECISION``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Boolean, Date, DateTime, Double, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all IRRBB tables."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON}


# ------------------------------------------------------------------ staging
class StgPosition(Base):
    """One leg of a position, as delivered by a source system."""

    __tablename__ = "stg_position"
    __table_args__ = (Index("ix_stg_position_asof", "as_of_date", "position_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    source_system: Mapped[str | None] = mapped_column(String(64))
    position_id: Mapped[str] = mapped_column(String(128))
    leg_id: Mapped[str | None] = mapped_column(String(32))
    product_type: Mapped[str] = mapped_column(String(32))
    book: Mapped[str | None] = mapped_column(String(64))
    counterparty: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))
    direction: Mapped[str | None] = mapped_column(String(8))
    notional: Mapped[float] = mapped_column(Double)
    start_date: Mapped[date | None] = mapped_column(Date)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    rate_type: Mapped[str | None] = mapped_column(String(16))
    fixed_rate: Mapped[float | None] = mapped_column(Double)
    index: Mapped[str | None] = mapped_column(String(64))
    spread: Mapped[float | None] = mapped_column(Double)
    payment_frequency: Mapped[str | None] = mapped_column(String(8))
    day_count: Mapped[str | None] = mapped_column(String(16))
    amortization: Mapped[str | None] = mapped_column(String(16))
    initial_exchange: Mapped[bool | None] = mapped_column(Boolean)
    final_exchange: Mapped[bool | None] = mapped_column(Boolean)
    current_rate: Mapped[float | None] = mapped_column(Double)
    runoff_rate: Mapped[float | None] = mapped_column(Double)
    nmd_category: Mapped[str | None] = mapped_column(String(32))
    core_share: Mapped[float | None] = mapped_column(Double)
    core_avg_maturity: Mapped[float | None] = mapped_column(Double)
    beta: Mapped[float | None] = mapped_column(Double)
    settlement_currency: Mapped[str | None] = mapped_column(String(3))
    discount_curve: Mapped[str | None] = mapped_column(String(64))


class StgCashflow(Base):
    """An externally computed future cash flow."""

    __tablename__ = "stg_cashflow"
    __table_args__ = (Index("ix_stg_cashflow_asof", "as_of_date", "position_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    source_system: Mapped[str | None] = mapped_column(String(64))
    position_id: Mapped[str] = mapped_column(String(128))
    product_type: Mapped[str | None] = mapped_column(String(32))
    book: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    pay_date: Mapped[date] = mapped_column(Date)
    interest: Mapped[float | None] = mapped_column(Double)
    principal: Mapped[float | None] = mapped_column(Double)
    rate_type: Mapped[str | None] = mapped_column(String(16))
    index: Mapped[str | None] = mapped_column(String(64))


class StgCurveQuote(Base):
    """A raw market quote of a curve."""

    __tablename__ = "stg_curve_quote"
    __table_args__ = (Index("ix_stg_curve_asof", "as_of_date", "curve_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    source_system: Mapped[str | None] = mapped_column(String(64))
    curve_name: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    tenor: Mapped[str] = mapped_column(String(8))
    quote_type: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Double)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    day_count: Mapped[str | None] = mapped_column(String(16))
    is_discount: Mapped[bool | None] = mapped_column(Boolean)
    base_curve: Mapped[str | None] = mapped_column(String(64))


class StgFxRate(Base):
    """Spot FX rate against the reporting currency."""

    __tablename__ = "stg_fx_rate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    source_system: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[float] = mapped_column(Double)


class StgNmdAssumption(Base):
    """Deposit-study NMD assumptions, effective from ``as_of_date``."""

    __tablename__ = "stg_nmd_assumption"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    source_system: Mapped[str | None] = mapped_column(String(64))
    product_type: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(3))
    nmd_category: Mapped[str | None] = mapped_column(String(32))
    core_share: Mapped[float] = mapped_column(Double)
    core_avg_maturity: Mapped[float] = mapped_column(Double)
    beta: Mapped[float] = mapped_column(Double)
    reprice_months: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(255))


# ------------------------------------------------------------------ results
class Run(Base):
    """Metadata of one measurement run."""

    __tablename__ = "run"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="COMPLETED")
    reporting_currency: Mapped[str] = mapped_column(String(3))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    rejected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class _RunScoped:
    """Mixin adding a surrogate key and ``run_id`` foreign key."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("run.run_id", ondelete="CASCADE"), index=True
    )


class ResEve(_RunScoped, Base):
    """EVE by method, scenario and currency."""

    __tablename__ = "res_eve"
    method: Mapped[str] = mapped_column(String(32))
    scenario: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    eve: Mapped[float] = mapped_column(Double)
    eve_rep: Mapped[float | None] = mapped_column(Double)
    delta_eve: Mapped[float] = mapped_column(Double)
    delta_eve_rep: Mapped[float | None] = mapped_column(Double)


class ResEveRegulatory(_RunScoped, Base):
    """Cross-currency regulatory ΔEVE loss per scenario."""

    __tablename__ = "res_eve_regulatory"
    method: Mapped[str] = mapped_column(String(32))
    scenario: Mapped[str] = mapped_column(String(64))
    eve_loss: Mapped[float] = mapped_column(Double)
    delta_eve_rep_net: Mapped[float] = mapped_column(Double)
    base_eve_rep: Mapped[float | None] = mapped_column(Double)
    is_worst: Mapped[bool] = mapped_column(Boolean)


class ResNii(_RunScoped, Base):
    """NII by scenario and currency."""

    __tablename__ = "res_nii"
    scenario: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    nii: Mapped[float] = mapped_column(Double)
    nii_rep: Mapped[float | None] = mapped_column(Double)
    delta_nii: Mapped[float] = mapped_column(Double)
    delta_nii_rep: Mapped[float | None] = mapped_column(Double)


class ResNiiRegulatory(_RunScoped, Base):
    """Total ΔNII per scenario."""

    __tablename__ = "res_nii_regulatory"
    scenario: Mapped[str] = mapped_column(String(64))
    nii_rep: Mapped[float | None] = mapped_column(Double)
    delta_nii_rep: Mapped[float | None] = mapped_column(Double)
    nii_loss: Mapped[float | None] = mapped_column(Double)
    is_worst: Mapped[bool] = mapped_column(Boolean)


class ResPositionPv(_RunScoped, Base):
    """MtM / PV per position, currency and scenario."""

    __tablename__ = "res_position_pv"
    scenario: Mapped[str] = mapped_column(String(64))
    position_id: Mapped[str] = mapped_column(String(128), index=True)
    product_type: Mapped[str] = mapped_column(String(32))
    book: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    pv: Mapped[float] = mapped_column(Double)
    pv_rep: Mapped[float | None] = mapped_column(Double)
    delta_pv: Mapped[float] = mapped_column(Double)
    delta_pv_rep: Mapped[float | None] = mapped_column(Double)


class ResGap(_RunScoped, Base):
    """Repricing gap per currency and time bucket."""

    __tablename__ = "res_gap"
    currency: Mapped[str] = mapped_column(String(3))
    bucket: Mapped[int] = mapped_column(Integer)
    bucket_label: Mapped[str] = mapped_column(String(16))
    assets: Mapped[float] = mapped_column(Double)
    liabilities: Mapped[float] = mapped_column(Double)
    gap: Mapped[float] = mapped_column(Double)
    cumulative_gap: Mapped[float] = mapped_column(Double)
    gap_rep: Mapped[float | None] = mapped_column(Double)
    cumulative_gap_rep: Mapped[float | None] = mapped_column(Double)


class ResKeyRate(_RunScoped, Base):
    """Key-rate DV01 per currency and pillar."""

    __tablename__ = "res_key_rate"
    currency: Mapped[str] = mapped_column(String(3))
    pillar: Mapped[float] = mapped_column(Double)
    dv01: Mapped[float] = mapped_column(Double)
    dv01_rep: Mapped[float | None] = mapped_column(Double)


class ResPv01(_RunScoped, Base):
    """Parallel PV01 per position and currency."""

    __tablename__ = "res_pv01"
    position_id: Mapped[str] = mapped_column(String(128))
    product_type: Mapped[str] = mapped_column(String(32))
    book: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    pv01: Mapped[float] = mapped_column(Double)
    pv01_rep: Mapped[float | None] = mapped_column(Double)


class ResOutlierTest(_RunScoped, Base):
    """Supervisory outlier test outcomes."""

    __tablename__ = "res_outlier_test"
    test: Mapped[str] = mapped_column(String(8))
    method: Mapped[str] = mapped_column(String(32))
    worst_scenario: Mapped[str] = mapped_column(String(64))
    loss: Mapped[float] = mapped_column(Double)
    threshold: Mapped[float] = mapped_column(Double)
    tier1_capital: Mapped[float | None] = mapped_column(Double)
    ratio: Mapped[float | None] = mapped_column(Double)
    breach: Mapped[bool | None] = mapped_column(Boolean)


class ResLimit(_RunScoped, Base):
    """Limit / early-warning utilisation."""

    __tablename__ = "res_limit"
    limit: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str] = mapped_column(String(8))
    basis: Mapped[str] = mapped_column(String(16))
    worst_scenario: Mapped[str | None] = mapped_column(String(64))
    loss: Mapped[float | None] = mapped_column(Double)
    base_value: Mapped[float | None] = mapped_column(Double)
    measure: Mapped[float | None] = mapped_column(Double)
    threshold: Mapped[float | None] = mapped_column(Double)
    utilisation: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(16))


class ResReconSummary(_RunScoped, Base):
    """Engine vs source cash-flow reconciliation per position."""

    __tablename__ = "res_recon_summary"
    position_id: Mapped[str] = mapped_column(String(128), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    engine_principal: Mapped[float] = mapped_column(Double)
    source_principal: Mapped[float] = mapped_column(Double)
    principal_diff: Mapped[float] = mapped_column(Double)
    max_month_principal_diff: Mapped[float] = mapped_column(Double)
    engine_interest: Mapped[float] = mapped_column(Double)
    source_interest: Mapped[float] = mapped_column(Double)
    interest_diff: Mapped[float] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(32))


class ResMarketDataCheck(_RunScoped, Base):
    """Data-quality findings of the run."""

    __tablename__ = "res_market_data_check"
    check: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    curve_name: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(3))
    tenor: Mapped[str | None] = mapped_column(String(8))
    detail: Mapped[str] = mapped_column(String(512))


class ResNdfSettlement(_RunScoped, Base):
    """Expected NDF settlement amounts in the settlement currency."""

    __tablename__ = "res_ndf_settlement"
    position_id: Mapped[str] = mapped_column(String(128), index=True)
    book: Mapped[str | None] = mapped_column(String(64))
    settlement_currency: Mapped[str] = mapped_column(String(3))
    settlement_date: Mapped[date] = mapped_column(Date)
    nd_currency: Mapped[str] = mapped_column(String(3))
    other_currency: Mapped[str] = mapped_column(String(3))
    nd_notional: Mapped[float] = mapped_column(Double)
    other_notional: Mapped[float] = mapped_column(Double)
    contract_rate: Mapped[float | None] = mapped_column(Double)
    market_forward: Mapped[float] = mapped_column(Double)
    settlement_amount: Mapped[float] = mapped_column(Double)
    pv_settlement_ccy: Mapped[float] = mapped_column(Double)
    pv_rep: Mapped[float] = mapped_column(Double)


#: Staging dataset name → ORM model.
STAGING_MODELS: dict[str, type[Base]] = {
    "positions": StgPosition,
    "cashflows": StgCashflow,
    "curve_quotes": StgCurveQuote,
    "fx_rates": StgFxRate,
    "nmd_assumptions": StgNmdAssumption,
}

#: Result table name (as in :attr:`RunResult.TABLES`) → ORM model.
RESULT_MODELS: dict[str, type[Base]] = {
    "eve": ResEve,
    "eve_regulatory": ResEveRegulatory,
    "nii": ResNii,
    "nii_regulatory": ResNiiRegulatory,
    "position_pv": ResPositionPv,
    "gap": ResGap,
    "key_rates": ResKeyRate,
    "pv01": ResPv01,
    "outlier_tests": ResOutlierTest,
    "limits": ResLimit,
    "recon_summary": ResReconSummary,
    "market_data_checks": ResMarketDataCheck,
    "ndf_settlements": ResNdfSettlement,
}
