from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def line_total(unit_price, quantity):
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    total = Decimal(unit_price) * quantity
    return total.quantize(CENTS, rounding=ROUND_HALF_UP)


def apply_discount(amount, percent):
    if not 0 <= Decimal(percent) <= 100:
        raise ValueError("percent must be between 0 and 100")
    discounted = Decimal(amount) * (Decimal(100) - Decimal(percent)) / Decimal(100)
    return discounted.quantize(Decimal("0.01"), ROUND_HALF_UP)


def add_tax(amount, rate):
    taxed = Decimal(amount) * (1 + Decimal(rate))
    return Decimal(taxed).quantize(CENTS, rounding=ROUND_HALF_UP)
