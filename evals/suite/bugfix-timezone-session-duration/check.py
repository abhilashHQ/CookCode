import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from timetrack.report import daily_totals, render
from timetrack.sessions import load_sessions, total_minutes

def row(start, end, user="ana"):
    return {"user": user, "start": start, "end": end}

def test_minutes_in_utc():
    assert total_minutes(load_sessions([row("2024-03-01T08:00:00Z", "2024-03-01T09:15:00Z")])) == 75

def test_rejects_backwards_session():
    with pytest.raises(ValueError):
        load_sessions([row("2024-03-01T10:00:00Z", "2024-03-01T09:00:00Z")])

def test_render():
    assert render(["ana; 2024-03-01T08:00:00Z; 2024-03-01T10:30:00Z"]) == "2024-03-01  ana           2h30"

def test_example_from_report():
    sessions = load_sessions([row("2024-03-01T10:00:00+02:00", "2024-03-01T09:30:00+00:00")])
    assert total_minutes(sessions) == 90

def test_negative_offsets():
    sessions = load_sessions([row("2024-03-01T08:00:00-05:00", "2024-03-01T14:00:00+01:00")])
    assert total_minutes(sessions) == 0

def test_naive_is_utc():
    sessions = load_sessions([row("2024-03-01T08:00:00", "2024-03-01T09:30:00+01:00")])
    assert total_minutes(sessions) == 30

def test_backwards_across_offsets_rejected():
    with pytest.raises(ValueError):
        load_sessions([row("2024-03-01T09:00:00+00:00", "2024-03-01T10:30:00+02:00")])

def test_daily_totals_use_utc_date():
    sessions = load_sessions([row("2024-03-01T23:30:00-02:00", "2024-03-02T03:00:00+00:00")])
    assert daily_totals(sessions) == {("ana", "2024-03-02"): 90}

def test_render_mixed_offsets():
    out = render([
        "ana; 2024-03-01T10:00:00+02:00; 2024-03-01T09:30:00Z",
        "ben; 2024-03-01T07:00:00+05:30; 2024-03-01T02:00:00Z",
    ])
    assert out.splitlines() == ["2024-03-01  ana           1h30", "2024-03-01  ben           0h30"]
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


require_tests("tests/test_sessions.py", [
    "test_minutes_in_utc", "test_rejects_backwards_session", "test_render", "test_daily_totals_per_user",
])
run_hidden_tests(HIDDEN)
sys.exit(0)
