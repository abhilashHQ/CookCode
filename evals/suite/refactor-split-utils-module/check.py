import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import importlib
from datetime import date
import pytest

import shopkit
from shopkit import Cart, format_money, parse_money, render_receipt
from shopkit.receipts import refund_amount

LAYOUT = {
    "shopkit.text": ["slug", "truncate"],
    "shopkit.money": ["to_cents", "format_money", "parse_money"],
    "shopkit.dates": ["is_business_day", "add_business_days", "business_days_between"],
}

def test_layout():
    for module, names in LAYOUT.items():
        mod = importlib.import_module(module)
        for name in names:
            fn = getattr(mod, name)
            assert fn.__module__ == module, f"{name} defined in {fn.__module__}"

def test_utils_gone():
    with pytest.raises(ImportError):
        importlib.import_module("shopkit.utils")

def test_public_api():
    assert set(shopkit.__all__) == {"Cart", "render_receipt", "format_money", "parse_money"}
    assert format_money.__module__ == "shopkit.money"

def test_text():
    from shopkit.text import slug, truncate
    assert slug("Crème Brûlée Mix!") == "creme-brulee-mix"
    assert truncate("abcdefghij", 6) == "abcde…" and truncate("abc", 6) == "abc"

def test_money():
    from shopkit.money import to_cents
    assert parse_money("€1,234.50") == 123450
    assert format_money(123450) == "€1,234.50"
    assert format_money(-5, "CHF") == "-CHF 0.05"
    assert to_cents("0.125") == 13
    with pytest.raises(ValueError):
        parse_money("free")

def test_dates():
    from shopkit.dates import add_business_days, business_days_between, is_business_day
    assert business_days_between(date(2024, 5, 3), date(2024, 5, 10)) == 5
    assert business_days_between(date(2024, 5, 10), date(2024, 5, 3)) == -5
    assert add_business_days(date(2024, 5, 6), -1) == date(2024, 5, 3)
    assert not is_business_day(date(2024, 5, 4))

def test_cart():
    cart = Cart()
    cart.add("Coffee Beans", "12.90", 2)
    cart.add("coffee beans", "12.90")
    assert cart.total() == "€38.70"
    assert cart.delivery_date(date(2024, 5, 3)) == date(2024, 5, 8)
    cart.remove("COFFEE BEANS")
    assert cart.total_cents == 0

def test_receipt_and_refund():
    cart = Cart(currency="USD")
    cart.add("Tea", 4)
    cart.add("A very long product name indeed", "1.5", 2)
    out = render_receipt(cart).splitlines()
    assert out[0] == "2x a-very-long-prod…       $3.00"
    assert out[-1].endswith("$7.00")
    assert refund_amount("$2.50", cart) == 250
    with pytest.raises(ValueError):
        refund_amount("$9.00", cart)
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


if (WORKDIR / "shopkit" / "utils.py").exists() or (WORKDIR / "shopkit" / "utils").exists():
    fail("shopkit/utils.py still exists")

for path in WORKDIR.rglob("*.py"):
    if ".git" in path.parts:
        continue
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError as exc:
        fail(f"{path.relative_to(WORKDIR)} does not parse: {exc}")
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom):
            mods = [node.module or ""] + [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        if any(m == "utils" or m.endswith(".utils") for m in mods):
            fail(f"{path.relative_to(WORKDIR)} still imports utils")

tests = WORKDIR / "tests" / "test_shopkit.py"
if not tests.is_file():
    fail("tests/test_shopkit.py was deleted")
for name in ("test_slug", "test_truncate", "test_money_roundtrip", "test_cart_total_and_delivery", "test_business_days", "test_receipt"):
    if f"def {name}(" not in tests.read_text():
        fail(f"tests/test_shopkit.py lost {name}")

run_hidden_tests(HIDDEN)
sys.exit(0)
