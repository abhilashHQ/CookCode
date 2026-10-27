"""Did a verify-first reproduction fail for the right reason?

A reproduction is right when it fails on the original code and passes on the reference
solution. Both are rebuilt from the fixture plus the files the agent created (its test or
script), so the judgment is about the reproduction, never about the agent's own fix."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cookcode.evals.runner import AttemptResult, work_dir
from cookcode.evals.stats import read_session
from cookcode.evals.suite import SuiteTask
from cookcode.tools.verify import registered

REPRO_TIMEOUT_S = 120


class ReproVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    attempt: int
    solved: bool
    command: str | None = None
    fails_before: bool | None = None
    passes_after: bool | None = None

    @property
    def right_reason(self) -> bool:
        return bool(self.fails_before and self.passes_after)


def _created(task: SuiteTask, work: Path) -> list[Path]:
    return [
        path.relative_to(work)
        for path in work.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(work).parts
        and "__pycache__" not in path.parts
        and not (task.repo / path.relative_to(work)).exists()
    ]


def _fails(command: str, cwd: Path) -> bool:
    try:
        proc = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True,
                              timeout=REPRO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return True
    return proc.returncode != 0


def judge(task: SuiteTask, result: AttemptResult, sessions: Path) -> ReproVerdict:
    verdict = ReproVerdict(task=task.id, attempt=result.attempt, solved=result.passed)
    if result.session_id is None:
        return verdict
    repro = registered(read_session(sessions / f"{result.session_id}.jsonl"))
    if repro is None:
        return verdict
    command, cwd = repro
    work = work_dir(task, result.attempt)
    if not work.is_dir():
        raise ValueError(f"{work} is gone; judge a run before its tasks run again")
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = Path(tmp) / "repo"
        shutil.copytree(task.repo, rebuilt)
        for relative in _created(task, work):
            (rebuilt / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / relative, rebuilt / relative)
        where = rebuilt / cwd
        before = _fails(command, where)
        subprocess.run(["bash", str(task.solution)], cwd=rebuilt, capture_output=True,
                       timeout=REPRO_TIMEOUT_S)
        after = not _fails(command, where)
    return verdict.model_copy(
        update={"command": command, "fails_before": before, "passes_after": after}
    )


def render(verdicts: list[ReproVerdict]) -> str:
    with_repro = [v for v in verdicts if v.command is not None]
    right = sum(v.right_reason for v in with_repro)
    lines = [
        f"{v.task:<40} #{v.attempt}  {'solved' if v.solved else 'failed':<6}  "
        + ("no reproduction" if v.command is None else
           f"before {'fails' if v.fails_before else 'PASSES'}, "
           f"after {'passes' if v.passes_after else 'FAILS'}  {v.command[:60]}")
        for v in verdicts
    ]
    share = f"{right / len(with_repro):.0%}" if with_repro else "n/a"
    lines.append(
        f"{len(with_repro)}/{len(verdicts)} attempts registered a reproduction; "
        f"{right} failed for the right reason ({share}); "
        f"{sum(v.solved for v in verdicts)}/{len(verdicts)} solved"
    )
    return "\n".join(lines)
