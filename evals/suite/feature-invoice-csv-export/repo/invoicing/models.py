from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Customer:
    name: str
    region: str  # tax region code, e.g. "DE" or "US-CA"


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price: Decimal
    taxable: bool = True

    @property
    def amount(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass
class Invoice:
    number: str
    customer: Customer
    issued: date
    items: list[LineItem] = field(default_factory=list)
    discount: Decimal = Decimal("0")  # fraction of the subtotal, 0..1
