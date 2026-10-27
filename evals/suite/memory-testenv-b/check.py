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


ENV = {"APP_ENV": "test"}
pytest("test_inventory.py", env=ENV)
def behaves(code_text):
    code, out = run("-c", "from inventory import Inventory\ninv = Inventory()\n" + code_text, env=ENV)
    if code != 0:
        fail(out.splitlines()[-1][:300] if out else "failed")
behaves("""
import pytest
for bad in (0, -3):
    with pytest.raises(ValueError):
        inv.restock('a', bad)
inv.restock('a', 2); assert inv.count('a') == 2
""")

sys.exit(0)
