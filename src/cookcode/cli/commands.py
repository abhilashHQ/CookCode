import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from cookcode.agent.context import SIDE_VIEWS, build_request, items_sha256
from cookcode.cli.app import SESSION_ERRORS, data_dir, main, session_config, session_error
from cookcode.core.events import (
    Event,
    ModelRequestStarted,
    RunConfig,
    RunStarted,
    SessionFinished,
    SessionStarted,
    UserMessage,
    event_schema,
)
from cookcode.core.ids import new_id
from cookcode.evals.cli import eval_command, stats_command
from cookcode.export.atif import to_atif
from cookcode.store.base import SessionCorruptError, SessionNotFoundError
from cookcode.store.jsonl import JsonlSessionStore

PROMPT_WIDTH = 60


def _store() -> JsonlSessionStore:
    return JsonlSessionStore(data_dir() / "sessions")


def schema_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cookcode schema", description="Print the JSON schema of headless events."
    )
    parser.parse_args(argv)
    print(json.dumps(event_schema(), indent=2, sort_keys=True))
    return 0


def export_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cookcode export", description="Export a session log to another format."
    )
    parser.add_argument("session")
    parser.add_argument("--format", choices=("atif",), default="atif")
    parser.add_argument("-o", "--output", help="file to write; stdout when omitted")
    args = parser.parse_args(argv)
    store = _store()
    try:
        events = store.read(args.session)
        trajectory = to_atif(events, session_id=args.session)
    except (SessionNotFoundError, ValueError) as exc:
        print(f"cookcode export: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(trajectory, indent=2, ensure_ascii=False)
    if args.output is None:
        print(text)
    else:
        Path(args.output).write_text(text + "\n")
    return 0


def resume_command(argv: list[str]) -> int:
    return main(["--resume", *argv])


def fork_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cookcode fork",
        description="Copy a session's log up to a seq into a new session and print its id.",
    )
    parser.add_argument("session")
    parser.add_argument("--at", type=int, metavar="SEQ", help="last seq to keep (default: last)")
    args = parser.parse_args(argv)
    store = _store()
    new_session = new_id()
    try:
        at_seq: int | None = args.at
        if at_seq is None:
            events = store.read(args.session)
            if not events:
                raise ValueError(f"session {args.session} has no events")
            at_seq = events[-1].seq
        store.fork(args.session, at_seq, new_session)
    except SESSION_ERRORS as exc:
        print(f"cookcode fork: {session_error(args.session, exc)}", file=sys.stderr)
        return 1
    print(new_session)
    return 0


def _status(events: Sequence[Event]) -> str:
    for event in reversed(events):
        if isinstance(event.body, SessionFinished):
            return event.body.status
        if isinstance(event.body, RunStarted):
            return "open"
    return "open"


def _first_prompt(events: Sequence[Event]) -> str:
    text = next((e.body.text for e in events if isinstance(e.body, UserMessage)), "")
    text = " ".join(text.split())
    return text if len(text) <= PROMPT_WIDTH else text[: PROMPT_WIDTH - 3] + "..."


def summarize(session_id: str, events: Sequence[Event]) -> str:
    config = session_config(events)
    start = events[0].ts.strftime("%Y-%m-%d %H:%M:%S") if events else "-"
    model = f"{config.provider}/{config.model}" if config is not None else "-"
    runs = sum(isinstance(event.body, RunStarted) for event in events)
    return (
        f"{session_id}  {start}  {model}  {_status(events):<15}  {runs} runs  "
        f"{_first_prompt(events)}"
    ).rstrip()


def sessions_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cookcode sessions", description="List sessions, newest first."
    )
    parser.parse_args(argv)
    store = _store()
    rows: list[tuple[datetime, str, str]] = []
    for session_id in store.list_sessions():
        try:
            events = store.read(session_id)
        except SessionCorruptError as exc:
            rows.append((datetime.min.replace(tzinfo=UTC), session_id, f"{session_id}  {exc}"))
            continue
        start = events[0].ts if events else datetime.min.replace(tzinfo=UTC)
        rows.append((start, session_id, summarize(session_id, events)))
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    for _, _, line in rows:
        print(line)
    return 0


def replay_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cookcode replay",
        description="Rebuild every logged model request from the log before it, under the "
        "strategy its run recorded, and check its items hash. For other strategies, see "
        "cookcode eval context.",
    )
    parser.add_argument("session")
    args = parser.parse_args(argv)
    try:
        events = _store().read(args.session)
    except SESSION_ERRORS as exc:
        print(f"cookcode replay: {session_error(args.session, exc)}", file=sys.stderr)
        return 1
    verdicts: Counter[str] = Counter()
    config: RunConfig | None = None
    for index, event in enumerate(events):
        body = event.body
        if isinstance(body, SessionStarted | RunStarted):
            config = body.config
        elif isinstance(body, ModelRequestStarted) and body.view not in SIDE_VIEWS:
            request = build_request(
                events[:index],
                model=config.model if config is not None else "",
                system="",
                tools=(),
                provider=config.provider if config is not None else None,
                strategy=config.strategy if config is not None else None,
            )
            if body.items_sha256 is None:
                verdict = "unverified"
            elif body.items_sha256 == items_sha256(request.items):
                verdict = "match"
            else:
                verdict = "MISMATCH"
            verdicts[verdict] += 1
            print(
                f"{body.request_id}  {len(request.items)} items  "
                f"~{body.token_estimate} tokens  {verdict}"
            )
    total = sum(verdicts.values())
    print(
        f"{total} requests: {verdicts['match']} match, {verdicts['MISMATCH']} mismatch, "
        f"{verdicts['unverified']} unverified"
    )
    return 1 if verdicts["MISMATCH"] else 0


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "schema": schema_command,
    "export": export_command,
    "resume": resume_command,
    "fork": fork_command,
    "sessions": sessions_command,
    "replay": replay_command,
    "stats": stats_command,
    "eval": eval_command,
}
