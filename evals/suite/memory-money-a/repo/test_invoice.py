from invoice import Invoice
from money import percent


def test_total_is_integer_cents():
    inv = Invoice()
    inv.add("widget", 1999)
    assert inv.total() == 1999 and isinstance(inv.total(), int)
    assert inv.render() == "widget: $19.99\nTotal: $19.99"


def test_percent_rounds_half_up():
    assert percent(1050, 5) == 53
