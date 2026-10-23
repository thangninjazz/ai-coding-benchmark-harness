"""Splitting an amount of money across several lines."""

from decimal import ROUND_DOWN, Decimal

from shop.money import CENT


def allocate(total, weights):
    """Split ``total`` across ``weights`` in proportion to each weight."""
    weight_sum = sum(weights, Decimal(0))
    if weight_sum == 0:
        return [Decimal("0.00") for _ in weights]
    return [(total * w / weight_sum).quantize(CENT, rounding=ROUND_DOWN) for w in weights]
