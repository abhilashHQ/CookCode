import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from harness.cli.app import PRESETS, data_dir
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.evals.context import ContextCost, session_cost
from harness.evals.context import render as render_context
from harness.evals.repro import judge
from harness.evals.repro import render as render_repro
from harness.evals.runner import AttemptResult, Mode, run_suite
from harness.evals.stats import (
    Price,
    read_session,
    render,
    session_files,
    session_stats,
    total,
)
from harness.evals.suite import load_suite
from harness.evals.variance import harbor_trials, suite_trials, summarize
from harness.evals.variance import render as render_summary
from harness.store.base import SessionNotFoundError

PRICES = TypeAdapter(dict[str, Price])


def _load_prices(path: str | None) -> dict[str, Price]:
    if path is None:
        return {}
    try:
        return PRICES.validate_json(Path(path).read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError(f"cannot load prices {path}: {exc}") from exc


def _sessions(target: str) -> list[Path]:
    path = Path(target)
    if path.is_dir():
        return session_files(path)
    if path.suffix == ".jsonl" and path.is_file():
        return [path]
    candidate = data_dir() / "sessions" / f"{target}.jsonl"
    if candidate.is_file():
        return [candidate]
    raise SessionNotFoundError(target)


def stats_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="harness stats",
        description="Tokens, cache share, time split and tool errors for a session, a suite "
        "run directory or a Harbor job directory.",
    )
    parser.add_argument("target", help="session id, session .jsonl file, or a directory")
    parser.add_argument("--prices", help='JSON {"model": {"input": usd_per_mtok, ...}}')
    args = parser.parse_args(argv)
    try:
        prices = _load_prices(args.prices)
        paths = _sessions(args.target)
    except (ValueError, SessionNotFoundError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 1
    if not paths:
        print(f"harness: no session logs under {args.target}", file=sys.stderr)
        return 1
    print(render(total(session_stats(read_session(path), prices) for path in paths)))
    return 0


def _run(args: argparse.Namespace) -> int:
    try:
        tasks = load_suite(Path(args.suite), args.tasks.split(",") if args.tasks else None)
    except (OSError, ValueError, ValidationError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 1
    if not tasks:
        print(f"harness: no tasks under {args.suite}", file=sys.stderr)
        return 1
    mode: Mode = args.mode
    preset = args.provider
    base_url, key_env, default_model = PRESETS[preset]
    if mode in ("live", "record") and not os.environ.get(key_env):
        print(f"harness: set {key_env} to an API key for {base_url}", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(args.output) if args.output else data_dir() / "runs" / f"{mode}-{stamp}"

    def show(result: AttemptResult) -> None:
        verdict = "PASS" if result.passed else "FAIL"
        drift = f"  drift x{len(result.drift)}" if result.drift else ""
        print(
            f"{verdict}  {result.task:<40} #{result.attempt}  {result.status:<15} "
            f"{result.turns:>2} turns  {result.wall_s:6.1f}s{drift}  {result.reason}"[:200],
            flush=True,
        )

    try:
        results = asyncio.run(
            run_suite(
                tasks,
                out,
                mode=mode,
                preset=preset,
                model=args.model or default_model,
                attempts=args.attempts,
                concurrency=args.concurrency,
                strategy=args.strategy,
                features=args.features,
                on_result=show,
            )
        )
    except ValueError as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 1
    passed = sum(result.passed for result in results)
    print(f"{passed}/{len(results)} passed, run directory {out}")
    return 0 if passed == len(results) or mode in ("live", "record") else 1


def _report(args: argparse.Namespace) -> int:
    """Suite runs report one by one. Harbor jobs are pooled and grouped by agent, so a job of
    re-runs merges with the job whose trials it replaces."""
    jobs: list[Path] = []
    for target in args.runs:
        path = Path(target)
        if (path / "results.jsonl").is_file():
            trials = suite_trials(path)
            if not trials:
                print(f"harness: no trials in {target}", file=sys.stderr)
                return 1
            manifest = json.loads((path / "run.json").read_text())
            label = f"{path.name} ({manifest['provider']}/{manifest['model']}, {manifest['mode']})"
            print(render_summary(summarize(trials), label))
            logs = session_files(path)
            if logs:
                print(render(total(session_stats(read_session(log)) for log in logs)))
            print()
        elif (path / "result.json").is_file():
            jobs.append(path)
        else:
            print(f"harness: {target} is neither a suite run nor a Harbor job", file=sys.stderr)
            return 1
    for run in harbor_trials(*jobs):
        if run.trials:
            print(render_summary(summarize(run.trials), f"{run.agent} (harbor)"))
        print(f"  excluded     {len(run.excluded)} infrastructure failures")
        for trial, reason in run.excluded.items():
            print(f"    {trial:<40} {reason}")
        print()
    logs = [log for job in jobs for log in session_files(job)]
    if logs:
        print(render(total(session_stats(read_session(log)) for log in logs)))
    return 0


def _context(args: argparse.Namespace) -> int:
    try:
        strategies = [Strategy.parse(text) for text in args.strategies.split(",")]
    except ValueError as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 1
    logs = [log for target in args.runs for log in session_files(Path(target))]
    if not logs:
        print("harness: no session logs found", file=sys.stderr)
        return 1
    sessions = [read_session(log) for log in logs]
    rows: list[tuple[str, ContextCost]] = []
    for strategy in strategies:
        try:
            cost = sum((session_cost(events, strategy) for events in sessions), ContextCost())
        except ValueError as exc:
            print(f"harness: {exc}", file=sys.stderr)
            return 1
        rows.append((strategy.label, cost))
    print(f"{len(sessions)} sessions, logged trajectories replayed under each strategy")
    print(render_context(rows))
    return 0


def _repro(args: argparse.Namespace) -> int:
    run = Path(args.run)
    rows = [
        AttemptResult.model_validate_json(line)
        for line in (run / "results.jsonl").read_text().splitlines()
        if line.strip()
    ]
    tasks = {task.id: task for task in load_suite(Path(args.suite))}
    try:
        verdicts = [judge(tasks[row.task], row, run / "sessions") for row in rows]
    except (KeyError, ValueError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 1
    print(render_repro(sorted(verdicts, key=lambda v: (v.task, v.attempt))))
    return 0


def eval_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="harness eval", description="Run and report evals.")
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run the local suite")
    run.add_argument("--suite", default="evals/suite")
    run.add_argument("--tasks", help="comma-separated task ids (default: all)")
    run.add_argument(
        "--mode",
        choices=("replay", "oracle", "live", "record"),
        default="replay",
        help="replay cassettes (default, no API calls), run the oracle solutions, call the "
        "model live, or call it live and save each task's cassette",
    )
    run.add_argument("--attempts", type=int, default=1)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--provider", choices=tuple(PRESETS), default="nim")
    run.add_argument("--model")
    run.add_argument("--strategy", type=Strategy.parse, default=Strategy())
    run.add_argument("--features", type=Features.parse, default=Features())
    run.add_argument("-o", "--output", help="run directory (default under $HARNESS_HOME/runs)")
    report = sub.add_parser("report", help="variance summary and stats of runs")
    report.add_argument("runs", nargs="+", help="suite run directories or Harbor job directories")
    context = sub.add_parser(
        "context", help="rebuild logged requests under other strategies (no API calls)"
    )
    context.add_argument("runs", nargs="+", help="directories with session logs")
    context.add_argument(
        "--strategies", default="raw,mask:k4,mask:k8,mask_batched:k4:b8,mask_batched:k8:b8"
    )
    repro = sub.add_parser(
        "repro", help="did verify-first reproductions fail for the right reason (no API calls)"
    )
    repro.add_argument("run", help="a suite run directory, judged before its tasks run again")
    repro.add_argument("--suite", default="evals/suite")
    args = parser.parse_args(argv)
    actions = {"run": _run, "report": _report, "context": _context, "repro": _repro}
    return actions[args.action](args)
