"""Non-maturity deposit (NMD) behavioural model.

NMDs (DDA, MMDA, savings) have no contractual maturity. Following BCBS 368
§4.3.1 the balance is split into:

* a **non-core** part that reprices / runs off overnight, and
* a **core** part that is stable and insensitive to rate changes, slotted over
  time with a run-off profile whose average maturity is capped by category.

=========================  ==============  ======================
Category                   Max core share  Max avg core maturity
=========================  ==============  ======================
Retail transactional       90%             5.0 years
Retail non-transactional   70%             4.5 years
Wholesale                  50%             4.0 years
=========================  ==============  ======================

The core part is modelled as a linearly amortising monthly profile over
``2 × average maturity``, so that its weighted-average life equals the
requested average maturity. For NII, the full balance is kept constant and its
administered rate moves by ``beta × Δmarket`` (repricing monthly by default).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import polars as pl

from irrbb.core.dates import add_months
from irrbb.core.enums import (
    Amortization,
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
class NMDCap:
    """Supervisory caps for one NMD category.

    Attributes:
        max_core_share: Maximum share of the balance treated as core.
        max_avg_maturity: Maximum average maturity of the core part (years).
    """

    max_core_share: float
    max_avg_maturity: float


#: BCBS 368 Table 4 caps.
BCBS_NMD_CAPS: dict[NMDCategory, NMDCap] = {
    NMDCategory.RETAIL_TRANSACTIONAL: NMDCap(0.90, 5.0),
    NMDCategory.RETAIL_NON_TRANSACTIONAL: NMDCap(0.70, 4.5),
    NMDCategory.WHOLESALE: NMDCap(0.50, 4.0),
}


@dataclass(frozen=True)
class NMDAssumption:
    """Behavioural assumption set for an NMD product / segment.

    Attributes:
        core_share: Share of the balance that is core (stable, rate-insensitive).
        core_avg_maturity: Average maturity of the core part in years.
        beta: Deposit rate pass-through of market rate changes.
        reprice_months: Repricing lag/frequency of the administered rate (NII).
    """

    core_share: float
    core_avg_maturity: float
    beta: float
    reprice_months: int = 1


DEFAULT_NMD_ASSUMPTIONS: dict[ProductType, NMDAssumption] = {
    ProductType.DDA: NMDAssumption(core_share=0.70, core_avg_maturity=3.0, beta=0.10),
    ProductType.SAVINGS: NMDAssumption(core_share=0.60, core_avg_maturity=2.5, beta=0.35),
    ProductType.MMDA: NMDAssumption(core_share=0.40, core_avg_maturity=1.5, beta=0.65),
}
"""Illustrative defaults by product.

