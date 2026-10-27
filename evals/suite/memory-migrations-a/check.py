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


ORIGINAL = "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n"
def columns(table, wanted):
    if (WORKDIR / "migrations" / "001_customers.sql").read_text() != ORIGINAL:
        fail("a shipped migration was edited")
    keeps("test_db.py", "test_version_matches_latest_migration")
    pytest("test_db.py")
    code, out = run("-c", f"import db; print([r[1] for r in db.connect().execute('PRAGMA table_info({table})')])")
    if code != 0 or not all(repr(c) in out for c in wanted):
        fail(f"{table} lacks {wanted}: {out[-200:]}")
    code, out = run("-c", "import db; print(db.SCHEMA_VERSION)")
    if out != "2":
        fail(f"SCHEMA_VERSION is {out}, wanted 2")
columns("customers", ["id", "name", "phone"])

sys.exit(0)
