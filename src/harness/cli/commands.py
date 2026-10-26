import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from harness.cli.app import data_dir
from harness.core.events import event_schema
from harness.export.atif import to_atif
from harness.store.base import SessionNotFoundError
from harness.store.jsonl import JsonlSessionStore


def schema_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="harness schema", description="Print the JSON schema of headless events."
    )
    parser.parse_args(argv)
    print(json.dumps(event_schema(), indent=2, sort_keys=True))
    return 0


def export_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="harness export", description="Export a session log to another format."
    )
    parser.add_argument("session")
    parser.add_argument("--format", choices=("atif",), default="atif")
    parser.add_argument("-o", "--output", help="file to write; stdout when omitted")
    args = parser.parse_args(argv)
    store = JsonlSessionStore(data_dir() / "sessions")
    try:
        events = store.read(args.session)
        trajectory = to_atif(events, session_id=args.session)
    except (SessionNotFoundError, ValueError) as exc:
        print(f"harness export: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(trajectory, indent=2, ensure_ascii=False)
    if args.output is None:
        print(text)
    else:
        Path(args.output).write_text(text + "\n")
    return 0


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "schema": schema_command,
    "export": export_command,
}
