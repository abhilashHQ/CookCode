import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

EXPECT = {
    "scripts/deploy.sh": {"eol": "lf"},
    "tools/ci/nested/run.sh": {"eol": "lf"},
    "build.sh": {"eol": "lf"},
    "scripts/setup.bat": {"eol": "crlf"},
    "tools/win/install.cmd": {"eol": "crlf"},
    "docs/img/logo.png": {"binary": True},
    "docs/img/screenshot.jpg": {"binary": True},
    "app/main.py": {"text": "auto"},
    "README.md": {"text": "auto"},
}


def fail(msg):
    print(msg)
    sys.exit(1)


def attrs(repo, path):
    proc = subprocess.run(["git", "check-attr", "text", "eol", "binary", "diff", "--", path],
                          cwd=repo, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        fail(f"git check-attr failed: {proc.stderr.strip()}")
    out = {}
    for line in proc.stdout.splitlines():
        _, attr, value = line.rsplit(": ", 2)
        out[attr] = value
    return out


with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    shutil.copytree(WORKDIR, repo, ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for path, want in EXPECT.items():
        got = attrs(repo, path)
        if "eol" in want and got.get("eol") != want["eol"]:
            fail(f"{path}: eol is {got.get('eol')}, expected {want['eol']}")
        if "eol" in want and got.get("text") == "unset":
            fail(f"{path}: marked -text, eol would not apply")
        if want.get("binary"):
            is_binary = got.get("binary") == "set" or (got.get("text") == "unset" and got.get("diff") == "unset")
            if not is_binary:
                fail(f"{path}: not treated as binary ({got})")
        if "text" in want and got.get("text") != want["text"]:
            fail(f"{path}: text is {got.get('text')}, expected {want['text']}")
        if not want.get("binary") and got.get("binary") == "set":
            fail(f"{path}: wrongly marked binary")

sys.exit(0)
