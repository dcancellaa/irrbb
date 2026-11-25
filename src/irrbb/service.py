"""Application services shared by the CLI and the REST API."""

from __future__ import annotations

import logging
from datetime import date

from irrbb.config import RunConfig
from irrbb.data.sources import DataSource
from irrbb.engine.runner import IRRBBEngine, RunInputs, RunResult

logger = logging.getLogger(__name__)


class MissingInputError(RuntimeError):
    """Raised when a mandatory dataset is unavailable for a date."""


def run_from_source(
    source: DataSource, config: RunConfig, compare_to: date | None = None
) -> RunResult:
    """Load inputs from a data source and run the engine.

    Curve quotes and FX rates are mandatory; at least one of positions or
    cash flows must be present. NMD assumptions are optional.

    Args:
        source: Data source (files, database, custom adapter...).
        config: Run configuration.
        compare_to: Previous date whose curve quotes are compared with the
            run date's (day-over-day market-data check).

    Returns:
        The run result.

    Raises:
        MissingInputError: If mandatory inputs are missing.
    """
    as_of: date = config.as_of
    quotes = source.load("curve_quotes", as_of)
    fx = source.load("fx_rates", as_of)
    positions = source.load("positions", as_of)
    cashflows = source.load("cashflows", as_of)
    if quotes is None:
        raise MissingInputError(f"No curve quotes available for {as_of}")
    if fx is None:
        raise MissingInputError(f"No FX rates available for {as_of}")
    if positions is None and cashflows is None:
        raise MissingInputError(f"No positions or cash flows available for {as_of}")
    previous = source.load("curve_quotes", compare_to) if compare_to else None
    nmd = source.load("nmd_assumptions", as_of)
    inputs = RunInputs.from_frames(config, quotes, fx, positions, cashflows, previous, nmd)
    result = IRRBBEngine(config).run(inputs)
    logger.info("Run %s completed for %s", result.run_id, as_of)
    return result
