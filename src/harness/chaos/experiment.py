import argparse
import asyncio
import datetime
import hashlib
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from harness.agent.repair import plan_repairs
from harness.chaos.checks import resolution_problems
from harness.chaos.scenario import (
    APPEND_LINE,
    chaos_tools,
    conservative_plan_repairs,
    naive_plan_repairs,
)
from harness.chaos.worker import (
    AFTER_EFFECT,
    AFTER_EVENT,
    LINES,
    PER_STEP,
    PROMPT,
    SESSION_ID,
    TARGET,
    ObservedLog,
    Planner,
    build_agent,
    parse_point,
)
from harness.core.events import Event, ItemEvent, ToolCallFinished
from harness.core.items import ToolCall, ToolResult
from harness.store.base import SessionLog
from harness.store.jsonl import JsonlSessionStore

__all__ = [
    "ChaosError",
    "ModeResult",
    "PointResult",
    "ReferenceRun",
    "Report",
    "check_crash_prefix",
    "copy_state",
    "enumerate_points",
    "evaluate_point",
    "expected_naive_duplicate_points",
    "main",
    "reference_run",
    "resolution_problems",
    "run_crash",
    "run_experiment",
    "tree_digest",
]

CRASH_TIMEOUT_S = 120.0
REPORT_STEM = "chaos-m1"
MODES = ("ours", "conservative", "naive")


class ChaosError(Exception):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class ModeResult(_Model):
    duplicates: int
    missing: int
    resume_ok: bool
    resume_requests: int
    status: str
    problems: tuple[str, ...]


class PointResult(_Model):
    point: str
    kind: str
    ordinal: int
    events_at_crash: int
    last_event: str
    file_lines_at_crash: int
    ours: ModeResult
    conservative: ModeResult
    naive: ModeResult


class Totals(_Model):
    points: int
    duplicates: int
    points_with_duplicates: int
    missing: int
    points_with_missing: int
    resume_ok: int
    resume_requests: int


class ShapeCheck(_Model):
    ours_clean: bool
    conservative_clean: bool
    expected_naive_duplicate_points: tuple[str, ...]
    observed_naive_duplicate_points: tuple[str, ...]
    matches: bool


class Scenario(_Model):
    lines: tuple[str, ...]
    per_step: int
    target: str
    reference_events: int
    reference_effects: int
    reference_requests: int


class Report(_Model):
    harness_commit: str
    date: str
    scenario: Scenario
    rows: tuple[PointResult, ...]
    totals: dict[str, Totals]
    shape: ShapeCheck
    summary: str


@dataclass(frozen=True)
class ReferenceRun:
    events: list[Event]
    effects: int
    requests: int


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def copy_state(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination)
    if tree_digest(destination) != tree_digest(source):
        raise ChaosError(f"copy of {source} to {destination} is not byte-identical")


def _file_lines(workdir: Path) -> list[str]:
    path = workdir / TARGET
    return path.read_text().splitlines() if path.exists() else []


def reference_run(root: Path) -> ReferenceRun:
    workdir = root / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    inner = JsonlSessionStore(root / "sessions").open(SESSION_ID, create=True)
    log = ObservedLog(inner)
    effects = 0

    def count_effect(line: str) -> None:
        nonlocal effects
        effects += 1

    try:
        agent, provider = build_agent(log, workdir, after_effect=count_effect)
        outcome = asyncio.run(agent.run(PROMPT))
        events = log.read()
        appended = log.appended
    finally:
        log.close()
    if outcome.status != "done":
        raise ChaosError(f"reference run ended with status {outcome.status}")
    if _file_lines(workdir) != list(LINES):
        raise ChaosError(f"reference run wrote {_file_lines(workdir)}, expected {list(LINES)}")
    if appended != len(events) or [e.seq for e in events] != list(range(len(events))):
        raise ChaosError("reference event count does not match the log")
    if effects != len(LINES):
        raise ChaosError(f"reference run performed {effects} side effects, expected {len(LINES)}")
    problems = resolution_problems(events)
    if problems:
        raise ChaosError(f"reference run left the log inconsistent: {problems}")
    return ReferenceRun(events=events, effects=effects, requests=len(provider.requests))


def enumerate_points(reference: ReferenceRun) -> list[str]:
    return [f"{AFTER_EVENT}:{k}" for k in range(len(reference.events))] + [
        f"{AFTER_EFFECT}:{i}" for i in range(1, reference.effects + 1)
    ]


