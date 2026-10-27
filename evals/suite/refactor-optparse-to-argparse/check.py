import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import argparse
import subprocess
import sys
import pytest
import logfilter
from logfilter import build_parser, main

LOG = """2024-04-01T09:00:00 DEBUG app.db: opening pool
2024-04-01T09:00:01 INFO app.db: pool ready
2024-04-01T09:05:12 INFO app.http: GET /health 200
2024-04-01T09:07:44 WARNING app.http.auth: token close to expiry
2024-04-01T09:15:03 ERROR app.db: query timeout
2024-04-01T09:15:04 INFO worker: retrying job 881
2024-04-01T10:02:30 CRITICAL app.db.replica: replica lag 120s
2024-04-01T10:03:00 INFO application: started
"""

@pytest.fixture
def logfile(tmp_path):
    p = tmp_path / "app.log"
    p.write_text(LOG)
    return str(p)

def out_lines(capsys):
    return capsys.readouterr().out.splitlines()

def test_parser_type():
    assert isinstance(build_parser(), argparse.ArgumentParser)

def test_default_level(logfile, capsys):
    assert main([logfile]) in (0, None)
    assert len(out_lines(capsys)) == 7

def test_long_level(logfile, capsys):
    main(["--level", "ERROR", logfile])
    assert [l.split()[1] for l in out_lines(capsys)] == ["ERROR", "CRITICAL"]

def test_repeatable_logger(logfile, capsys):
    main(["-l", "DEBUG", "-g", "app.http", "--logger", "worker", logfile])
    lines = out_lines(capsys)
    assert len(lines) == 3 and "application" not in "".join(lines)

def test_logger_prefix_boundary(logfile, capsys):
    main(["-g", "app", logfile])
    assert not any("application" in l for l in out_lines(capsys))

def test_count_and_since(logfile, capsys):
    main(["-c", "--since", "2024-04-01T09:10:00", logfile])
    assert out_lines(capsys) == ["4"]

def test_count_zero(logfile, capsys):
    main(["--count", "-l", "CRITICAL", "-g", "worker", logfile])
    assert out_lines(capsys) == ["0"]

def test_options_after_file(logfile, capsys):
    main([logfile, "-l", "WARNING"])
    assert len(out_lines(capsys)) == 3

def test_multiple_files(logfile, capsys):
    main(["-c", logfile, logfile])
    assert out_lines(capsys) == ["14"]

@pytest.mark.parametrize("args", [["-l", "LOUD"], ["--since", "yesterday"], ["--bogus"]])
def test_bad_input_exits_2(logfile, args):
    with pytest.raises(SystemExit) as info:
        main(args + [logfile])
    assert info.value.code == 2

def test_stdin_and_help():
    proc = subprocess.run([sys.executable, logfilter.__file__, "-l", "ERROR"], input=LOG,
                          capture_output=True, text=True)
    assert proc.returncode == 0 and len(proc.stdout.splitlines()) == 2
    proc = subprocess.run([sys.executable, logfilter.__file__, "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    for flag in ("--level", "--logger", "--since", "--count", "-l", "-g", "-c"):
        assert flag in proc.stdout
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


src = WORKDIR / "logfilter.py"
if not src.is_file():
    fail("logfilter.py is missing")
tree = ast.parse(src.read_text())
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported |= {a.name.split(".")[0] for a in node.names}
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split(".")[0])
if "optparse" in imported or "optparse" in src.read_text():
    fail("logfilter.py still references optparse")
if "argparse" not in imported:
    fail("logfilter.py does not import argparse")

tests = WORKDIR / "test_logfilter.py"
if not tests.is_file():
    fail("test_logfilter.py was deleted")
for name in ("test_default_level", "test_logger_prefix", "test_count", "test_bad_level_exits"):
    if f"def {name}(" not in tests.read_text():
        fail(f"test_logfilter.py lost {name}")

run_hidden_tests(HIDDEN)
sys.exit(0)
