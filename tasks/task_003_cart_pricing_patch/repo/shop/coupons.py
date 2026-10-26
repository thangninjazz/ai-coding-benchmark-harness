"""Coupons that can be applied to a cart."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PercentOff:
    """Take ``percent`` percent off every line."""

    percent: Decimal

    def __post_init__(self):
        object.__setattr__(self, "percent", Decimal(str(self.percent)))


@dataclass(frozen=True)
class FixedOff:
    """Take a fixed amount of money off the whole cart."""

    amount: Decimal

    def __post_init__(self):
        object.__setattr__(self, "amount", Decimal(str(self.amount)))
