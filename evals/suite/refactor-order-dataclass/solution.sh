set -euo pipefail
cat > orders/models.py <<'EOF'
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Order:
    order_id: str
    sku: str
    quantity: int
    unit_price: Decimal

    @property
    def total(self) -> Decimal:
        return self.quantity * self.unit_price
EOF
python3 - <<'EOF'
from pathlib import Path

def edit(path, *pairs):
    p = Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert old in s, (path, old)
        s = s.replace(old, new)
    p.write_text(s)

edit("orders/parsing.py",
     ("from decimal import Decimal, InvalidOperation\n",
      "from decimal import Decimal, InvalidOperation\n\nfrom .models import Order\n"),
     ("into (order_id, sku, quantity, unit_price)", "into an Order"),
     ("return (order_id, sku.upper(), int(qty), Decimal(price))",
      "return Order(order_id, sku.upper(), int(qty), Decimal(price))"))
edit("orders/summary.py",
     ("revenue[order[1]] += order[2] * order[3]", "revenue[order.sku] += order.total"),
     ("sum(order[2] for order in orders)", "sum(order.quantity for order in orders)"),
     ("units[order[1]] += order[2]", "units[order.sku] += order.quantity"),
     ("max(orders, key=lambda o: o[2] * o[3])", "max(orders, key=lambda o: o.total)"),
     ("return best[0]", "return best.order_id"))
edit("orders/export.py",
     ('"order_id": o[0]', '"order_id": o.order_id'),
     ('"sku": o[1]', '"sku": o.sku'),
     ('"quantity": o[2]', '"quantity": o.quantity'),
     ('f"{o[3]:.2f}"', 'f"{o.unit_price:.2f}"'),
     ('f"{o[2] * o[3]:.2f}"', 'f"{o.total:.2f}"'))
edit("orders/__init__.py",
     ("from .export import to_rows, to_tsv\n", "from .export import to_rows, to_tsv\nfrom .models import Order\n"),
     ('    "parse_order", "parse_orders",\n', '    "Order", "parse_order", "parse_orders",\n'))
edit("tests/test_orders.py",
     ("assert order[1] == \"SOCK-L\"", "assert order.sku == \"SOCK-L\""),
     ("assert order[2] * order[3] == Decimal(\"12.75\")", "assert order.total == Decimal(\"12.75\")"))
EOF
