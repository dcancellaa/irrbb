"""Build :class:`~irrbb.instruments.leg.Leg` objects from normalised position rows.

The factory applies product-specific defaults (direction, rate type, exchange
flags, behavioural kind) so that source systems only need to send what they
actually know, and routes non-maturity deposits through the NMD model.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Any

import polars as pl

from irrbb.behavioral.nmd import (
    DEFAULT_NMD_ASSUMPTIONS,
    DEFAULT_NMD_CATEGORY,
    AssumptionTable,
    NMDAssumption,
    NMDModel,
)
from irrbb.core.enums import (
    Amortization,
    BehaviouralKind,
    DayCount,
    Direction,
    Frequency,
    NMDCategory,
    ProductType,
    RateType,
)
from irrbb.instruments.leg import Leg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductDefaults:
    """Defaults applied when a source field is null.

    Attributes:
        direction: Default direction.
        rate_type: Default rate type.
        payment_frequency: Default payment frequency.
        amortization: Default amortisation.
        initial_exchange: Default initial exchange flag.
        final_exchange: Default final exchange flag.
        behavioural_kind: Which scalar family scales the run-off rate.
    """

    direction: Direction
    rate_type: RateType = RateType.FIXED
    payment_frequency: Frequency = Frequency.ZERO
    amortization: Amortization = Amortization.BULLET
    initial_exchange: bool = False
    final_exchange: bool = True
    behavioural_kind: BehaviouralKind = BehaviouralKind.NONE


PRODUCT_DEFAULTS: dict[ProductType, ProductDefaults] = {
    ProductType.TERM_DEPOSIT: ProductDefaults(
        Direction.PAY, behavioural_kind=BehaviouralKind.EARLY_REDEMPTION
    ),
    ProductType.LOAN: ProductDefaults(
        Direction.RECEIVE,
        payment_frequency=Frequency.MONTHLY,
        behavioural_kind=BehaviouralKind.PREPAYMENT,
    ),
    ProductType.MORTGAGE: ProductDefaults(
        Direction.RECEIVE,
        payment_frequency=Frequency.MONTHLY,
        amortization=Amortization.ANNUITY,
        behavioural_kind=BehaviouralKind.PREPAYMENT,
    ),
    ProductType.CREDIT_CARD: ProductDefaults(
        Direction.RECEIVE,
        rate_type=RateType.ADMINISTERED,
        payment_frequency=Frequency.MONTHLY,
        behavioural_kind=BehaviouralKind.PREPAYMENT,
    ),
    ProductType.BOND: ProductDefaults(Direction.RECEIVE, payment_frequency=Frequency.SEMIANNUAL),
    ProductType.IRS: ProductDefaults(
        Direction.RECEIVE, payment_frequency=Frequency.QUARTERLY, final_exchange=False
    ),
    ProductType.CCS: ProductDefaults(
        Direction.RECEIVE, payment_frequency=Frequency.QUARTERLY, initial_exchange=True
    ),
    ProductType.FX_FORWARD: ProductDefaults(Direction.RECEIVE, rate_type=RateType.NONE),
    ProductType.NDF: ProductDefaults(Direction.RECEIVE, rate_type=RateType.NONE),
    ProductType.FX_SWAP: ProductDefaults(
        Direction.RECEIVE, rate_type=RateType.NONE, initial_exchange=True
    ),
}
"""Per-product defaults applied when a source field is null."""


@dataclass
class Portfolio:
    """Legs of a portfolio split by purpose.

    Attributes:
        eve_legs: Legs used for cash flows, MtM, EVE and gap (behavioural view).
        nii_legs: Legs used for NII (constant balance sheet view).
        rejected: ``(position_id, reason)`` for rows that could not be built.
        assumptions: ``(position_id, note)`` for defaults applied to a position
            (e.g. an NDF without settlement currency).
    """

    eve_legs: list[Leg] = field(default_factory=list)
    nii_legs: list[Leg] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    assumptions: list[tuple[str, str]] = field(default_factory=list)

    @property
    def position_ids(self) -> set[str]:
        """set[str]: Distinct position identifiers."""
        return {leg.position_id for leg in self.eve_legs}


def _enum[E: StrEnum](cls: type[E], value: Any, default: E) -> E:
    if value is None or str(value).strip() == "":
        return default
    return cls(str(value).strip().upper())


def _f(value: Any, default: float = 0.0) -> float:
    return default if value is None else float(value)


def leg_from_row(
    row: dict[str, Any],
    as_of: date,
    discount_overrides: dict[ProductType, dict[str, str]] | None = None,
) -> Leg:
    """Build a single (non-NMD) leg from a normalised position row.

    Args:
        row: Row of the normalised positions frame.
        as_of: Valuation date.
        discount_overrides: Product → currency → discount curve defaults
            (used when the row has no ``discount_curve``).

    Returns:
        The leg.

    Raises:
        ValueError: If mandatory contract fields are missing.
    """
    product = ProductType(str(row["product_type"]).upper())
    d = PRODUCT_DEFAULTS.get(product, ProductDefaults(Direction.RECEIVE))
    maturity = row.get("maturity_date")
    if maturity is None:
        raise ValueError("maturity_date is required for non-NMD products")
    start = row.get("start_date") or as_of
    rate_type = _enum(RateType, row.get("rate_type"), d.rate_type)
    index = row.get("index")
    if rate_type is RateType.FLOAT and not index:
        logger.warning(
            "%s: floating leg without index, falling back to discount curve", row["position_id"]
        )
    init_x = row.get("initial_exchange")
    final_x = row.get("final_exchange")
    return Leg(
        position_id=str(row["position_id"]),
        leg_id=str(row.get("leg_id") or "1"),
        product_type=product,
        currency=str(row["currency"]),
        direction=_enum(Direction, row.get("direction"), d.direction),
        notional=abs(float(row["notional"])),
        start_date=start,
        maturity_date=maturity,
        rate_type=rate_type,
        fixed_rate=_f(row.get("fixed_rate")),
        index=index,
        spread=_f(row.get("spread")),
        payment_frequency=_enum(Frequency, row.get("payment_frequency"), d.payment_frequency),
        day_count=_enum(DayCount, row.get("day_count"), DayCount.ACT_360),
        amortization=_enum(Amortization, row.get("amortization"), d.amortization),
        initial_exchange=d.initial_exchange if init_x is None else bool(init_x),
        final_exchange=d.final_exchange if final_x is None else bool(final_x),
        current_rate=row.get("current_rate"),
        behavioural_kind=d.behavioural_kind,
        runoff_rate=_f(row.get("runoff_rate")),
        beta=_f(row.get("beta"), 1.0),
        book=str(row.get("book") or "BANKING"),
        discount_curve=(
            row.get("discount_curve")
            or (discount_overrides or {}).get(product, {}).get(str(row["currency"]))
        ),
        settlement_currency=(
            str(row["settlement_currency"]).strip().upper()
            if row.get("settlement_currency")
            else None
        ),
        attributes={
            **{k: row[k] for k in ("counterparty",) if row.get(k)},
            "explicit_discount_curve": bool(row.get("discount_curve")),
        },
    )


def _validate_ndfs(
    pf: Portfolio,
    allowed: Sequence[str],
    default: str,
    by_pair: Mapping[str, str] | None = None,
) -> None:
    """Check NDF structure, resolve settlement currency and pair-level curves.

    An NDF must have exactly two legs in two different currencies and one
    settlement currency from ``allowed``. Positions that fail are rejected
    (all their legs removed); a missing settlement currency is defaulted and
    recorded in :attr:`Portfolio.assumptions`.

    ``by_pair`` maps a currency pair written *other* + *non-deliverable*
    (e.g. ``"EURBRL"``) to the discount curve of the non-deliverable leg. It
    applies unless the position row set ``discount_curve`` explicitly.
    """
    ndf: dict[str, list[Leg]] = {}
    for leg in pf.eve_legs:
        if leg.product_type is ProductType.NDF:
            ndf.setdefault(leg.position_id, []).append(leg)
    if not ndf:
        return
    ok = {c.upper() for c in allowed}
    bad: dict[str, str] = {}
    resolved: dict[str, str] = {}
    for pid, legs in ndf.items():
        stated = {lg.settlement_currency for lg in legs if lg.settlement_currency}
        if len(legs) != 2 or len({lg.currency for lg in legs}) != 2:
            bad[pid] = "NDF must have two legs in two different currencies"
        elif len(stated) > 1:
            bad[pid] = f"NDF legs disagree on settlement currency {sorted(stated)}"
        elif stated and next(iter(stated)) not in ok:
            bad[pid] = f"NDF settlement currency {next(iter(stated))} not in {sorted(ok)}"
        else:
            if not stated:
                pf.assumptions.append((pid, f"settlement currency not provided; {default} assumed"))
            resolved[pid] = next(iter(stated)) if stated else default

    pair_curve: dict[tuple[str, str], str] = {}  # (position, nd currency) -> curve
    for pid, legs in ndf.items():
        if pid in bad or len(legs) != 2:
            continue
        a, b = legs
        for nd, other in ((a, b), (b, a)):
            curve = (by_pair or {}).get(f"{other.currency}{nd.currency}")
            if curve and not nd.attributes.get("explicit_discount_curve"):
                pair_curve[(pid, nd.currency)] = curve

    def fix(legs: list[Leg]) -> list[Leg]:
        out = []
        for lg in legs:
            if lg.position_id in bad:
                continue
            if lg.position_id in resolved:
                lg = replace(lg, settlement_currency=resolved[lg.position_id])
            curve = pair_curve.get((lg.position_id, lg.currency))
            if curve:
                lg = replace(lg, discount_curve=curve)
            out.append(lg)
        return out

    pf.eve_legs = fix(pf.eve_legs)
    pf.nii_legs = fix(pf.nii_legs)
    pf.rejected.extend(bad.items())


def build_portfolio(
    positions: pl.DataFrame,
    as_of: date,
    nmd_model: NMDModel | None = None,
    nmd_assumptions: dict[ProductType, NMDAssumption] | None = None,
    assumption_table: AssumptionTable | None = None,
    discount_overrides: dict[ProductType, dict[str, str]] | None = None,
    ndf_settlement_currencies: Sequence[str] = ("USD", "EUR"),
    ndf_default_settlement: str = "USD",
    ndf_discount_by_pair: Mapping[str, str] | None = None,
) -> Portfolio:
    """Convert a normalised positions frame into a :class:`Portfolio`.

    Rows with errors are rejected (and reported) rather than failing the run.
    Duplicate ``(position_id, leg_id)`` rows are rejected. Positions already
    matured at ``as_of`` are skipped.

    NMD assumptions are resolved per row with this precedence: position
    columns (``core_share``, ``core_avg_maturity``, ``beta``) → the most
    specific ``assumption_table`` row → ``nmd_assumptions`` (config) →
    :data:`~irrbb.behavioral.nmd.DEFAULT_NMD_ASSUMPTIONS`.

    Args:
        positions: Frame conforming to :data:`irrbb.data.schemas.POSITIONS_SCHEMA`.
        as_of: Valuation date.
        nmd_model: NMD behavioural model (defaults to BCBS caps enforced).
        nmd_assumptions: Product-level NMD assumptions overriding the defaults.
        assumption_table: Segment-level deposit-study assumptions.
        discount_overrides: Product → currency → discount curve defaults.
        ndf_settlement_currencies: Allowed NDF settlement currencies.
        ndf_default_settlement: Settlement currency assumed (and reported) for
            NDFs that do not state one.
        ndf_discount_by_pair: Pair (``"EURBRL"``) → discount curve of the
            non-deliverable leg, e.g. a EUR-based BRL implied curve.

    Returns:
        The portfolio.
    """
    model = nmd_model or NMDModel()
    assumptions = {**DEFAULT_NMD_ASSUMPTIONS, **(nmd_assumptions or {})}
    table = assumption_table or AssumptionTable()
    pf = Portfolio()
    seen: set[tuple[str, str]] = set()
    for row in positions.iter_rows(named=True):
        pid = str(row.get("position_id"))
        try:
            key = (pid, str(row.get("leg_id") or "1"))
            if key in seen:
                raise ValueError(f"duplicate leg {key[0]}/{key[1]}")
            seen.add(key)
            product = ProductType(str(row["product_type"]).upper())
            if product.is_non_maturity_deposit:
                category = _enum(
                    NMDCategory, row.get("nmd_category"), DEFAULT_NMD_CATEGORY[product]
                )
                base = table.lookup(product, str(row["currency"]), category) or assumptions[product]
                assumption = NMDAssumption(
                    core_share=_f(row.get("core_share"), base.core_share),
                    core_avg_maturity=_f(row.get("core_avg_maturity"), base.core_avg_maturity),
                    beta=_f(row.get("beta"), base.beta),
                    reprice_months=base.reprice_months,
                )
                eve, nii = model.legs(
                    position_id=pid,
                    product_type=product,
                    currency=str(row["currency"]),
                    balance=abs(float(row["notional"])),
                    client_rate=_f(row.get("fixed_rate")),
                    as_of=as_of,
                    category=category,
                    assumption=assumption,
                    day_count=_enum(DayCount, row.get("day_count"), DayCount.ACT_360),
                    book=str(row.get("book") or "BANKING"),
                    direction=_enum(Direction, row.get("direction"), Direction.PAY),
                )
                pf.eve_legs.extend(eve)
                pf.nii_legs.extend(nii)
                continue
            maturity = row.get("maturity_date")
            if maturity is not None and maturity <= as_of:
                continue  # matured: no longer in the banking book
            leg = leg_from_row(row, as_of, discount_overrides)
            pf.eve_legs.append(leg)
            pf.nii_legs.append(leg)
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("Rejected position %s: %s", pid, exc)
            pf.rejected.append((pid, str(exc)))
    _validate_ndfs(pf, ndf_settlement_currencies, ndf_default_settlement, ndf_discount_by_pair)
    if pf.rejected:
        sample = "; ".join(f"{p}: {r}" for p, r in pf.rejected[:5])
        logger.warning("%d position row(s) rejected (first: %s)", len(pf.rejected), sample)
    return pf
