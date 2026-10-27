from decimal import Decimal

import pytest

from orders import largest_order, parse_order, parse_orders, revenue_by_sku, to_tsv, top_skus, total_units

DATA = """# daily export
A-1, mug-blue, 2, 8.50
A-2, tee-m, 1, 19.00
A-3, MUG-BLUE, 5, 8.00
"""


def test_parse_order():
    order = parse_order("A-9, sock-l, 3, 4.25")
    assert order[1] == "SOCK-L"
    assert order[2] * order[3] == Decimal("12.75")


def test_malformed():
    with pytest.raises(ValueError):
        parse_order("A-9, sock-l, three, 4.25")


def test_summary():
    orders = parse_orders(DATA)
    assert revenue_by_sku(orders) == {"MUG-BLUE": Decimal("57.00"), "TEE-M": Decimal("19.00")}
    assert total_units(orders) == 8
    assert top_skus(orders, 1) == [("MUG-BLUE", 7)]
    assert largest_order(orders) == "A-3"


def test_tsv():
    assert to_tsv(parse_orders(DATA)).splitlines()[2] == "A-2\tTEE-M\t1\t19.00\t19.00"
