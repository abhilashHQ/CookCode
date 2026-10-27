import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal

from invoicing.models import Customer, Invoice, LineItem
from invoicing.report import render_text, to_csv

EXPECTED = (
    "number,customer,issued,subtotal,tax,total\n"
    'INV-1001,"Acme, Inc.",2024-05-02,109.97,11.39,121.36\n'
    "INV-1002,Blue Fox Ltd,2024-05-03,180.00,13.05,193.05\n"
    "INV-1003,Zeta GmbH,2024-05-07,0.05,0.01,0.06\n"
    'INV-1004,"Kai ""The Hammer"" Ltd",2024-05-09,40.00,0.00,40.00\n'
)

RAW = [
    {"number": "INV-1001", "customer": {"name": "Acme, Inc.", "region": "DE"}, "issued": "2024-05-02",
     "items": [{"description": "Widget", "quantity": 3, "unit_price": "19.99"},
               {"description": "Shipping", "quantity": 1, "unit_price": "50.00", "taxable": False}]},
    {"number": "INV-1002", "customer": {"name": "Blue Fox Ltd", "region": "us-ca"}, "issued": "2024-05-03",
     "discount": "0.10", "items": [{"description": "Consulting", "quantity": 2, "unit_price": "100"}]},
    {"number": "INV-1003", "customer": {"name": "Zeta GmbH", "region": "NZ"}, "issued": "2024-05-07",
     "items": [{"description": "Sticker", "quantity": 1, "unit_price": "0.05"}]},
    {"number": "INV-1004", "customer": {"name": 'Kai "The Hammer" Ltd', "region": "US-OR"}, "issued": "2024-05-09",
     "items": [{"description": "Hammer", "quantity": 4, "unit_price": "10"}]},
]

def build():
    out = []
    for e in RAW:
        items = [LineItem(i["description"], i["quantity"], Decimal(i["unit_price"]), i.get("taxable", True)) for i in e["items"]]
        out.append(Invoice(e["number"], Customer(**e["customer"]), date.fromisoformat(e["issued"]), items,
                           Decimal(e.get("discount", "0"))))
    return out

def test_to_csv_exact():
    assert to_csv(build()) == EXPECTED

def test_to_csv_empty():
    assert to_csv([]) == "number,customer,issued,subtotal,tax,total\n"

def test_to_csv_order_preserved():
    invoices = build()[::-1]
    rows = to_csv(invoices).splitlines()[1:]
    assert [r.split(",")[0] for r in rows] == ["INV-1004", "INV-1003", "INV-1002", "INV-1001"]

def test_text_report_unchanged():
    text = render_text(build())
    assert "INV-1002" in text and "193.05" in text

def run_cli(tmp_path, *args):
    path = tmp_path / "invoices.json"
    path.write_text(json.dumps(RAW))
    return subprocess.run([sys.executable, "-m", "invoicing.cli", str(path), *args],
                          capture_output=True, text=True, env=dict(os.environ))

def test_cli_csv(tmp_path):
    proc = run_cli(tmp_path, "--format", "csv")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == EXPECTED

def test_cli_default_text(tmp_path):
    proc = run_cli(tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == render_text(build())

def test_cli_rejects_unknown_format(tmp_path):
    assert run_cli(tmp_path, "--format", "xlsx").returncode == 2
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


run_hidden_tests(HIDDEN)
sys.exit(0)
