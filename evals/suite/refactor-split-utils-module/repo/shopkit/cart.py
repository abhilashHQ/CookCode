from dataclasses import dataclass, field
from datetime import date

from .utils import add_business_days, format_money, slug, to_cents


@dataclass
class Cart:
    currency: str = "EUR"
    lines: dict = field(default_factory=dict)

    def add(self, name, price, quantity=1):
        key = slug(name)
        cents, qty = self.lines.get(key, (to_cents(price), 0))
        self.lines[key] = (cents, qty + quantity)
        return key

    def remove(self, name):
        self.lines.pop(slug(name), None)

    @property
    def total_cents(self):
        return sum(cents * qty for cents, qty in self.lines.values())

    def total(self):
        return format_money(self.total_cents, self.currency)

    def delivery_date(self, ordered: date, lead_days=3):
        return add_business_days(ordered, lead_days)
