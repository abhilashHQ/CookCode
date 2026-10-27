import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
from decimal import Decimal
import pytest
from checkout import cart_total, split_evenly, tip
from pricing import add_tax, apply_discount, line_total, round_money

def test_line_total_rounds_half_up():
    assert line_total("0.125", 1) == Decimal("0.13")

def test_discount():
    assert apply_discount(Decimal("19.99"), 15) == Decimal("16.99")

def test_tax():
    assert add_tax(Decimal("10.00"), "0.075") == Decimal("10.75")

def test_cart_total():
    assert cart_total([("2.50", 3), ("0.99", 2)], discount_percent=10, tax_rate="0.2") == Decimal("10.24")

def test_split():
    assert split_evenly("10.00", 3) == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]
    assert split_evenly("0.05", 2) == [Decimal("0.03"), Decimal("0.02")]

def test_tip():
    assert tip("42.10", 18) == Decimal("7.58")
    assert tip("10", 12.5) == Decimal("1.25")

@pytest.mark.parametrize("value, expected", [
    (Decimal("2.675"), "2.68"), (Decimal("2.665"), "2.67"), (Decimal("-1.005"), "-1.01"),
    (Decimal("0.004999"), "0.00"), (7, "7.00"), (Decimal("1E+2"), "100.00"),
])
def test_round_money(value, expected):
    out = round_money(value)
    assert isinstance(out, Decimal) and str(out) == expected

def test_errors_preserved():
    with pytest.raises(ValueError):
        line_total("1", -1)
    with pytest.raises(ValueError):
        apply_discount(10, 101)
    with pytest.raises(ValueError):
        split_evenly(10, 0)
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


def parse(name):
    path = WORKDIR / name
    if not path.is_file():
        fail(f"{name} is missing")
    try:
        return ast.parse(path.read_text())
    except SyntaxError as exc:
        fail(f"{name} does not parse: {exc}")


def functions(tree):
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def calls(node, name):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name):
                return True
    return False


def quantize_calls(node):
    return [s for s in ast.walk(node) if isinstance(s, ast.Call)
            and isinstance(s.func, ast.Attribute) and s.func.attr == "quantize"]


pricing = parse("pricing.py")
checkout = parse("checkout.py")
pf, cf = functions(pricing), functions(checkout)
if "round_money" not in pf:
    fail("pricing.round_money is not defined at module level")
helper = pf["round_money"]
if len(quantize_calls(pricing)) != len(quantize_calls(helper)) or len(quantize_calls(checkout)) != 0:
    fail("quantize is still called outside round_money")
if len(quantize_calls(helper)) > 1:
    fail("round_money should contain the single quantize call")
for tree_funcs, names, label in ((pf, ("line_total", "apply_discount", "add_tax"), "pricing"),
                                 (cf, ("split_evenly", "tip"), "checkout")):
    for name in names:
        if name not in tree_funcs:
            fail(f"{label}.{name} is missing")
        if not calls(tree_funcs[name], "round_money"):
            fail(f"{label}.{name} does not call round_money")

tests = WORKDIR / "test_pricing.py"
if not tests.is_file():
    fail("test_pricing.py was deleted")
for name in ("test_line_total_rounds_half_up", "test_discount", "test_tax", "test_cart_total", "test_split", "test_tip"):
    if f"def {name}(" not in tests.read_text():
        fail(f"test_pricing.py lost {name}")

run_hidden_tests(HIDDEN)
sys.exit(0)
