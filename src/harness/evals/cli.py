import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from harness.cli.app import PRESETS, data_dir
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
    for target in args.runs:
        path = Path(target)
        if (path / "results.jsonl").is_file():
            trials = suite_trials(path)
            manifest = json.loads((path / "run.json").read_text())
            label = f"{path.name} ({manifest['provider']}/{manifest['model']}, {manifest['mode']})"
        elif (path / "result.json").is_file():
            trials = harbor_trials(path)
            label = f"{path.name} (harbor)"
        else:
            print(f"harness: {target} is neither a suite run nor a Harbor job", file=sys.stderr)
            return 1
        if not trials:
            print(f"harness: no trials in {target}", file=sys.stderr)
            return 1
        print(render_summary(summarize(trials), label))
        logs = session_files(path)
        if logs:
            print(render(total(session_stats(read_session(log)) for log in logs)))
        print()
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
    run.add_argument("-o", "--output", help="run directory (default under $HARNESS_HOME/runs)")
    report = sub.add_parser("report", help="variance summary and stats of runs")
    report.add_argument("runs", nargs="+", help="suite run directories or Harbor job directories")
    args = parser.parse_args(argv)
    return _run(args) if args.action == "run" else _report(args)
