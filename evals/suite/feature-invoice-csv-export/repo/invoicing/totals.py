from decimal import ROUND_HALF_UP, Decimal

from .tax import rate_for

CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def subtotal(invoice) -> Decimal:
    gross = sum((item.amount for item in invoice.items), Decimal("0"))
    return _money(gross * (1 - invoice.discount))


def tax(invoice) -> Decimal:
    taxable = sum((item.amount for item in invoice.items if item.taxable), Decimal("0"))
    return _money(taxable * (1 - invoice.discount) * rate_for(invoice.customer.region))


def total(invoice) -> Decimal:
    return subtotal(invoice) + tax(invoice)
