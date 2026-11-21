"""Decimal money helpers. All monetary maths in the project goes through here.

Rule (documented in docs/BUSINESS_RULES.md): every monetary amount is stored
with 2 decimal places and rounded ROUND_HALF_UP at the point it is computed.
Floats are never used for money.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_decimal(value, default=None):
    """Convert user input to Decimal, or return ``default`` when invalid."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default
    if not result.is_finite():
        return default
    return result


def q2(value) -> Decimal:
    """Quantize to 2 decimal places, half-up."""
    from apps.core.exceptions import BusinessRuleError

    amount = to_decimal(value)
    if amount is None:
        raise BusinessRuleError("Enter a finite monetary amount.")
    try:
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise BusinessRuleError("The monetary amount is too large.") from None


def pct_of(amount, percent) -> Decimal:
    return q2(Decimal(amount) * Decimal(percent) / Decimal(100))
