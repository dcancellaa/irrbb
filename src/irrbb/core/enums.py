"""Enumerations shared across the IRRBB engine.

All enums subclass :class:`enum.StrEnum` so they serialise naturally to and from
CSV, Parquet, JSON and database columns.
"""

from __future__ import annotations

from enum import StrEnum


class ProductType(StrEnum):
    """Banking-book products supported by the engine."""

    DDA = "DDA"
    """Demand deposit account (non-maturity deposit, usually transactional)."""
    MMDA = "MMDA"
    """Money market deposit account (non-maturity deposit, rate-sensitive)."""
    SAVINGS = "SAVINGS"
    """Savings account (non-maturity deposit)."""
    TERM_DEPOSIT = "TERM_DEPOSIT"
    """Time / term deposit (CD) with contractual maturity."""
    LOAN = "LOAN"
    """Commercial or retail loan, fixed or floating."""
    MORTGAGE = "MORTGAGE"
    """Residential mortgage (prepayment sensitive)."""
    CREDIT_CARD = "CREDIT_CARD"
    """Revolving credit card balances (behavioural run-off)."""
    BOND = "BOND"
    """Investment-securities holding in the banking book."""
    IRS = "IRS"
    """Single-currency interest rate swap."""
    CCS = "CCS"
    """Cross-currency swap."""
    FX_FORWARD = "FX_FORWARD"
    """Deliverable FX outright forward."""
    NDF = "NDF"
    """Non-deliverable forward (cash-settled in the settlement currency)."""
    FX_SWAP = "FX_SWAP"
    """FX swap (near and far exchanges)."""

    @property
    def is_non_maturity_deposit(self) -> bool:
        """bool: Whether the product is a non-maturity deposit (NMD)."""
        return self in {ProductType.DDA, ProductType.MMDA, ProductType.SAVINGS}

    @property
    def is_derivative(self) -> bool:
        """bool: Whether the product is an off-balance-sheet derivative."""
        return self in {
            ProductType.IRS,
            ProductType.CCS,
            ProductType.FX_FORWARD,
            ProductType.NDF,
            ProductType.FX_SWAP,
        }


class RateType(StrEnum):
    """How the interest rate of a leg is determined."""

    FIXED = "FIXED"
    """Contractual fixed coupon."""
    FLOAT = "FLOAT"
    """Floating coupon referencing an index curve plus a spread."""
    ADMINISTERED = "ADMINISTERED"
    """Bank-administered rate (NMDs) that moves with market rates via a beta."""
    NONE = "NONE"
    """Notional-only leg without interest (FX forwards, NDFs, FX swaps)."""


class Direction(StrEnum):
    """Economic direction of a leg from the bank's perspective."""

    RECEIVE = "RECEIVE"
    """Asset / receive leg: the bank receives the cash flows (+1)."""
    PAY = "PAY"
    """Liability / pay leg: the bank pays the cash flows (-1)."""

    @property
    def sign(self) -> int:
        """int: ``+1`` for receive legs and ``-1`` for pay legs."""
        return 1 if self is Direction.RECEIVE else -1


class DayCount(StrEnum):
    """Day count conventions."""

    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"
    ACT_ACT = "ACT/ACT"
    THIRTY_360 = "30/360"


class Frequency(StrEnum):
    """Payment / reset frequencies."""

    ZERO = "ZERO"
    """Single payment at maturity (zero coupon / bullet)."""
    MONTHLY = "1M"
    QUARTERLY = "3M"
    SEMIANNUAL = "6M"
    ANNUAL = "12M"

    @property
    def months(self) -> int:
        """int: Number of months per period (``0`` for :attr:`ZERO`)."""
        return {"ZERO": 0, "1M": 1, "3M": 3, "6M": 6, "12M": 12}[self.value]


class Amortization(StrEnum):
    """Principal amortisation profiles."""

    BULLET = "BULLET"
    """All principal repaid at maturity."""
    LINEAR = "LINEAR"
    """Equal principal instalments every period."""
    ANNUITY = "ANNUITY"
    """Level payment (principal + interest) every period."""


class FlowType(StrEnum):
    """Type of an individual cash flow."""

    INTEREST = "INTEREST"
    PRINCIPAL = "PRINCIPAL"


class NMDCategory(StrEnum):
    """BCBS 368 non-maturity deposit categories (Table 4 of the standard)."""

    RETAIL_TRANSACTIONAL = "RETAIL_TRANSACTIONAL"
    RETAIL_NON_TRANSACTIONAL = "RETAIL_NON_TRANSACTIONAL"
    WHOLESALE = "WHOLESALE"


class BehaviouralKind(StrEnum):
    """Which BCBS behavioural scalar family applies to a leg's run-off rate."""

    NONE = "NONE"
    PREPAYMENT = "PREPAYMENT"
    """Loan / mortgage prepayment (CPR), scaled by the prepayment scalars."""
    EARLY_REDEMPTION = "EARLY_REDEMPTION"
    """Term-deposit early redemption (TDRR), scaled by the TDRR scalars."""


class QuoteType(StrEnum):
    """Market quote types accepted for raw curve inputs."""

    ZERO_CC = "ZERO_CC"
    """Continuously compounded zero rate."""
    ZERO_ANNUAL = "ZERO_ANNUAL"
    """Annually compounded zero rate."""
    DISCOUNT_FACTOR = "DISCOUNT_FACTOR"
    """Discount factor."""
    DEPOSIT = "DEPOSIT"
    """Simple-interest money market deposit rate (bootstrapped)."""
    OIS_SWAP = "OIS_SWAP"
    """Par OIS swap rate, annual fixed vs compounded overnight (bootstrapped)."""
    FX_SPOT = "FX_SPOT"
    """FX spot of an implied curve: curve-currency units per one base-currency unit."""
    FX_OUTRIGHT = "FX_OUTRIGHT"
    """FX forward outright (same convention as ``FX_SPOT``) → implied curve."""
    FX_POINTS = "FX_POINTS"
    """FX forward points in price units (``outright - spot``) → implied curve."""


class Interpolation(StrEnum):
    """Curve interpolation schemes."""

    LINEAR_ZERO = "LINEAR_ZERO"
    """Linear on continuously compounded zero rates."""
    LOG_LINEAR_DF = "LOG_LINEAR_DF"
    """Linear on log discount factors (piecewise-flat forwards)."""


class ScenarioName(StrEnum):
    """The six BCBS 368 standard interest rate shock scenarios plus the base case."""

    BASE = "BASE"
    PARALLEL_UP = "PARALLEL_UP"
    PARALLEL_DOWN = "PARALLEL_DOWN"
    STEEPENER = "STEEPENER"
    FLATTENER = "FLATTENER"
    SHORT_UP = "SHORT_UP"
    SHORT_DOWN = "SHORT_DOWN"


class LowerBoundPolicy(StrEnum):
    """Post-shock lower bound applied to shocked zero rates."""

    NONE = "NONE"
    """No floor: shocked rates may go arbitrarily negative."""
    ZERO = "ZERO"
    """Floor shocked rates at zero (only where the shock moves them below zero)."""
    EBA = "EBA"
    """EBA GL/2022/14 maturity-dependent floor: -150bp rising 3bp per year to 0bp."""
