"""Runs suite tasks: a fresh git copy of the fixture, the agent unattended, the hidden check."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from harness import __version__
from harness.agent.instructions import load_instructions, render_system
from harness.agent.loop import Agent, AgentConfig
from harness.agent.tools import ToolRegistry
from harness.cli.app import BASE_PROMPT, PRESETS, SECRET_ENV, UNATTENDED, make_provider
from harness.cli.headless import final_text
from harness.core.events import Limits
from harness.core.features import Features
from harness.core.ids import new_id
from harness.core.strategy import Strategy
from harness.env.local import LocalEnvironment
from harness.evals.suite import SuiteTask, suite_sha256
from harness.providers.base import Provider
from harness.providers.cassette import Cassette, RecordingTransport, ReplayTransport
from harness.redact import Redactor
from harness.store.checkpoints import FileCheckpointStore
from harness.store.jsonl import JsonlSessionStore
from harness.tools import default_tools

Mode = Literal["live", "record", "replay", "oracle"]

# A fixed location, so the workspace path in the system prompt is the same when a cassette is
# recorded and when it is replayed.
WORK_ROOT = Path(tempfile.gettempdir()) / "harness-suite"
CHECK_TIMEOUT_S = 120.0
SOLUTION_TIMEOUT_S = 120.0
GIT_IDENTITY = ("-c", "user.name=suite", "-c", "user.email=suite@localhost")
FIXTURE_EPOCH = 1_767_225_600  # 2026-01-01T00:00:00Z
FIXTURE_DATE = f"@{FIXTURE_EPOCH} +0000"


class AttemptResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    kind: str
    attempt: int
    passed: bool
    reason: str
    status: str
    turns: int = 0
    session_id: str | None = None
    wall_s: float
    drift: list[str] = []


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset: str = "local-suite"
    suite_sha256: str
    harness_version: str = __version__
    harness_commit: str
    mode: Mode
    provider: str
    model: str
    strategy: str = "raw"
    features: str = "base"
    attempts: int
    concurrency: int
    tasks: list[str]
    started_at: datetime


def harness_commit() -> str:
    here = Path(__file__).parent

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=here, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}-dirty" if dirty else commit


def prepare(task: SuiteTask, attempt: int) -> Path:
    """The same bytes, commit id and timestamps every time, so a listing the agent runs reads the
    same when a cassette is recorded and when it is replayed."""
    work = WORK_ROOT / task.id / str(attempt)
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(task.repo, work)
    env = {**os.environ, "GIT_AUTHOR_DATE": FIXTURE_DATE, "GIT_COMMITTER_DATE": FIXTURE_DATE}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(
            ["git", *GIT_IDENTITY, "-c", "commit.gpgsign=false", *args],
            cwd=work,
            env=env,
            capture_output=True,
            check=True,
        )
    for path in [*work.rglob("*"), work]:
        os.utime(path, (FIXTURE_EPOCH, FIXTURE_EPOCH), follow_symlinks=False)
    return work


def run_check(task: SuiteTask, work: Path, answer: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, str(task.check), str(work), str(answer)],
            cwd=task.dir,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, lines[-1] if lines else ""


def run_solution(task: SuiteTask, work: Path) -> str:
    proc = subprocess.run(
        ["bash", str(task.solution)],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=SOLUTION_TIMEOUT_S,
    )
    return proc.stdout


class Run:
    def __init__(
        self,
        out: Path,
        *,
        mode: Mode,
        preset: str,
        model: str,
        strategy: Strategy | None = None,
        features: Features | None = None,
        provider_factory: Callable[..., Provider] | None = None,
    ) -> None:
        self.out = out
        self.strategy = strategy or Strategy()
        self.features = features or Features()
        self.mode: Mode = mode
        self.preset = preset
        self.model = model
        self.base_url, self.key_env, _ = PRESETS[preset]
        self.make = provider_factory or make_provider
        secrets = Redactor.from_env(SECRET_ENV, os.environ)
        self.store = JsonlSessionStore(out / "sessions", secrets)

    def provider(self, task: SuiteTask) -> tuple[Provider, Cassette | None, ReplayTransport | None]:
        if self.mode == "replay":
            transport = ReplayTransport(Cassette.load(task.cassette), strict=False)
            source = self.make(
                self.preset,
                api_key="replay",
                base_url=self.base_url,
                transport=transport,
                backoff_base_s=0.0,
            )
            return source, None, transport
        key = os.environ.get(self.key_env)
        if self.mode == "record":
            cassette = Cassette()
            source = self.make(
                self.preset,
                api_key=key,
                base_url=self.base_url,
                transport=RecordingTransport(cassette),
            )
            return source, cassette, None
        return self.make(self.preset, api_key=key, base_url=self.base_url), None, None

    async def attempt(self, task: SuiteTask, attempt: int) -> AttemptResult:
        started = time.monotonic()
        work = await asyncio.to_thread(prepare, task, attempt)
        answer = self.out / "answers" / f"{task.id}-{attempt}.txt"
        answer.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "oracle":
            answer.write_text(await asyncio.to_thread(run_solution, task, work))
            passed, reason = await asyncio.to_thread(run_check, task, work, answer)
            return AttemptResult(
                task=task.id,
                kind=task.spec.kind,
                attempt=attempt,
                passed=passed,
                reason=reason,
                status="oracle",
                wall_s=time.monotonic() - started,
            )
        provider, cassette, replay = self.provider(task)
        env = LocalEnvironment(work)
        session_id = new_id()
        log = self.store.open(session_id, create=True)
        try:
            instructions = await load_instructions(env, ".")
            agent = Agent(
                log=log,
                provider=provider,
                env=env,
                tools=ToolRegistry(
                    default_tools(recall=self.strategy.masks, jobs=self.features.jobs)
                ),
                config=AgentConfig(
                    model=self.model,
                    system_prompt=render_system(
                        BASE_PROMPT.format(root=env.root, cwd=".") + UNATTENDED, instructions
                    ),
                    limits=Limits(max_turns=task.spec.max_turns, max_wall_s=task.spec.max_wall_s),
                    mode="yolo",
                    strategy=self.strategy,
                ),
                checkpoints=FileCheckpointStore(self.out / "checkpoints" / session_id),
            )
            outcome = await agent.run(task.spec.prompt)
            answer.write_text(final_text(log.read()))
        finally:
            log.close()
        if cassette is not None:
            cassette.save(task.cassette, Redactor([os.environ.get(self.key_env) or ""]))
        drift: list[str] = []
        if replay is not None:
            drift = list(replay.drift)
            unused = len(Cassette.load(task.cassette).interactions) - replay.position
            if unused:
                drift.append(f"{unused} recorded request(s) were never made")
        passed, reason = await asyncio.to_thread(run_check, task, work, answer)
        return AttemptResult(
            task=task.id,
            kind=task.spec.kind,
            attempt=attempt,
            passed=passed,
            reason=reason,
            status=outcome.status,
            turns=outcome.turns,
            session_id=session_id,
            wall_s=time.monotonic() - started,
            drift=drift,
        )


async def run_suite(
    tasks: list[SuiteTask],
    out: Path,
    *,
    mode: Mode,
    preset: str,
    model: str,
    attempts: int = 1,
    concurrency: int = 1,
    strategy: Strategy | None = None,
    features: Features | None = None,
    provider_factory: Callable[..., Provider] | None = None,
    on_result: Callable[[AttemptResult], None] | None = None,
) -> list[AttemptResult]:
    if mode in ("record", "replay") and attempts != 1:
        raise ValueError(f"{mode} runs one attempt per task")
    if mode == "replay":
        missing = [task.id for task in tasks if not task.cassette.exists()]
        if missing:
            raise ValueError(f"no cassette for: {', '.join(missing)}")
    out.mkdir(parents=True, exist_ok=True)
    manifest = RunManifest(
        suite_sha256=suite_sha256(tasks),
        harness_commit=harness_commit(),
        mode=mode,
        provider=preset,
        model=model,
        strategy=(strategy or Strategy()).label,
        features=(features or Features()).label,
        attempts=attempts,
        concurrency=concurrency,
        tasks=[task.id for task in tasks],
        started_at=datetime.now(UTC),
    )
    (out / "run.json").write_text(manifest.model_dump_json(indent=1) + "\n")
    run = Run(
        out,
        mode=mode,
        preset=preset,
        model=model,
        strategy=strategy,
        features=features,
        provider_factory=provider_factory,
    )
    gate = asyncio.Semaphore(concurrency)
    results: list[AttemptResult] = []

    async def one(task: SuiteTask, attempt: int) -> None:
        async with gate:
            result = await run.attempt(task, attempt)
        results.append(result)
        with (out / "results.jsonl").open("a") as file:
            file.write(result.model_dump_json() + "\n")
        if on_result is not None:
            on_result(result)

    async with asyncio.TaskGroup() as group:
        for attempt in range(1, attempts + 1):
            for task in tasks:
                group.create_task(one(task, attempt))
    return results
