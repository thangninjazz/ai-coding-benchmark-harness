"""Hidden tests for Task 003. Everything asserted here is stated in problem.md."""

import dataclasses
import math
import random
from decimal import Decimal
from fractions import Fraction

import pytest
from shop import Cart, FixedOff, PercentOff
from shop.allocation import allocate
from shop.money import quantize, to_decimal

D = Decimal


def cents(amount: Decimal) -> str:
    return f"{amount:.2f}"


# ------------------------------------------------------------------------ shop.money
@pytest.mark.parametrize("bad", [0.1, 1.0, True, False])
def test_to_decimal_rejects_floats_and_bools(bad):
    with pytest.raises(TypeError):
        to_decimal(bad)


@pytest.mark.parametrize("bad", ["abc", "", "1.2.3", "NaN", "Infinity", "-inf"])
def test_to_decimal_rejects_garbage_with_value_error(bad):
    with pytest.raises(ValueError):
        to_decimal(bad)


def test_to_decimal_accepts_str_int_and_decimal():
    assert to_decimal("19.99") == D("19.99")
    assert to_decimal(" 7 ") == D("7")
    assert to_decimal(5) == D("5")
    assert to_decimal(D("0.125")) == D("0.125")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2.665", "2.67"), ("2.675", "2.68"), ("1.005", "1.01"), ("0.005", "0.01"),
        ("0.004", "0.00"), ("0.015", "0.02"), ("-2.665", "-2.67"), ("10", "10.00"),
    ],
)
def test_quantize_rounds_half_up(raw, expected):
    assert cents(quantize(D(raw))) == expected


# ------------------------------------------------------------------- shop.allocation
@pytest.mark.parametrize(
    "total,weights,expected",
    [
        ("0.10", ["1.00", "1.00", "1.00"], ["0.04", "0.03", "0.03"]),
        ("1.00", ["0.01", "0.02"], ["0.33", "0.67"]),
        ("0.01", ["1.00", "1.00"], ["0.01", "0.00"]),  # tie -> earlier index
        ("5.00", ["0.00", "0.00", "0.03"], ["0.00", "0.00", "5.00"]),
        ("0.03", ["0.02", "0.01", "0.01", "0.00"], ["0.01", "0.01", "0.01", "0.00"]),
        ("0.00", ["1.00", "2.00"], ["0.00", "0.00"]),
        ("7.00", ["10.00"], ["7.00"]),
    ],
)
def test_allocate_largest_remainder(total, weights, expected):
    shares = allocate(D(total), [D(w) for w in weights])
    assert [cents(s) for s in shares] == expected
    assert sum(shares) == D(total)


def test_allocate_edge_cases_and_validation():
    assert allocate(D("0.00"), []) == []
    assert [cents(s) for s in allocate(D("0.00"), [D("0.00"), D("0.00")])] == ["0.00", "0.00"]
    with pytest.raises(ValueError):
        allocate(D("1.00"), [D("0.00"), D("0.00")])
    assert allocate(D("1.00"), []) == []  # nothing to split across, as stated in the spec
    with pytest.raises(ValueError):
        allocate(D("-1.00"), [D("1.00")])
    with pytest.raises(ValueError):
        allocate(D("1.00"), [D("1.00"), D("-1.00")])
    with pytest.raises(ValueError):
        allocate(D("0.005"), [D("1.00")])
    with pytest.raises(ValueError):
        allocate(D("1.00"), [D("1.005")])


def test_allocate_never_loses_or_invents_cents():
    rng = random.Random(7)
    for _ in range(300):
        weights = [D(rng.randint(0, 5000)) / 100 for _ in range(rng.randint(1, 8))]
        if not any(weights):
            continue
        total = D(rng.randint(0, 100000)) / 100
        shares = allocate(total, weights)
        assert sum(shares) == total
        assert all(s >= 0 and s == quantize(s) for s in shares)


# ----------------------------------------------------------------------- shop.coupons
@pytest.mark.parametrize("bad", [0, "0", "-1", "100.01", 101, "abc"])
def test_percent_off_validation(bad):
    with pytest.raises(ValueError):
        PercentOff(bad)


def test_percent_off_bounds_and_types():
    assert PercentOff("100").percent == D("100")
    assert PercentOff("0.01").percent == D("0.01")
    assert PercentOff(15).percent == D("15")
    with pytest.raises(TypeError):
        PercentOff(10.0)


@pytest.mark.parametrize("bad", [0, "0", "-1", "1.005", "0.001"])
def test_fixed_off_validation(bad):
    with pytest.raises(ValueError):
        FixedOff(bad)