def expected_naive_duplicate_points(
    reference_events: Sequence[Event], points: Sequence[str]
) -> set[str]:
    finished: dict[str, int] = {}
    resolved: dict[str, int] = {}
    appends: list[str] = []
    for event in reference_events:
        body = event.body
        if isinstance(body, ToolCallFinished):
            finished[body.call_id] = event.seq
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            resolved[body.item.call_id] = event.seq
        elif (
            isinstance(body, ItemEvent)
            and isinstance(body.item, ToolCall)
            and body.item.name == APPEND_LINE
        ):
            appends.append(body.item.id)
    windows: list[range] = []
    for call_id in appends:
        if call_id not in finished or call_id not in resolved:
            raise ChaosError(f"reference call {call_id} has no finish event or no result")
        windows.append(range(finished[call_id], resolved[call_id]))
    expected: set[str] = set()
    for point in points:
        kind, ordinal = parse_point(point)
        if kind == AFTER_EFFECT or any(ordinal in window for window in windows):
            expected.add(point)
    return expected


def run_crash(root: Path, point: str) -> int:
    command = [
        sys.executable,
        "-m",
        "harness.chaos.worker",
        "--root",
        str(root),
        "--point",
        point,
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=CRASH_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ChaosError(f"worker for {point} timed out after {CRASH_TIMEOUT_S}s") from exc
    if completed.returncode != -signal.SIGKILL:
        raise ChaosError(
            f"worker for {point} did not die by SIGKILL "
            f"(returncode {completed.returncode})\n"
            f"stdout: {completed.stdout.strip()}\nstderr: {completed.stderr.strip()}"
        )
    return completed.returncode


def _resume(root: Path, planner: Planner) -> ModeResult:
    store = JsonlSessionStore(root / "sessions")
    log: SessionLog = store.open(SESSION_ID)
    try:
        agent, provider = build_agent(log, root / "work", planner=planner)
        outcome = asyncio.run(agent.run(None))
    finally:
        log.close()
    events = store.read(SESSION_ID)
    lines = _file_lines(root / "work")
    counts = Counter(lines)
    stray = sorted(set(counts) - set(LINES))
    if stray:
        raise ChaosError(f"unexpected lines in {TARGET} after resume: {stray}")
    problems = resolution_problems(events)
    return ModeResult(
        duplicates=sum(max(0, counts[line] - 1) for line in LINES),
        missing=sum(1 for line in LINES if counts[line] == 0),
        resume_ok=outcome.status == "done" and not problems,
        resume_requests=len(provider.requests),
        status=outcome.status,
        problems=tuple(problems),
    )


def check_crash_prefix(
    point: str, crashed: Sequence[Event], reference: Sequence[Event]
) -> None:
    kind, ordinal = parse_point(point)
    crashed_kinds = [event.body.type for event in crashed]
    if kind == AFTER_EVENT and len(crashed) != ordinal + 1:
        raise ChaosError(f"{point}: crashed log has {len(crashed)} events, expected {ordinal + 1}")
    expected = [event.body.type for event in reference[: len(crashed)]]
    if len(crashed) > len(reference) or crashed_kinds != expected:
        raise ChaosError(
            f"{point}: crashed log diverges from the reference run: "
            f"{crashed_kinds} versus {expected}"
        )


def evaluate_point(point: str, base: Path, reference: ReferenceRun) -> PointResult:
    kind, ordinal = parse_point(point)
    crashed = base / "crashed"
    run_crash(crashed, point)
    events = JsonlSessionStore(crashed / "sessions").read(SESSION_ID)
    lines = _file_lines(crashed / "work")
    check_crash_prefix(point, events, reference.events)
    if kind == AFTER_EFFECT and lines != list(LINES[:ordinal]):
        raise ChaosError(f"{point}: crashed file holds {lines}, expected {list(LINES[:ordinal])}")
    for mode in MODES:
        copy_state(crashed, base / mode)
    ours = _resume(base / "ours", plan_repairs)
    conservative = _resume(
        base / "conservative", conservative_plan_repairs(chaos_tools(TARGET))
    )
    naive = _resume(base / "naive", naive_plan_repairs)
    return PointResult(
        point=point,
        kind=kind,
        ordinal=ordinal,
        events_at_crash=len(events),
        last_event=events[-1].body.type if events else "none",
        file_lines_at_crash=len(lines),
        ours=ours,
        conservative=conservative,
        naive=naive,
    )


def _totals(rows: Sequence[PointResult], mode: str) -> Totals:
    results: list[ModeResult] = [getattr(row, mode) for row in rows]
    return Totals(
        points=len(results),
        duplicates=sum(r.duplicates for r in results),
        points_with_duplicates=sum(1 for r in results if r.duplicates > 0),
        missing=sum(r.missing for r in results),
        points_with_missing=sum(1 for r in results if r.missing > 0),
        resume_ok=sum(1 for r in results if r.resume_ok),
        resume_requests=sum(r.resume_requests for r in results),
    )


def _shape(
    rows: Sequence[PointResult], reference: ReferenceRun, totals: dict[str, Totals]
) -> ShapeCheck:
    points = [row.point for row in rows]
    expected = expected_naive_duplicate_points(reference.events, points)
    observed = {row.point for row in rows if row.naive.duplicates > 0}
    ours, conservative = totals["ours"], totals["conservative"]
    ours_clean = ours.duplicates == 0 and ours.missing == 0
    conservative_clean = conservative.duplicates == 0 and conservative.missing == 0
    return ShapeCheck(
        ours_clean=ours_clean,
        conservative_clean=conservative_clean,
        expected_naive_duplicate_points=tuple(sorted(expected)),
        observed_naive_duplicate_points=tuple(sorted(observed)),
        matches=ours_clean and conservative_clean and expected == observed,
    )


def _summary(totals: dict[str, Totals], shape: ShapeCheck, reference: ReferenceRun) -> str:
    ours, cons, naive = totals["ours"], totals["conservative"], totals["naive"]
    text = (
        f"Across {ours.points} crash points, the three-state repair produced "
        f"{ours.duplicates} duplicated and {ours.missing} missing side effects, with "
        f"{ours.resume_ok} of {ours.points} clean resumes and {ours.resume_requests} model "
        f"requests. The conservative two-state repair, which has no write-ahead records and "
        f"marks every dangling mutating call unknown, produced {cons.duplicates} duplicated "
        f"and {cons.missing} missing, with {cons.resume_ok} of {cons.points} clean resumes and "
        f"{cons.resume_requests} requests. The naive two-state repair, which marks every "
        f"dangling call not executed, produced {naive.duplicates} duplicated side effects "
        f"across {naive.points_with_duplicates} points and {naive.missing} missing, with "
        f"{naive.resume_ok} of {naive.points} clean resumes and {naive.resume_requests} "
        f"requests. An uninterrupted run takes {reference.requests} requests. Both safe repairs "
        f"avoid duplicates. Write-ahead records save {cons.resume_requests - ours.resume_requests} "
        f"verify requests over the conservative repair, because a call that never started is "
        f"known to be safe to issue again. "
    )
    if shape.matches:
        text += (
            "Shape check: both safe repairs were clean at every point, and the naive repair "
            "duplicated at exactly the points between a side effect and its result."
        )
    else:
        unexpected = sorted(
            set(shape.observed_naive_duplicate_points) - set(shape.expected_naive_duplicate_points)
        )
        absent = sorted(
            set(shape.expected_naive_duplicate_points) - set(shape.observed_naive_duplicate_points)
        )
        text += (
            "SHAPE MISMATCH: the numbers differ from the expected shape. "
            f"Three-state clean at every point: {shape.ours_clean}. "
            f"Conservative clean at every point: {shape.conservative_clean}. "
            f"Baseline duplicated at unexpected points: {unexpected}. "
            f"Baseline did not duplicate at expected points: {absent}."
        )
    return text


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip()


def _cell(result: ModeResult) -> str:
    ok = "yes" if result.resume_ok else "NO"
    return f"{result.duplicates} | {result.missing} | {ok} | {result.resume_requests}"


def _render(report: Report) -> str:
    scenario = report.scenario
    lines = [
        "# Chaos experiment, M1",
        "",
        f"- Harness base commit: `{report.harness_commit}`",
        f"- Date: {report.date}",
        f"- Scenario: append {', '.join(scenario.lines)} to `{scenario.target}`, "
        f"{scenario.per_step} calls per step",
        f"- Reference run: {scenario.reference_events} events, "
        f"{scenario.reference_effects} side effects, {scenario.reference_requests} model requests",
        f"- Crash points: {len(report.rows)}, each killed with SIGKILL "
        "(returncode -9 enforced by the driver)",
        "",
        "## Result",
        "",
        report.summary,
        "",
        "## Totals",
        "",
        "| mode | points | duplicates | points with duplicates | missing | resume_ok "
        "| model requests |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in MODES:
        totals = report.totals[name]
        lines.append(
            f"| {name} | {totals.points} | {totals.duplicates} | {totals.points_with_duplicates} "
            f"| {totals.missing} | {totals.resume_ok} | {totals.resume_requests} |"
        )
    lines += [
        "",
        "## Per point",
        "",
        "Each mode shows duplicates, missing, resume_ok, model requests in the resume.",
        "",
        "| point | last event | file lines | ours dup | ours miss | ours ok | ours req "
        "| cons dup | cons miss | cons ok | cons req "
        "| naive dup | naive miss | naive ok | naive req |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.rows:
        lines.append(
            f"| {row.point} | {row.last_event} | {row.file_lines_at_crash} "
            f"| {_cell(row.ours)} | {_cell(row.conservative)} | {_cell(row.naive)} |"
        )
    lines += [
        "",
        "## Method",
        "",
        "Each point runs the scenario in a subprocess that SIGKILLs itself, either right after "
        "the log append for a given seq returns (`after_event:k`) or right after the shell exec "
        "that performed a given side effect (`after_effect:i`). The crashed directory (session "
        "log plus work dir) is copied byte for byte into three directories. One resumes with the "
        "three-state repair, one with a conservative two-state repair that ignores write-ahead "
        "records and marks every dangling mutating call unknown, and one with a naive two-state "
        "repair that marks every dangling call not executed. Each uses a fresh scenario provider "
        "and one `run(None)`. For `after_event:k` the crashed log's event kinds must equal the "
        "reference run's first k+1 kinds, or the driver fails. A resume that "
        "does not end `done`, leaves a dangling call, gives a call more than one result, or "
        "leaves a gap in the seqs is not `resume_ok`.",
        "",
        "## Caveat",
        "",
        "SIGKILL ends the process but does not drop the operating system page cache, so every "
        "byte written before the kill survives. This experiment therefore does not test fsync "
        "behavior under power loss or a kernel crash. It tests the repair rules against process "
        "death at every event boundary and after every side effect. The scenario provider is "
        "deterministic and goal-driven, so the numbers describe the repair logic, not model "
        "behavior. Crashes are injected only into the first run, never into the resume itself, "
        "and there is one scenario shape.",
        "",
    ]
    return "\n".join(lines)


def run_experiment(
    out_dir: Path, *, points: Sequence[str] | None = None, date: str | None = None
) -> Report:
    with tempfile.TemporaryDirectory(prefix="chaos-") as scratch:
        scratch_path = Path(scratch)
        reference = reference_run(scratch_path / "reference")
        selected = list(points) if points is not None else enumerate_points(reference)
        rows: list[PointResult] = []
        for index, point in enumerate(selected):
            rows.append(evaluate_point(point, scratch_path / f"p{index:03d}", reference))
            print(f"[{index + 1}/{len(selected)}] {point}", file=sys.stderr)
    totals = {mode: _totals(rows, mode) for mode in MODES}
    shape = _shape(rows, reference, totals)
    report = Report(
        harness_commit=_git_commit(),
        date=date or datetime.date.today().isoformat(),
        scenario=Scenario(
            lines=LINES,
            per_step=PER_STEP,
            target=TARGET,
            reference_events=len(reference.events),
            reference_effects=reference.effects,
            reference_requests=reference.requests,
        ),
        rows=tuple(rows),
        totals=totals,
        shape=shape,
        summary=_summary(totals, shape, reference),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{REPORT_STEM}.md").write_text(_render(report))
    (out_dir / f"{REPORT_STEM}.json").write_text(_dump_json(report))
    return report


def _dump_json(report: Report) -> str:
    return report.model_dump_json(indent=2) + "\n"


def totals_line(report: Report) -> str:
    parts = [f"points={report.totals['ours'].points}"]
    for mode in MODES:
        t = report.totals[mode]
        parts.append(
            f"{mode}: duplicates={t.duplicates} missing={t.missing} "
            f"resume_ok={t.resume_ok}/{t.points} requests={t.resume_requests}"
        )
    return " | ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.chaos.experiment")
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--date", default=None)
    args = parser.parse_args(argv)
    report = run_experiment(args.out, date=args.date)
    print(totals_line(report))
    if not report.shape.matches:
        print("SHAPE MISMATCH, see the report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
