# Task 003: Cart totals are wrong by a cent (and coupons stack incorrectly)

You are given the repository snapshot in `repo/` (a small library, package `shop`).
**Submit a unified diff** (as `git diff` would print it) that fixes the bugs below. You may
modify or add `.py` files under `shop/`. The public API must stay as described here.

## Report

> Customers see totals that are off by a cent, a coupon that was supposed to be capped
> still goes negative, "SKU already in the cart" silently throws away the old quantity,
> and tax is charged on money the customer never paid. Prices are `Decimal` internally,
> but something in the rounding path is clearly wrong.

## Specification (this is what the hidden tests check)

All money values are `Decimal` rounded to cents. **Rounding is always half-up**
(`ROUND_HALF_UP`: `2.665 -> 2.67`, `-2.665 -> -2.67`), never banker's rounding.

### `shop.money`
* `to_decimal(value)` accepts `str`, `int` and `Decimal` and returns a finite `Decimal`.
  `float` and `bool` raise `TypeError`. A string that is not a finite number raises
  `ValueError`.
* `quantize(amount)` rounds a `Decimal` to cents, half-up.

### `shop.allocation.allocate(total, weights) -> list[Decimal]`
Splits `total` across `weights` proportionally. `total` and every weight are non-negative
whole-cent `Decimal`s (anything else raises `ValueError`).
* The shares are whole cents and **add up to exactly `total`**.
* Largest-remainder method: each share starts at `floor(total * weight / sum(weights))`
  cents; the remaining cents go to the shares with the largest fractional remainders,
  one cent each; **ties go to the earlier index**.
* Empty `weights` -> `[]`. All-zero weights with `total == 0` -> all zeros; all-zero
  weights with a non-zero `total` raises `ValueError`.

### `shop.coupons`
* `PercentOff(percent)`: `0 < percent <= 100`, otherwise `ValueError`.
* `FixedOff(amount)`: `amount > 0` and a whole number of cents, otherwise `ValueError`.
* Both accept `str`/`int`/`Decimal` (via `to_decimal`) and are immutable.

### `shop.Cart`
* `add(sku, unit_price, qty=1)`: `unit_price` via `to_decimal`, must not be negative;
  `qty` must be a positive `int` (not `bool`); violations raise `ValueError`.
  Adding an existing `sku` **adds** to its quantity; if the price differs from the one
  already in the cart, raise `ValueError` and change nothing.
* `apply_coupon(coupon)`: at most one `PercentOff` and one `FixedOff` are active; a later
  coupon of the same type replaces the earlier one. Any other object raises `TypeError`.
* Pricing, in this order, with every step rounded to cents half-up:
  1. **line subtotal** = `unit_price * qty` (one line per sku, insertion order);
  2. **percent coupon**, per line: `line - round(line * percent / 100)`;
  3. **fixed coupon**: the amount actually taken is `min(amount, sum of the lines)` (the
     total can never go below zero); it is split across the lines with
     `allocate(taken, lines)` using the lines' current values as weights;
  4. **tax**, per line, on the discounted line: `round(line * rate)`; `tax_total` is the
     sum of those per-line taxes.
* `subtotal()` = sum of line subtotals. `discount_total()` = `subtotal()` minus the sum of
  the discounted lines. `tax_total(rate="0")`: `rate` via `to_decimal`, negative raises
  `ValueError`. `total(rate="0")` = sum of discounted lines + `tax_total(rate)`.
* An empty cart returns `Decimal("0.00")` from every one of these.

Standard library only. Absolute imports (`from shop.money import ...`).
