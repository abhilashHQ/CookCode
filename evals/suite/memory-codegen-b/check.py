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


def entity(name, fields):
    schema = json.loads((WORKDIR / "schema.json").read_text())
    if schema.get(name) is None or not set(fields) <= set(schema[name]):
        fail(f"schema.json has no {name} with {fields}")
    keeps("test_app.py", "test_generated_file_is_fresh")
    pytest("test_app.py")
    code, out = run("-c", f"import models_gen as m; m.{name}(**{{f: 'x' for f in m.{name}.__dataclass_fields__}})")
    if code != 0:
        fail(f"models_gen.{name} unusable: {out[-200:]}")
    code, out = run("-c", f"import models_gen as m; print(sorted(m.{name}.__dataclass_fields__))")
    if code != 0 or not all(repr(f) in out for f in fields):
        fail(f"models_gen.{name} lacks {fields}")
entity("Product", ["sku", "price"])

sys.exit(0)
