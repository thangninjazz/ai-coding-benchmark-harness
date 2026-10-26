"""The shopping cart."""

from shop.allocation import allocate
from shop.coupons import FixedOff, PercentOff
from shop.money import ZERO, quantize, to_decimal


class Cart:
    def __init__(self):
        self._lines = {}  # sku -> [unit_price, qty]
        self._percent = None
        self._fixed = None

    def add(self, sku, unit_price, qty=1):
        price = to_decimal(unit_price)
        if price < 0:
            raise ValueError("price must not be negative")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise ValueError("qty must be a positive integer")
        if sku in self._lines:
            self._lines[sku][1] = qty
        else:
            self._lines[sku] = [price, qty]

    def apply_coupon(self, coupon):
        if isinstance(coupon, PercentOff):
            self._percent = coupon
        elif isinstance(coupon, FixedOff):
            self._fixed = coupon
        else:
            raise TypeError("unsupported coupon")

    def _line_subtotals(self):
        return [price * qty for price, qty in self._lines.values()]

    def _discounted_lines(self):
        lines = self._line_subtotals()
        if self._fixed and lines:
            shares = allocate(self._fixed.amount, lines)
            lines = [line - share for line, share in zip(lines, shares)]
        if self._percent:
            lines = [line - line * self._percent.percent / 100 for line in lines]
        return lines

    def subtotal(self):
        return quantize(sum(self._line_subtotals(), ZERO))

    def discount_total(self):
        return quantize(self.subtotal() - sum(self._discounted_lines(), ZERO))

    def tax_total(self, rate="0"):
        rate = to_decimal(rate)
        return quantize(sum((line * rate for line in self._line_subtotals()), ZERO))

    def total(self, rate="0"):
        return quantize(sum(self._discounted_lines(), ZERO)) + self.tax_total(rate)
