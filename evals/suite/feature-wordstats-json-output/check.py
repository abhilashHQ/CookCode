import json
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
SCRIPT = WORKDIR / "wordstats.py"


def fail(msg):
    print(msg)
    sys.exit(1)


def run(args, stdin=""):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin,
                          capture_output=True, text=True, timeout=30, cwd=WORKDIR)


if not SCRIPT.is_file():
    fail("wordstats.py is missing")

with tempfile.TemporaryDirectory() as tmp:
    a = Path(tmp, "alpha.txt")
    a.write_text("hello world\nsecond line here\n")
    b = Path(tmp, "beta.txt")
    b.write_text("x\ny\nz")

    proc = run([str(a), str(b)])
    expected_table = [
        f"      2       5      29 {a}",
        f"      2       3       5 {b}",
        "      4       8      34 total",
    ]
    if proc.returncode != 0 or proc.stdout.splitlines() != expected_table:
        fail("default table output changed")
    proc = run(["--format", "table", str(a)])
    if proc.stdout.splitlines() != expected_table[:1]:
        fail("--format table does not match the default output")

    proc = run(["--format", "json", str(b), str(a)])
    if proc.returncode != 0:
        fail(f"--format json exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail("--format json did not print a single JSON document")
    want = {
        "files": [
            {"name": str(b), "lines": 2, "words": 3, "chars": 5},
            {"name": str(a), "lines": 2, "words": 5, "chars": 29},
        ],
        "total": {"lines": 4, "words": 8, "chars": 34},
    }
    if data != want:
        fail(f"wrong JSON for two files: {proc.stdout.strip()[:200]}")

    proc = run([str(a), "--format=json"])
    data = json.loads(proc.stdout) if proc.returncode == 0 else None
    if data != {"files": [{"name": str(a), "lines": 2, "words": 5, "chars": 29}],
                "total": {"lines": 2, "words": 5, "chars": 29}}:
        fail("single-file JSON must still include total")

    proc = run(["--format", "json"], stdin="from stdin\n")
    data = json.loads(proc.stdout) if proc.returncode == 0 else None
    if data != {"files": [{"name": "-", "lines": 1, "words": 2, "chars": 11}],
                "total": {"lines": 1, "words": 2, "chars": 11}}:
        fail("stdin JSON output is wrong")

    proc = run(["--format", "xml", str(a)])
    if proc.returncode != 2 or "usage" not in proc.stderr.lower():
        fail("unknown --format value should be an argparse usage error (exit 2)")

sys.exit(0)
