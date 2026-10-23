"""Money helpers."""

from decimal import Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_decimal(value):
    """Turn a price, amount or rate into a Decimal."""
    return Decimal(str(value))


def quantize(amount):
    """Round a Decimal to whole cents."""
    return amount.quantize(CENT)