def test_fixed_off_accepts_cents_and_rejects_floats():
    assert FixedOff("5.25").amount == D("5.25")
    assert FixedOff(3).amount == D("3")
    with pytest.raises(TypeError):
        FixedOff(1.5)


def test_coupons_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        PercentOff("10").percent = D("5")
    with pytest.raises(dataclasses.FrozenInstanceError):
        FixedOff("1").amount = D("5")


# ---------------------------------------------------------------------------- Cart: basics
def test_empty_cart_is_all_zero():
    cart = Cart()
    for value in (cart.subtotal(), cart.discount_total(), cart.tax_total(), cart.total("0.2")):
        assert cents(value) == "0.00"
    cart.apply_coupon(PercentOff("10"))
    cart.apply_coupon(FixedOff("5"))
    assert cents(cart.total("0.2")) == "0.00"


def test_basic_totals_without_coupons_or_tax():
    cart = Cart()
    cart.add("A", "19.99", 3)
    cart.add("B", "5.00")
    assert cents(cart.subtotal()) == "64.97"
    assert cents(cart.total()) == "64.97"
    assert cents(cart.discount_total()) == "0.00"
    assert cents(cart.tax_total()) == "0.00"


def test_add_validation():
    cart = Cart()
    with pytest.raises(ValueError):
        cart.add("A", "-0.01")
    with pytest.raises(TypeError):
        cart.add("A", 9.99)
    with pytest.raises(ValueError):
        cart.add("A", "abc")
    for bad_qty in (0, -1, True, 1.5, "2"):
        with pytest.raises(ValueError):
            cart.add("A", "1.00", bad_qty)
    assert cents(cart.subtotal()) == "0.00"


def test_adding_an_existing_sku_adds_quantity():
    cart = Cart()
    cart.add("X", "2.50", 2)
    cart.add("X", "2.50", 3)
    assert cents(cart.subtotal()) == "12.50"


def test_adding_an_existing_sku_at_another_price_is_rejected_atomically():
    cart = Cart()
    cart.add("X", "2.50", 2)
    with pytest.raises(ValueError):
        cart.add("X", "2.51", 1)
    assert cents(cart.subtotal()) == "5.00"


def test_zero_price_lines_are_allowed():
    cart = Cart()
    cart.add("FREE", "0", 4)
    cart.add("A", "1.00")
    assert cents(cart.total()) == "1.00"


def test_unknown_coupon_type_is_rejected():
    with pytest.raises(TypeError):
        Cart().apply_coupon("TENOFF")
    with pytest.raises(TypeError):
        Cart().apply_coupon(10)


def test_tax_rate_validation():
    cart = Cart()
    cart.add("A", "1.00")
    with pytest.raises(ValueError):
        cart.tax_total("-0.01")
    with pytest.raises(ValueError):
        cart.total("-1")
    with pytest.raises(TypeError):
        cart.total(0.1)


def test_line_subtotal_is_rounded_half_up():
    cart = Cart()
    cart.add("A", "0.335", 3)  # 1.005 -> 1.01
    assert cents(cart.subtotal()) == "1.01"


# --------------------------------------------------------------- Cart: coupons & tax
def test_percent_coupon_rounds_each_line_half_up():
    cart = Cart()
    cart.add("A", "0.05")
    cart.add("B", "0.05")
    cart.apply_coupon(PercentOff("10"))  # 0.005 per line -> 0.01 each
    assert cents(cart.discount_total()) == "0.02"
    assert cents(cart.total()) == "0.08"


def test_fixed_coupon_applies_after_percent_and_is_split_by_discounted_weight():
    cart = Cart()
    cart.add("A", "20.00")
    cart.add("B", "10.00")
    cart.apply_coupon(FixedOff("6.00"))  # applied second even though added first
    cart.apply_coupon(PercentOff("50"))  # lines -> 10.00 / 5.00, then 4.00 / 2.00 off
    assert cents(cart.total()) == "9.00"
    assert cents(cart.discount_total()) == "21.00"


def test_fixed_coupon_is_capped_so_the_total_never_goes_negative():
    cart = Cart()
    cart.add("A", "20.00")
    cart.add("B", "10.00")
    cart.apply_coupon(PercentOff("50"))
    cart.apply_coupon(FixedOff("100.00"))
    assert cents(cart.total()) == "0.00"
    assert cents(cart.total("0.2")) == "0.00"
    assert cents(cart.discount_total()) == "30.00"


def test_fixed_coupon_split_never_loses_a_cent():
    cart = Cart()
    for sku in "ABC":
        cart.add(sku, "1.00")
    cart.apply_coupon(FixedOff("0.10"))  # 0.04 / 0.03 / 0.03
    assert cents(cart.discount_total()) == "0.10"
    assert cents(cart.total()) == "2.90"


