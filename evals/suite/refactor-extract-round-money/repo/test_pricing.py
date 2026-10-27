from decimal import Decimal

from checkout import cart_total, split_evenly, tip
from pricing import add_tax, apply_discount, line_total


def test_line_total_rounds_half_up():
    assert line_total("0.125", 1) == Decimal("0.13")


def test_discount():
    assert apply_discount(Decimal("19.99"), 15) == Decimal("16.99")


def test_tax():
    assert add_tax(Decimal("10.00"), "0.075") == Decimal("10.75")


def test_cart_total():
    assert cart_total([("2.50", 3), ("0.99", 2)], discount_percent=10, tax_rate="0.2") == Decimal("10.24")


def test_split():
    assert split_evenly("10.00", 3) == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]


def test_tip():
    assert tip("42.10", 18) == Decimal("7.58")
