import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import dataclasses
from decimal import Decimal
import pytest

import orders
from orders import largest_order, parse_order, parse_orders, revenue_by_sku, to_rows, to_tsv, top_skus, total_units
from orders.models import Order

DATA = """# daily export
A-1, mug-blue, 2, 8.50
A-2, tee-m, 1, 19.00
  # comment
A-3, MUG-BLUE, 5, 8.00
A-4, cap, 3, 12.333
"""

def test_dataclass_shape():
    assert dataclasses.is_dataclass(Order)
    assert [f.name for f in dataclasses.fields(Order)] == ["order_id", "sku", "quantity", "unit_price"]
    o = Order("X", "SKU", 2, Decimal("1.50"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.quantity = 3
    assert o.total == Decimal("3.00")
    assert orders.Order is Order

def test_parse_returns_order():
    o = parse_order("A-9, sock-l, 3, 4.25")
    assert isinstance(o, Order) and not isinstance(o, tuple)
    assert o == Order("A-9", "SOCK-L", 3, Decimal("4.25"))
    assert o.total == Decimal("12.75")

@pytest.mark.parametrize("bad", ["A-9, sock-l, three, 4.25", "A-9, sock-l, 3", ", sku, 1, 1", "A, sku, 1, abc"])
def test_malformed(bad):
    with pytest.raises(ValueError):
        parse_order(bad)

def test_summary():
    parsed = parse_orders(DATA)
    assert len(parsed) == 4
    assert revenue_by_sku(parsed) == {"CAP": Decimal("36.999"), "MUG-BLUE": Decimal("57.00"), "TEE-M": Decimal("19.00")}
    assert list(revenue_by_sku(parsed)) == ["CAP", "MUG-BLUE", "TEE-M"]
    assert total_units(parsed) == 11
    assert top_skus(parsed) == [("MUG-BLUE", 7), ("CAP", 3), ("TEE-M", 1)]
    assert largest_order(parsed) == "A-3"
    assert largest_order([]) is None

def test_export():
    parsed = parse_orders(DATA)
    assert to_rows(parsed)[3] == {"order_id": "A-4", "sku": "CAP", "quantity": 3, "unit_price": "12.33", "total": "37.00"}
    assert to_tsv(parsed) == (
        "order_id\tsku\tquantity\tunit_price\ttotal\n"
        "A-1\tMUG-BLUE\t2\t8.50\t17.00\n"
        "A-2\tTEE-M\t1\t19.00\t19.00\n"
        "A-3\tMUG-BLUE\t5\t8.00\t40.00\n"
        "A-4\tCAP\t3\t12.33\t37.00\n"
    )
'''


def fail(msg):
    print(msg)
    sys.exit(1)


def run_hidden_tests(source):
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_hidden.py"
        test_file.write_text(source)
        env = dict(os.environ, PYTHONPATH=str(WORKDIR), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=180,
        )
    if proc.returncode != 0:
        lines = proc.stdout.splitlines()
        summary = [l for l in lines if l.startswith(("FAILED", "ERROR"))] or (proc.stdout + proc.stderr).strip().splitlines() or ["no output"]
        fail("hidden tests failed: " + summary[0][:300])


if not (WORKDIR / "orders" / "models.py").is_file():
    fail("orders/models.py does not exist")

for rel in ("orders/summary.py", "orders/export.py", "tests/test_orders.py"):
    path = WORKDIR / rel
    if not path.is_file():
        fail(f"{rel} is missing")
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
                and isinstance(node.value, ast.Name) and node.value.id in {"o", "order", "best"}):
            fail(f"{rel} still indexes orders positionally (line {node.lineno})")

tests = (WORKDIR / "tests" / "test_orders.py").read_text()
for name in ("test_parse_order", "test_malformed", "test_summary", "test_tsv"):
    if f"def {name}(" not in tests:
        fail(f"tests/test_orders.py lost {name}")

run_hidden_tests(HIDDEN)
sys.exit(0)
