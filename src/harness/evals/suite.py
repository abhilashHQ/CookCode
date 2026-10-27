"""The local regression suite: fixture repos, each with a prompt, a hidden check and a solution."""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

Kind = Literal["bugfix", "feature", "refactor", "investigate", "memory"]


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Kind
    prompt: str
    max_turns: int
    max_wall_s: float
    after: str | None = None


@dataclass(frozen=True)
class SuiteTask:
    id: str
    dir: Path
    spec: TaskSpec

    @property
    def repo(self) -> Path:
        """A task that runs after another starts from a fresh copy of that task's fixture, so
        what it shares with the first session is memory, never the first session's edits."""
        if self.spec.after is not None:
            return self.dir.parent / self.spec.after / "repo"
        return self.dir / "repo"

    @property
    def check(self) -> Path:
        return self.dir / "check.py"

    @property
    def solution(self) -> Path:
        return self.dir / "solution.sh"

    @property
    def cassette(self) -> Path:
        return self.dir / "cassette.json"


def load_suite(root: Path, only: list[str] | None = None) -> list[SuiteTask]:
    root = root.resolve()
    tasks = [
        SuiteTask(
            id=path.parent.name,
            dir=path.parent,
            spec=TaskSpec.model_validate(tomllib.loads(path.read_text())),
        )
        for path in sorted(root.glob("*/task.toml"))
    ]
    if only is None:
        return tasks
    known = {task.id for task in tasks}
    missing = sorted(set(only) - known)
    if missing:
        raise ValueError(f"unknown task(s): {', '.join(missing)}")
    return [task for task in tasks if task.id in only]


def chains(tasks: list[SuiteTask]) -> list[list[SuiteTask]]:
    """Selected tasks grouped so each runs after the task it names; an attempt runs a chain in
    order, sharing one memory directory. A task whose predecessor is not selected runs alone."""
    by_id = {task.id: task for task in tasks}
    followers = {task.spec.after for task in tasks if task.spec.after in by_id}
    result: list[list[SuiteTask]] = []
    for task in tasks:
        if task.spec.after in by_id:
            continue
        chain = [task]
        while chain[-1].id in followers:
            chain.append(next(t for t in tasks if t.spec.after == chain[-1].id))
        result.append(chain)
    return result


def suite_sha256(tasks: list[SuiteTask]) -> str:
    """Pins the suite's content, cassettes excluded, so a run names exactly what it ran."""
    digest = hashlib.sha256()
    for task in tasks:
        for path in sorted(task.dir.rglob("*")):
            if path.is_file() and path != task.cassette and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(task.dir.parent)).encode() + b"\0")
                digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()
