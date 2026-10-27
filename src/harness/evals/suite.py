"""The local regression suite: fixture repos, each with a prompt, a hidden check and a solution."""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

Kind = Literal["bugfix", "feature", "refactor", "investigate"]


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Kind
    prompt: str
    max_turns: int
    max_wall_s: float


@dataclass(frozen=True)
class SuiteTask:
    id: str
    dir: Path
    spec: TaskSpec

    @property
    def repo(self) -> Path:
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


def suite_sha256(tasks: list[SuiteTask]) -> str:
    """Pins the suite's content, cassettes excluded, so a run names exactly what it ran."""
    digest = hashlib.sha256()
    for task in tasks:
        for path in sorted(task.dir.rglob("*")):
            if path.is_file() and path != task.cassette and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(task.dir.parent)).encode() + b"\0")
                digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()
