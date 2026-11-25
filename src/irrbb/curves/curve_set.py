"""Collections of curves and FX rates used in a valuation run."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import numpy.typing as npt

from irrbb.curves.yield_curve import YieldCurve


@dataclass(frozen=True)
class CurveSet:
    """A named set of curves plus the currency → discount-curve mapping.

    Projection curves are looked up by the leg's ``index`` (which is simply a
    curve name, e.g. ``"USD-SOFR"`` or ``"EUR-EURIBOR-3M"``). Discount curves
    are looked up by currency (risk-free / OIS discounting, as required by
    BCBS 368 §4).

    Attributes:
        as_of: Valuation date common to every curve.
        curves: Curves keyed by name.
        discount_map: Currency → name of its discount curve.
    """

    as_of: date
    curves: Mapping[str, YieldCurve]
    discount_map: Mapping[str, str]

    def __post_init__(self) -> None:
        missing = {c: n for c, n in self.discount_map.items() if n not in self.curves}
        if missing:
            raise ValueError(f"Discount curves not found in curve set: {missing}")

    def __iter__(self) -> Iterator[YieldCurve]:
        return iter(self.curves.values())

    @property
    def currencies(self) -> list[str]:
        """list[str]: Currencies that have a discount curve."""
        return sorted(self.discount_map)

    def discount_curve(self, currency: str) -> YieldCurve:
        """Return the discount curve of a currency.

        Args:
            currency: ISO currency code.

        Returns:
            The currency's discount curve.

        Raises:
            KeyError: If the currency has no discount curve.
        """
        try:
            return self.curves[self.discount_map[currency]]
        except KeyError as exc:
            raise KeyError(f"No discount curve configured for currency {currency}") from exc

    def discounting(self, curve_name: str | None, currency: str) -> YieldCurve:
        """Return a leg's discount curve.

        Args:
            curve_name: Explicit curve (e.g. an NDF-implied curve) or ``None``.
            currency: Currency used when no explicit curve is given.

        Returns:
            The explicit curve if given, else the currency's discount curve.

        Raises:
            KeyError: If the explicit curve is not in the set.
        """
        if curve_name:
            try:
                return self.curves[curve_name]
            except KeyError as exc:
                raise KeyError(f"Discount curve {curve_name} not in curve set") from exc
        return self.discount_curve(currency)

    def projection_curve(self, index: str | None, currency: str) -> YieldCurve:
        """Return the projection curve for a floating index.

        Args:
            index: Index / curve name. When ``None`` or unknown the currency's
                discount curve is used (single-curve fallback).
            currency: Currency used for the fallback.

        Returns:
            The projection curve.
        """
        if index and index in self.curves:
            return self.curves[index]
        return self.discount_curve(currency)

    def transformed(self, fn: Callable[[YieldCurve], YieldCurve]) -> CurveSet:
        """Apply a transformation (typically a shock) to every curve.

        Args:
            fn: Function mapping a curve to its transformed version.

        Returns:
            A new curve set.
        """
        return CurveSet(self.as_of, {k: fn(v) for k, v in self.curves.items()}, self.discount_map)


@dataclass(frozen=True)
class FxRates:
    """Spot FX rates expressed against a single reporting currency.

    Attributes:
        reporting_currency: Currency all results are converted to (e.g. ``"USD"``).
        spot: Units of reporting currency per one unit of each currency.
    """

    reporting_currency: str
    spot: Mapping[str, float] = field(default_factory=dict)

    def rate(self, currency: str) -> float:
        """Return the conversion rate of a currency into the reporting currency.

        Args:
            currency: ISO currency code.

        Returns:
            Reporting-currency units per one unit of ``currency``.

        Raises:
            KeyError: If no rate is available.
        """
        if currency == self.reporting_currency:
            return 1.0
        try:
            return float(self.spot[currency])
        except KeyError as exc:
            raise KeyError(f"Missing FX rate {currency}/{self.reporting_currency}") from exc

    def convert(self, amount: npt.ArrayLike, currency: str) -> npt.NDArray[np.float64]:
        """Convert amounts in ``currency`` into the reporting currency.

        Args:
            amount: Amount(s) in ``currency``.
            currency: ISO currency code of ``amount``.

        Returns:
            Converted amount(s).
        """
        return np.asarray(amount, dtype=np.float64) * self.rate(currency)

    def forward(self, currency: str, t: float, curves: CurveSet) -> float:
        """Covered-interest-parity forward of ``currency`` in reporting units.

        Args:
            currency: Foreign currency.
            t: Time to delivery in years.
            curves: Curve set holding both discount curves.

        Returns:
            ``S × DF_ccy(t) / DF_rep(t)``.
        """
        if currency == self.reporting_currency:
            return 1.0
        df_f = curves.discount_curve(currency).discount_factor(t)[0]
        df_d = curves.discount_curve(self.reporting_currency).discount_factor(t)[0]
        return self.rate(currency) * float(df_f / df_d)
