"""Pydantic request / response models of the REST API."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from irrbb.config import CustomScenarioConfig
from irrbb.core.enums import LowerBoundPolicy


class Health(BaseModel):
    """Health-check response."""

    status: str = "ok"
    version: str
    database: str


class StageResponse(BaseModel):
    """Result of staging a dataset."""

    dataset: str
    as_of: date
    rows: int
    warnings: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    """Parameters of a run triggered through the API.

    Data is taken from the staging tables for ``as_of``. Any field left out
    uses the :class:`~irrbb.config.RunConfig` default.
    """

    as_of: date
    profile: str | None = Field(default=None, description="Packaged profile, e.g. us_bank")
    compare_to: date | None = Field(default=None, description="Previous date for DoD checks")
    reporting_currency: str = "USD"
    tier1_capital: float | None = None
    lower_bound: LowerBoundPolicy = LowerBoundPolicy.ZERO
    gain_weight: float = Field(default=0.0, ge=0, le=1)
    nii_horizon_months: int = Field(default=12, ge=1, le=120)
    custom_scenarios: list[CustomScenarioConfig] = Field(default_factory=list)
    compute_key_rates: bool = True
    persist: bool = True


class RunSummary(BaseModel):
    """Headline figures of a run."""

    run_id: str
    summary: dict[str, Any]


class RunListItem(BaseModel):
    """One entry of the run list."""

    run_id: str
    as_of: str
    created_at: str
    status: str
    reporting_currency: str


class CurveRequest(BaseModel):
    """Raw quotes to bootstrap (market-data validation helper)."""

    as_of: date
    quotes: list[dict[str, Any]]


class CurvePoint(BaseModel):
    """A zero rate at a tenor."""

    curve_name: str
    tenor_years: float
    zero_rate: float
    discount_factor: float