def test_fixed_coupon_is_split_proportionally_not_evenly():
    cart = Cart()
    cart.add("A", "0.01")
    cart.add("B", "0.06")
    cart.apply_coupon(FixedOff("0.02"))  # proportional: lines 0.01 / 0.04 (an even split gives 0.00 / 0.05)
    assert cents(cart.tax_total("0.10")) == "0.00"
    assert cents(cart.total("0.10")) == "0.05"


def test_tax_is_computed_per_line_and_rounded_half_up():
    cart = Cart()
    for sku in "ABC":
        cart.add(sku, "0.05")
    assert cents(cart.tax_total("0.10")) == "0.03"  # 3 x round(0.005), not round(0.015)
    assert cents(cart.total("0.10")) == "0.18"


def test_tax_applies_to_the_discounted_amount_only():
    cart = Cart()
    cart.add("A", "100.00")
    cart.apply_coupon(PercentOff("10"))
    assert cents(cart.tax_total("0.0825")) == "7.43"  # 90.00 * 0.0825 = 7.425
    assert cents(cart.total("0.0825")) == "97.43"


def test_a_later_coupon_of_the_same_type_replaces_the_earlier_one():
    cart = Cart()
    cart.add("A", "100.00")
    cart.apply_coupon(PercentOff("10"))
    cart.apply_coupon(PercentOff("20"))
    assert cents(cart.total()) == "80.00"
    cart.apply_coupon(FixedOff("1.00"))
    cart.apply_coupon(FixedOff("5.00"))
    assert cents(cart.total()) == "75.00"


def test_results_are_stable_across_repeated_calls():
    cart = Cart()
    cart.add("A", "9.99", 3)
    cart.apply_coupon(PercentOff("12.5"))
    cart.apply_coupon(FixedOff("2.00"))
    first = (cart.subtotal(), cart.discount_total(), cart.tax_total("0.07"), cart.total("0.07"))
    assert first == (cart.subtotal(), cart.discount_total(), cart.tax_total("0.07"), cart.total("0.07"))


# ------------------------------------------- independent reference model (integer cents)
def _half_up(x: Fraction) -> int:
    return math.floor(x + Fraction(1, 2))


def _reference(items, percent, fixed_cents, rate):
    lines = [_half_up(Fraction(price) * qty * 100) for price, qty in items]
    subtotal = sum(lines)
    if percent is not None:
        lines = [line - _half_up(Fraction(line) * Fraction(percent) / 100) for line in lines]
    if fixed_cents is not None and lines:
        taken = min(fixed_cents, sum(lines))
        weight_sum = sum(lines)
        shares = [taken * w // weight_sum for w in lines] if weight_sum else [0] * len(lines)
        remainders = [taken * w % weight_sum for w in lines] if weight_sum else [0] * len(lines)
        order = sorted(range(len(lines)), key=lambda i: (-remainders[i], i))
        for i in order[: taken - sum(shares)]:
            shares[i] += 1
        lines = [w - s for w, s in zip(lines, shares)]
    tax = sum(_half_up(Fraction(line) * Fraction(rate)) for line in lines)
    return subtotal, subtotal - sum(lines), tax, sum(lines) + tax


def test_random_carts_agree_with_the_reference_model():
    rng = random.Random(20240601)
    prices = ["0.01", "0.05", "0.335", "1.00", "2.675", "9.99", "19.99", "100.00", "0.995", "12.345"]
    rates = ["0", "0.0825", "0.10", "0.2", "0.0725", "0.075"]
    for _ in range(400):
        items = [(rng.choice(prices), rng.randint(1, 6)) for _ in range(rng.randint(0, 6))]
        percent = rng.choice([None, "5", "10", "12.5", "33.333", "50", "100", "0.5"])
        fixed = rng.choice([None, 1, 7, 99, 500, 2500, 100000])  # in cents
        rate = rng.choice(rates)

        cart = Cart()
        for index, (price, qty) in enumerate(items):
            cart.add(f"sku{index}", price, qty)
        if percent is not None:
            cart.apply_coupon(PercentOff(percent))
        if fixed is not None:
            cart.apply_coupon(FixedOff(D(fixed) / 100))

        subtotal, discount, tax, total = _reference(items, percent, fixed, rate)
        got = (cart.subtotal(), cart.discount_total(), cart.tax_total(rate), cart.total(rate))
        expected = tuple(D(c) / 100 for c in (subtotal, discount, tax, total))
        assert got == expected, (items, percent, fixed, rate)
        assert all(g.as_tuple().exponent == -2 for g in got), "money must be quantized to cents"
        assert cart.total(rate) >= 0
