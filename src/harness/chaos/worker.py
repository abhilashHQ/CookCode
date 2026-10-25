import argparse
import asyncio
import os
import signal
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

from harness.agent.loop import Agent, AgentConfig
from harness.agent.repair import RepairAction, plan_repairs
from harness.chaos.scenario import ScenarioProvider, chaos_tools
from harness.core.events import Event, EventBody, Limits
from harness.env.local import LocalEnvironment
from harness.store.base import SessionLog
from harness.store.jsonl import JsonlSessionStore

SESSION_ID = "s1"
TARGET = "out.txt"
LINES = ("L1", "L2", "L3")
PER_STEP = 2
PROMPT = "append the lines"
MODEL = "scenario-1"
MAX_TURNS = 20
AFTER_EVENT = "after_event"
AFTER_EFFECT = "after_effect"

Planner = Callable[[Sequence[Event]], list[RepairAction]]


class ObservedLog:
    def __init__(self, inner: SessionLog, on_append: Callable[[Event], None] | None = None) -> None:
        self._inner = inner
        self._on_append = on_append
        self.appended = 0

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    @property
    def next_seq(self) -> int:
        return self._inner.next_seq

    def append(self, body: EventBody, *, durable: bool = False) -> Event:
        event = self._inner.append(body, durable=durable)
        self.appended += 1
        if self._on_append is not None:
            self._on_append(event)
        return event

    def read(self, from_seq: int = 0) -> list[Event]:
        return self._inner.read(from_seq)

    def close(self) -> None:
        self._inner.close()


def parse_point(point: str) -> tuple[str, int]:
    kind, separator, raw = point.partition(":")
    if not separator or kind not in (AFTER_EVENT, AFTER_EFFECT) or not raw.isdecimal():
        raise ValueError(f"invalid crash point {point!r}")
    ordinal = int(raw)
    if kind == AFTER_EFFECT and ordinal < 1:
        raise ValueError(f"invalid crash point {point!r}: effects are numbered from 1")
    return kind, ordinal


def build_agent(
    log: SessionLog,
    workdir: Path,
    *,
    after_effect: Callable[[str], None] | None = None,
    planner: Planner = plan_repairs,
) -> tuple[Agent, ScenarioProvider]:
    provider = ScenarioProvider(LINES, per_step=PER_STEP)
    agent = Agent(
        log=log,
        provider=provider,
        env=LocalEnvironment(workdir),
        tools=chaos_tools(TARGET, after_effect),
        config=AgentConfig(model=MODEL, system_prompt=PROMPT, limits=Limits(max_turns=MAX_TURNS)),
        repair_planner=planner,
    )
    return agent, provider


def sigkill_self() -> NoReturn:
    os.kill(os.getpid(), signal.SIGKILL)
    raise RuntimeError("SIGKILL did not terminate the process")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.chaos.worker")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--point", required=True)
    args = parser.parse_args(argv)
    try:
        kind, ordinal = parse_point(args.point)
    except ValueError as exc:
        parser.error(str(exc))

    workdir = args.root / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    inner = JsonlSessionStore(args.root / "sessions").open(SESSION_ID, create=True)
    effects = 0

    def crash_after_event(event: Event) -> None:
        if event.seq == ordinal:
            sigkill_self()

    def crash_after_effect(line: str) -> None:
        nonlocal effects
        effects += 1
        if effects == ordinal:
            sigkill_self()

    if kind == AFTER_EVENT:
        log = ObservedLog(inner, crash_after_event)
        agent, _ = build_agent(log, workdir)
    else:
        log = ObservedLog(inner)
        agent, _ = build_agent(log, workdir, after_effect=crash_after_effect)
    try:
        asyncio.run(agent.run(PROMPT))
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
