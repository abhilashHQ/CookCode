import filecmp
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
SCRIPT = WORKDIR / "bin" / "backup.sh"

FILES = {
    "Q1 report.pdf": "%PDF-1.4 quarterly numbers\n",
    "notes.md": "# notes\n",
    "budget  2024 (final).xlsx": "spreadsheet bytes\n",
    "-starts-with-dash.txt": "dash\n",
}
OLD = ["20230101-020000", "20230102-020000", "20230103-020000", "20230104-020000", "20230105-020000"]


def fail(msg):
    print(msg)
    sys.exit(1)


def run(src, dest, keep, cwd):
    return subprocess.run(["bash", str(SCRIPT), str(src), str(dest), str(keep)],
                          cwd=cwd, capture_output=True, text=True, timeout=60)


if not SCRIPT.is_file():
    fail("bin/backup.sh is missing")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    src = tmp / "Shared Docs" / "finance dept"
    dest = tmp / "backups" / "Team Drive"
    src.mkdir(parents=True)
    for name, body in FILES.items():
        (src / name).write_text(body)
    for name in OLD:
        (dest / name).mkdir(parents=True)
        (dest / name / "old.txt").write_text(name)
    decoy = tmp / "backups" / "Team"
    decoy.mkdir()
    (decoy / "keep-me.txt").write_text("unrelated")
    cwd = tmp / "cwd"
    cwd.mkdir()

    proc = run(src, dest, 3, cwd)
    if proc.returncode != 0:
        fail(f"backup.sh exited {proc.returncode}: {(proc.stderr.strip().splitlines() or [''])[-1]}")

    snapshots = sorted(p.name for p in dest.iterdir() if p.is_dir())
    if len(snapshots) != 3:
        fail(f"expected 3 snapshots after pruning with KEEP=3, found {snapshots}")
    if snapshots[:2] != OLD[-2:]:
        fail(f"pruning removed the wrong snapshots, left {snapshots}")
    new = dest / snapshots[2]
    copied = sorted(p.name for p in new.iterdir())
    if copied != sorted(FILES):
        fail(f"snapshot contents wrong: {copied}")
    for name in FILES:
        if not filecmp.cmp(src / name, new / name, shallow=False):
            fail(f"{name} not copied byte-for-byte")
    if not (decoy / "keep-me.txt").exists():
        fail("pruning deleted a directory outside DEST")
    if any(cwd.iterdir()):
        fail("script left files in the working directory")

    empty_src = tmp / "empty source"
    empty_src.mkdir()
    dest2 = tmp / "second dest"
    proc = run(empty_src, dest2, 5, cwd)
    if proc.returncode != 0:
        fail("backup.sh fails on an empty source directory")

sys.exit(0)
