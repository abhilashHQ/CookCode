import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from pagination import paginate
import api

def test_first_page_starts_at_first_item():
    assert paginate(list(range(10)), page=1, per_page=4).items == [0, 1, 2, 3]

def test_page_count_rounds_up():
    assert paginate(list(range(10)), per_page=4).pages == 3

def test_empty_input_has_one_page():
    page = paginate([], page=1)
    assert page.items == [] and page.pages == 1 and not page.has_next

def test_rejects_page_zero():
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=0)

def test_every_item_appears_exactly_once():
    items = list(range(57))
    seen = []
    for n in range(1, 4):
        seen += paginate(items, page=n, per_page=20).items
    assert seen == items

def test_last_page_is_partial():
    page = paginate(list(range(57)), page=3, per_page=20)
    assert page.items == list(range(40, 57))
    assert not page.has_next and page.has_prev

def test_page_past_end_is_empty():
    assert paginate(list(range(57)), page=4, per_page=20).items == []

def test_api_first_page():
    resp = api.list_products({"page": "1"})
    assert resp["items"][0]["sku"] == "SKU-0001"
    assert len(resp["items"]) == 20
    assert resp["next"] == 2 and resp["prev"] is None and resp["pages"] == 3

def test_api_last_page():
    resp = api.list_products({"page": "3"})
    assert [p["sku"] for p in resp["items"]][-1] == "SKU-0057"
    assert len(resp["items"]) == 17 and resp["next"] is None
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


def require_tests(relpath, names):
    path = WORKDIR / relpath
    if not path.is_file():
        fail(f"{relpath} was deleted")
    text = path.read_text()
    missing = [n for n in names if f"def {n}(" not in text]
    if missing:
        fail(f"{relpath} lost tests: {', '.join(missing)}")


require_tests("test_pagination.py", [
    "test_first_page_starts_at_first_item", "test_page_count_rounds_up",
    "test_empty_input_has_one_page", "test_rejects_page_zero",
])
run_hidden_tests(HIDDEN)
sys.exit(0)