These must be replaced by the bank's own calibrated, validated assumptions
(deposit studies).
"""

#: Default category per product when the source system does not provide one.
DEFAULT_NMD_CATEGORY: dict[ProductType, NMDCategory] = {
    ProductType.DDA: NMDCategory.RETAIL_TRANSACTIONAL,
    ProductType.SAVINGS: NMDCategory.RETAIL_NON_TRANSACTIONAL,
    ProductType.MMDA: NMDCategory.RETAIL_NON_TRANSACTIONAL,
}


@dataclass(frozen=True)
class NMDModel:
    """Turns an NMD balance into EVE run-off legs and an NII leg.

    Attributes:
        caps: Supervisory caps by category.
        enforce_caps: Clip assumptions that exceed the caps (with a warning).
        nii_horizon_years: Maturity assigned to the constant-balance NII leg.
    """

    caps: dict[NMDCategory, NMDCap] = field(default_factory=lambda: dict(BCBS_NMD_CAPS))
    enforce_caps: bool = True
    nii_horizon_years: int = 30

    def resolve(
        self,
        position_id: str,
        category: NMDCategory,
        assumption: NMDAssumption,
    ) -> NMDAssumption:
        """Apply supervisory caps to an assumption.

        Args:
            position_id: Position identifier (for log messages).
            category: NMD category.
            assumption: Requested assumption.

        Returns:
            The (possibly clipped) assumption.
        """
        if not self.enforce_caps:
            return assumption
        cap = self.caps[category]
        core = min(assumption.core_share, cap.max_core_share)
        mat = min(assumption.core_avg_maturity, cap.max_avg_maturity)
        if core < assumption.core_share or mat < assumption.core_avg_maturity:
            logger.warning(
                "NMD %s: assumption (core %.2f, %.2fy) capped to (%.2f, %.2fy) for %s",
                position_id,
                assumption.core_share,
                assumption.core_avg_maturity,
                core,
                mat,
                category.value,
            )
        return NMDAssumption(core, mat, assumption.beta, assumption.reprice_months)

    def legs(
        self,
        *,
        position_id: str,
        product_type: ProductType,
        currency: str,
        balance: float,
        client_rate: float,
        as_of: date,
        category: NMDCategory,
        assumption: NMDAssumption,
        day_count: DayCount = DayCount.ACT_360,
        book: str = "BANKING",
        direction: Direction = Direction.PAY,
    ) -> tuple[list[Leg], list[Leg]]:
        """Build EVE legs and NII legs for one NMD balance.

        Args:
            position_id: Position identifier.
            product_type: DDA / MMDA / SAVINGS.
            currency: Currency of the balance.
            balance: Outstanding balance (positive).
            client_rate: Current administered rate paid to clients.
            as_of: Valuation date.
            category: BCBS NMD category.
            assumption: Behavioural assumption (caps are applied).
            day_count: Accrual day count.
            book: Reporting book.
            direction: ``PAY`` for deposits (liabilities).

        Returns:
            ``(eve_legs, nii_legs)``.
        """
        a = self.resolve(position_id, category, assumption)
        attrs = {
            "nmd_category": category.value,
            "core_share": a.core_share,
            "core_avg_maturity": a.core_avg_maturity,
        }

        def mk(
            leg_id: str, notional: float, maturity: date, freq: Frequency, amort: Amortization
        ) -> Leg:
            return Leg(
                position_id=position_id,
                leg_id=leg_id,
                product_type=product_type,
                currency=currency,
                direction=direction,
                notional=notional,
                start_date=as_of,
                maturity_date=maturity,
                rate_type=RateType.ADMINISTERED,
                fixed_rate=client_rate,
                payment_frequency=freq,
                day_count=day_count,
                amortization=amort,
                beta=a.beta,
                reprice_months=a.reprice_months,
                book=book,
                attributes=dict(attrs),
            )

        eve: list[Leg] = []
        non_core = balance * (1.0 - a.core_share)
        core = balance * a.core_share
        if non_core > 0:
            eve.append(
                mk(
                    "NONCORE",
                    non_core,
                    as_of + timedelta(days=1),
                    Frequency.ZERO,
                    Amortization.BULLET,
                )
            )
        if core > 0:
            months = max(1, round(2 * a.core_avg_maturity * 12))
            eve.append(
                mk("CORE", core, add_months(as_of, months), Frequency.MONTHLY, Amortization.LINEAR)
            )
        nii = [
            mk(
                "NII",
                balance,
                add_months(as_of, 12 * self.nii_horizon_years),
                Frequency.MONTHLY,
                Amortization.BULLET,
            )
        ]
        return eve, nii


class AssumptionTable:
    """Segment-level NMD assumptions from a deposit study.

    Rows are keyed by ``product_type`` with optional ``currency`` and
    ``nmd_category`` (empty = any). The most specific matching row wins:
    product+currency+category, then product+currency, product+category,
    product only.

    Args:
        frame: Frame conforming to
            :data:`~irrbb.data.schemas.NMD_ASSUMPTIONS_SCHEMA`.
    """

    def __init__(self, frame: pl.DataFrame | None = None) -> None:
        self._rows: dict[tuple[str, str | None, str | None], NMDAssumption] = {}
        if frame is None or frame.is_empty():
            return
        for r in frame.iter_rows(named=True):
            key = (
                str(r["product_type"]).upper(),
                r.get("currency") or None,
                (r.get("nmd_category") or None),
            )
            self._rows[key] = NMDAssumption(
                core_share=float(r["core_share"]),
                core_avg_maturity=float(r["core_avg_maturity"]),
                beta=float(r["beta"]),
                reprice_months=int(r.get("reprice_months") or 1),
            )

    def __len__(self) -> int:
        return len(self._rows)

    def lookup(
        self, product: ProductType, currency: str, category: NMDCategory
    ) -> NMDAssumption | None:
        """Return the most specific assumption for a segment.

        Args:
            product: NMD product.
            currency: Currency of the balance.
            category: BCBS category.

        Returns:
            The assumption or ``None`` when no row matches.
        """
        p, c = product.value, category.value
        for key in ((p, currency, c), (p, currency, None), (p, None, c), (p, None, None)):
            if key in self._rows:
                return self._rows[key]
        return None
