"""NDF settlement report.

An NDF (e.g. USD/BRL or EUR/BRL) never exchanges the non-deliverable
currency; at maturity the net difference is cash-settled in the settlement
currency (USD or EUR). The engine values NDFs as two notional legs (each
discounted on its own curve, BRL on the offshore NDF-implied curve), which is
independent of the settlement currency. This report adds the settlement view:

* the **market forward** vs the **contract rate** (non-deliverable currency
  per unit of the other currency, e.g. BRL per USD);
* the **expected settlement amount** at maturity in the settlement currency:
  each leg is converted at the forward FX rate implied by the curves,
  ``F_{c→s}(T) = (spot_c / spot_s) · DF_c(T) / DF_s(T)``;
* its present value in settlement and reporting currency, which equals the sum
  of the two legs' PVs (a consistency check of the two-leg valuation).

Fixing-to-settlement lags (usually two business days) are ignored.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

import polars as pl

from irrbb.core.enums import ProductType
from irrbb.curves.curve_set import CurveSet, FxRates
from irrbb.instruments.leg import Leg

NDF_SCHEMA: dict[str, pl.DataType] = {
    "position_id": pl.String(),
    "book": pl.String(),
    "settlement_currency": pl.String(),
    "settlement_date": pl.Date(),
    "nd_currency": pl.String(),
    "other_currency": pl.String(),
    "nd_notional": pl.Float64(),
    "other_notional": pl.Float64(),
    "contract_rate": pl.Float64(),
    "market_forward": pl.Float64(),
    "settlement_amount": pl.Float64(),
    "pv_settlement_ccy": pl.Float64(),
    "pv_rep": pl.Float64(),
}


def ndf_settlements(
    legs: Iterable[Leg],
    curves: CurveSet,
    fx: FxRates,
    as_of: date,
    deliverable: Sequence[str] = ("USD", "EUR"),
) -> pl.DataFrame:
    """Build the NDF settlement report.

    Args:
        legs: Portfolio legs (non-NDF legs are ignored).
        curves: Base curves.
        fx: Spot FX (reporting-currency units per currency unit).
        as_of: Valuation date.
        deliverable: Currencies that can be settled; the leg in any other
            currency is the non-deliverable one.

    Returns:
        Frame with :data:`NDF_SCHEMA`, one row per NDF. Signs are from the
        bank's perspective (positive settlement amount = the bank receives).
    """
    by_pos: dict[str, list[Leg]] = {}
    for lg in legs:
        if lg.product_type is ProductType.NDF and lg.maturity_date > as_of:
            by_pos.setdefault(lg.position_id, []).append(lg)
    rows: list[dict[str, object]] = []
    for pid, pair in sorted(by_pos.items()):
        if len(pair) != 2:
            continue
        s_ccy = pair[0].settlement_currency or fx.reporting_currency
        mat = max(lg.maturity_date for lg in pair)
        t = (mat - as_of).days / 365.0
        df_s = float(curves.discount_curve(s_ccy).discount_factor(t)[0])

        def df_leg(lg: Leg, t: float = t) -> float:
            return float(curves.discounting(lg.discount_curve, lg.currency).discount_factor(t)[0])

        def fwd_to_settlement(lg: Leg, df_s: float = df_s, s_ccy: str = s_ccy) -> float:
            return fx.rate(lg.currency) / fx.rate(s_ccy) * df_leg(lg) / df_s

        amount = sum(lg.sign * lg.notional * fwd_to_settlement(lg) for lg in pair)
        nd = [lg for lg in pair if lg.currency not in deliverable]
        nd_leg = nd[0] if len(nd) == 1 else pair[0]
        other = pair[1] if nd_leg is pair[0] else pair[0]
        market_fwd = (
            fx.rate(other.currency) / fx.rate(nd_leg.currency) * df_leg(other) / df_leg(nd_leg)
        )
        pv_s = amount * df_s
        rows.append(
            {
                "position_id": pid,
                "book": pair[0].book,
                "settlement_currency": s_ccy,
                "settlement_date": mat,
                "nd_currency": nd_leg.currency,
                "other_currency": other.currency,
                "nd_notional": nd_leg.sign * nd_leg.notional,
                "other_notional": other.sign * other.notional,
                "contract_rate": nd_leg.notional / other.notional if other.notional else None,
                "market_forward": market_fwd,
                "settlement_amount": amount,
                "pv_settlement_ccy": pv_s,
                "pv_rep": pv_s * fx.rate(s_ccy),
            }
        )
    return pl.DataFrame(rows, schema=NDF_SCHEMA)
