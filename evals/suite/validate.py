"""Validate the regression suite.

For every task under evals/suite/:
  (a) the untouched repo/ plus an empty answer must FAIL check.py
  (b) repo/ after solution.sh (stdout saved as the answer) must PASS check.py
"""
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

SUITE = Path(__file__).resolve().parent
KINDS = ("bugfix", "feature", "refactor", "investigate", "memory")
EXPECTED = {"bugfix": 6, "feature": 6, "refactor": 6, "investigate": 6, "memory": 12}
GIT_ID = ["-c", "user.name=eval", "-c", "user.email=eval@localhost", "-c", "commit.gpgsign=false"]


def git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", *GIT_ID, "commit", "-qm", "initial", "--allow-empty"], cwd=path, check=True)


def fixture(task: Path) -> Path:
    after = tomllib.loads((task / "task.toml").read_text()).get("after")
    return SUITE / after / "repo" if after else task / "repo"


def fresh_workdir(task: Path, tmp: Path) -> Path:
    workdir = tmp / "work"
    shutil.copytree(fixture(task), workdir)
    git_init(workdir)
    return workdir


def run_check(task: Path, workdir: Path, answer: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(task / "check.py"), str(workdir), str(answer)],
        capture_output=True, text=True, timeout=300,
    )
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, (lines[-1] if lines else "")


def lint_task(task: Path) -> list[str]:
    problems = []
    for name in ("task.toml", "check.py", "solution.sh"):
        if not (task / name).exists():
            problems.append(f"missing {name}")
    if problems:
        return problems
    meta = tomllib.loads((task / "task.toml").read_text())
    if not fixture(task).is_dir():
        return [f"missing fixture {fixture(task)}"]
    if meta.get("after") and (task / "repo").exists():
        problems.append("a task that runs after another must not have its own repo/")
    kind = meta.get("kind")
    if kind not in KINDS:
        problems.append(f"bad kind {kind!r}")
    elif not task.name.startswith(kind + "-"):
        problems.append(f"id does not start with {kind}-")
    if not isinstance(meta.get("prompt"), str) or not meta["prompt"].strip():
        problems.append("empty prompt")
    if not (isinstance(meta.get("max_turns"), int) and 12 <= meta["max_turns"] <= 25):
        problems.append("max_turns out of range")
    if not (isinstance(meta.get("max_wall_s"), int) and 180 <= meta["max_wall_s"] <= 900):
        problems.append("max_wall_s out of range")
    if (fixture(task) / ".git").exists():
        problems.append("repo/ contains .git")
    files = [p for p in fixture(task).rglob("*") if p.is_file()]
    if not 1 <= len(files) <= 6:
        problems.append(f"repo/ has {len(files)} files")
    lines = sum(len(p.read_text(errors="replace").splitlines()) for p in files)
    if lines > 320:
        problems.append(f"repo/ has {lines} lines")
    return problems


def validate(task: Path) -> tuple[bool, str]:
    problems = lint_task(task)
    if problems:
        return False, "; ".join(problems)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        workdir = fresh_workdir(task, tmp)
        answer = tmp / "answer.txt"
        answer.write_text("")
        code, baseline = run_check(task, workdir, answer)
        if code == 0:
            return False, "original repo PASSES the check"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        workdir = fresh_workdir(task, tmp)
        answer = tmp / "answer.txt"
        sol = subprocess.run(
            ["bash", str(task / "solution.sh")], cwd=workdir,
            capture_output=True, text=True, timeout=300,
        )
        if sol.returncode != 0:
            return False, f"solution.sh exited {sol.returncode}: {sol.stderr.strip()[-200:]}"
        answer.write_text(sol.stdout)
        code, msg = run_check(task, workdir, answer)
        if code != 0:
            return False, f"solved repo FAILS the check: {msg}"
    return True, f"baseline fails with: {baseline}"


def main() -> int:
    tasks = sorted(p for p in SUITE.iterdir() if p.is_dir() and (p / "task.toml").exists())
    selected = sys.argv[1:]
    if selected:
        tasks = [t for t in tasks if any(s in t.name for s in selected)]
    kinds = Counter()
    failures = 0
    for task in tasks:
        ok, detail = validate(task)
        kind = task.name.split("-", 1)[0]
        kinds[kind] += 1
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {task.name:45s} {detail}")
    print()
    print(f"{len(tasks)} tasks: " + ", ".join(f"{k}={kinds[k]}" for k in KINDS))
    print(f"{len(tasks) - failures} ok, {failures} failing")
    if not selected and dict(kinds) != EXPECTED:
        print(f"suite shape is wrong: expected {EXPECTED}")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
