from decimal import ROUND_HALF_UP, Decimal

from pricing import add_tax, apply_discount, line_total


def cart_total(lines, discount_percent=0, tax_rate="0"):
    """``lines`` is an iterable of (unit_price, quantity) pairs."""
    subtotal = sum((line_total(price, qty) for price, qty in lines), Decimal("0"))
    return add_tax(apply_discount(subtotal, discount_percent), tax_rate)


def split_evenly(total, people):
    """Split ``total`` into ``people`` shares; the last share absorbs the rounding remainder."""
    if people < 1:
        raise ValueError("need at least one person")
    total = Decimal(total)
    share = (total / people).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    shares = [share] * people
    shares[-1] = total - share * (people - 1)
    return shares


def tip(amount, percent):
    return (Decimal(amount) * Decimal(percent) / 100).quantize(Decimal("1.00"), ROUND_HALF_UP)
