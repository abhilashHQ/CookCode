import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()


def fail(msg):
    print(msg)
    sys.exit(1)


def run(*args, env=None):
    proc = subprocess.run([sys.executable, *args], cwd=WORKDIR, capture_output=True, text=True,
                          timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **(env or {})))
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def keeps(relpath, test):
    path = WORKDIR / relpath
    if not path.is_file() or f"def {test}(" not in path.read_text():
        fail(f"{relpath} lost {test}")


def pytest(*paths, env=None):
    code, out = run("-m", "pytest", "-q", "-p", "no:cacheprovider", *paths, env=env)
    if code != 0:
        lines = [l for l in out.splitlines() if l.startswith(("FAILED", "ERROR"))] or out.splitlines() or ["no output"]
        fail("tests failed: " + lines[0][:300])


def invoice(code_text):
    keeps("test_invoice.py", "test_total_is_integer_cents")
    pytest("test_invoice.py")
    code, out = run("-c", "from invoice import Invoice\n" + code_text)
    if code != 0:
        fail(out.splitlines()[-1][:300] if out else "failed")
invoice("""
inv = Invoice(); inv.add('a', 10050)
assert inv.total() == 10553 and isinstance(inv.total(), int), inv.total()
assert inv.render().splitlines()[-2:] == ['Service fee: $5.03', 'Total: $105.53'], inv.render()
small = Invoice(); small.add('a', 10000)
assert small.total() == 10000 and 'Service fee' not in small.render()
""")

sys.exit(0)
