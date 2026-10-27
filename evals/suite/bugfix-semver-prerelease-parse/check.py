import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from release import next_version
from versioning import Version

def test_parse_plain():
    v = Version.parse("v1.4.2")
    assert (v.major, v.minor, v.patch, v.prerelease) == (1, 4, 2, None)

def test_parse_release_candidate():
    assert Version.parse("2.0.0-rc.1").prerelease == "rc.1"

def test_prerelease_sorts_before_release():
    assert Version.parse("1.0.0-beta") < Version.parse("1.0.0")

@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "01.2.3", "1.2.3-", "1.2.3-rc..1", "1.2.3-rc.", "1.2.3+", "1.2.3+build..1", "1.2.3-r c"])
def test_rejects_invalid(bad):
    with pytest.raises(ValueError):
        Version.parse(bad)

def test_next_version():
    assert next_version("1.4.2", "minor") == "1.5.0"

def test_next_version_from_rc():
    assert next_version("2.0.0-rc.1", "minor") == "2.1.0"
    assert next_version("2.0.0-rc.1", "patch") == "2.0.0"

def test_build_metadata_with_dots():
    v = Version.parse("1.0.0-alpha.1+build.5.sha-3f2a")
    assert v.prerelease == "alpha.1" and v.build == "build.5.sha-3f2a"
    assert str(v) == "1.0.0-alpha.1+build.5.sha-3f2a"

def test_build_only():
    v = Version.parse("1.0.0+20240301.ci-77")
    assert v.prerelease is None and v.build == "20240301.ci-77"
    assert v == Version.parse("1.0.0")

def test_spec_precedence_order():
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
             "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    parsed = [Version.parse(v) for v in chain]
    for lower, higher in zip(parsed, parsed[1:]):
        assert lower < higher, (str(lower), str(higher))
    shuffled = [parsed[i] for i in (5, 0, 7, 3, 1, 6, 2, 4)]
    assert [str(v) for v in sorted(shuffled)] == chain
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


require_tests("test_versioning.py", [
    "test_parse_plain", "test_parse_release_candidate", "test_prerelease_sorts_before_release",
    "test_rejects_invalid", "test_next_version",
])
run_hidden_tests(HIDDEN)
sys.exit(0)
