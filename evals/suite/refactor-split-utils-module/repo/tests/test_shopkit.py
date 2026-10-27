from datetime import date

from shopkit import Cart, format_money, render_receipt
from shopkit.utils import business_days_between, parse_money, slug, truncate


def test_slug():
    assert slug("Crème Brûlée Mix!") == "creme-brulee-mix"


def test_truncate():
    assert truncate("abcdefghij", 6) == "abcde…"


def test_money_roundtrip():
    assert parse_money("€1,234.50") == 123450
    assert format_money(123450) == "€1,234.50"


def test_cart_total_and_delivery():
    cart = Cart()
    cart.add("Coffee Beans", "12.90", 2)
    cart.add("coffee beans", "12.90")
    assert cart.total() == "€38.70"
    assert cart.delivery_date(date(2024, 5, 3)) == date(2024, 5, 8)


def test_business_days():
    assert business_days_between(date(2024, 5, 3), date(2024, 5, 10)) == 5


def test_receipt():
    cart = Cart(currency="USD")
    cart.add("Tea", 4)
    assert render_receipt(cart).splitlines()[-1].endswith("$4.00")
