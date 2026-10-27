"""Long-term memory across sessions in one repository (spec section 12a), in two variants for
the ablation against no memory:

- notes: a notes file the agent appends to with `remember`, shown at session start.
- facts: atomic facts with file provenance, written by a consolidation pass when a run ends,
  shown at session start as a fixed profile and searchable with `memory_search`.

Memory enters the request only at session start and through tools, never per turn, so the prompt
prefix stays cacheable. A fact is stale when a file it cites no longer has the hash recorded with
it; stale facts are left out of the profile and of search results. Staleness is computed on
read, so it can never disagree with the files."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from cookcode.agent.context import MEMORY_VIEW
from cookcode.agent.hooks import Hook, HookContext
from cookcode.agent.tools import Tool, ToolContext, ToolOutput, workspace_path
from cookcode.core.cancel import CancelToken
from cookcode.core.events import Event, ItemEvent, UserMessage
from cookcode.core.features import Features
from cookcode.core.items import Message, ToolCall, ToolResult
from cookcode.env.base import Environment
from cookcode.memory.store import Fact, FactStore

PROFILE_FACTS = 20
SEARCH_LIMIT = 10
NOTES_PROFILE_CHARS = 6000
MAX_NEW_FACTS = 12
LINE_CHARS = 800
TRANSCRIPT_CHARS = 60_000

CONSOLIDATE_SYSTEM = (
    "You maintain the long-term memory of a coding agent that works in one repository. You "
    "read a finished session and write down what a future session should know."
)
CONSOLIDATE_PROMPT = """Below are the facts already remembered about this repository, then the \
transcript of a session in it.

Write facts a future session in this repository would want to know before it starts: how the \
code is laid out, how to build, run and test it, conventions, pitfalls, and what was learned the \
hard way. Leave out the session's task and its changes unless they change how the repository \
works. Each fact is one sentence and cites the repository files it is about, as paths relative to \
the repository root; a fact citing no existing file is dropped. If a fact replaces a remembered \
one, put that fact's id in "updates". Do not repeat remembered facts that still hold.

Reply with one JSON object per line and nothing else, at most {limit} lines:
{{"fact": "...", "paths": ["path/to/file"], "updates": null}}
Reply with nothing if there is nothing worth keeping.

Remembered facts:
{facts}

Transcript:
{transcript}"""

REMEMBER_DESCRIPTION = (
    "Save a note for future sessions in this repository: how to build or test it, a "
    "convention, a pitfall you hit. Notes are shown at the start of every later session here."
)
SEARCH_DESCRIPTION = (
    "Search facts remembered from earlier sessions in this repository. Facts whose cited files "
    "changed since are left out. The facts most worth knowing are already in the system prompt."
)


async def repo_key(env: Environment) -> str:
    """The repository's root commit, so every copy of a repository shares one memory. A
    directory that is not a git repository falls back to its path."""
    result = await env.exec(
        "git rev-list --max-parents=0 HEAD 2>/dev/null | tail -n 1", cwd=".", timeout_s=30,
        max_output_bytes=1000, cancel=CancelToken(),
    )
    root = result.output.strip()
    if result.exit_code == 0 and root:
        return root[:16]
    return hashlib.sha256(env.root.encode()).hexdigest()[:16]


async def _head(env: Environment) -> str:
    result = await env.exec("git rev-parse HEAD 2>/dev/null", cwd=".", timeout_s=30,
                            max_output_bytes=1000, cancel=CancelToken())
    return result.output.strip() if result.exit_code == 0 else ""


async def _hash(env: Environment, path: str) -> str | None:
    try:
        return hashlib.sha256(await env.read_file(path)).hexdigest()
    except (OSError, ValueError):
        return None


async def fresh(env: Environment, facts: Sequence[Fact]) -> tuple[list[Fact], int]:
    """Facts whose cited files still hash as recorded, and how many were stale."""
    hashes: dict[str, str | None] = {}
    kept: list[Fact] = []
    for fact in facts:
        for path in fact.paths:
            if path not in hashes:
                hashes[path] = await _hash(env, path)
        if all(hashes[path] == digest for path, digest in fact.hashes.items()):
            kept.append(fact)
    return kept, len(facts) - len(kept)


def _render(facts: Sequence[Fact]) -> str:
    return "\n".join(f"- [{f.id}] {f.text} ({', '.join(f.paths)})" for f in facts)


def _transcript(events: Sequence[Event]) -> str:
    lines: list[str] = []
    for event in events:
        body = event.body
        if isinstance(body, UserMessage):
            lines.append(f"[user] {body.text}")
        elif isinstance(body, ItemEvent):
            item = body.item
            if isinstance(item, Message):
                lines.append(f"[{item.role}] {item.text}")
            elif isinstance(item, ToolCall):
                lines.append(f"[call {item.name}] {json.dumps(item.arguments)}")
            elif isinstance(item, ToolResult):
                lines.append(f"[result {item.status}] {item.content}")
    capped = [line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + " [...]" for line in lines]
    text = "\n".join(capped)
    if len(text) <= TRANSCRIPT_CHARS:
        return text
    head = capped[0] if capped else ""
    return f"{head}\n[... earlier steps left out ...]\n{text[-(TRANSCRIPT_CHARS - len(head)):]}"


def parse_facts(reply: str) -> list[tuple[str, list[str], str | None]]:
    """(fact, paths, updated id) from the consolidation reply; lines that are not a fact
    object are skipped."""
    parsed: list[tuple[str, list[str], str | None]] = []
    for line in reply.splitlines():
        try:
            data = json.loads(line.strip().strip(","))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        obj = cast(dict[str, Any], data)
        text, paths, updates = obj.get("fact"), obj.get("paths"), obj.get("updates")
        if not isinstance(text, str) or not text.strip() or not isinstance(paths, list):
            continue
        cited = [str(p) for p in cast(list[Any], paths)]
        parsed.append((text.strip(), cited, updates if isinstance(updates, str) else None))
    return parsed[:MAX_NEW_FACTS]


async def consolidate(store: FactStore, ctx: HookContext) -> list[Fact]:
    """One model call over the finished session; facts are written with the hashes of the files
    they cite as those files are now."""
    if ctx.ask is None:
        return []
    known, _ = await fresh(ctx.env, store.latest())
    prompt = CONSOLIDATE_PROMPT.format(
        limit=MAX_NEW_FACTS, facts=_render(known) or "(none)", transcript=_transcript(ctx.history)
    )
    reply = await ctx.ask(MEMORY_VIEW, CONSOLIDATE_SYSTEM, prompt)
    if reply is None:
        return []
    commit = await _head(ctx.env)
    known_ids = {fact.id for fact in known}
    written: list[Fact] = []
    for text, paths, updates in parse_facts(reply):
        hashes: dict[str, str] = {}
        for path in paths:
            relative = workspace_path(ctx.env.root, ".", path)
            digest = None if relative.startswith("/") else await _hash(ctx.env, relative)
            if digest is not None:
                hashes[relative] = digest
        if not hashes:
            continue
        target = updates if updates in known_ids else None
        written.append(store.add(text, hashes, commit, "updates" if target else None, target))
    return written


class RememberArgs(BaseModel):
    text: str = Field(description="The note, one or two sentences.")


class SearchArgs(BaseModel):
    query: str = Field(description="Words to look for.")


@dataclass
class MemoryParts:
    tools: list[Tool[Any]] = field(default_factory=lambda: [])
    hooks: list[Hook] = field(default_factory=lambda: [])
    profile: str = ""


def _notes(path: Path) -> MemoryParts:
    async def remember(args: RememberArgs, ctx: ToolContext) -> ToolOutput:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as notes:
            notes.write(f"- {' '.join(args.text.split())}\n")
        return ToolOutput(content="noted")

    text = path.read_text()[-NOTES_PROFILE_CHARS:] if path.exists() else ""
    profile = f"Notes from earlier sessions in this repository:\n{text}" if text.strip() else ""
    tool = Tool(name="remember", description=REMEMBER_DESCRIPTION, args_model=RememberArgs,
                side_effect="read_only", handler=remember)
    return MemoryParts(tools=[tool], profile=profile)


async def _facts(env: Environment, path: Path) -> MemoryParts:
    async def search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
        with FactStore(path) as store:
            matches = store.search(args.query)
        found, stale = await fresh(ctx.env, matches)
        note = f"\n({stale} stale facts left out: their files changed)" if stale else ""
        if not found:
            return ToolOutput(content=f"no remembered facts match {args.query!r}{note}")
        return ToolOutput(content=_render(found[:SEARCH_LIMIT]) + note)

    async def on_finish(ctx: HookContext) -> None:
        with FactStore(path) as store:
            await consolidate(store, ctx)

    with FactStore(path) as store:
        latest = store.latest()
    profile_facts, _ = await fresh(env, latest)
    shown = _render(profile_facts[:PROFILE_FACTS])
    profile = (
        "Facts remembered from earlier sessions in this repository, checked against the "
        f"current files:\n{shown}" if shown else ""
    )
    tool = Tool(name="memory_search", description=SEARCH_DESCRIPTION, args_model=SearchArgs,
                side_effect="read_only", handler=search)
    return MemoryParts(tools=[tool], hooks=[Hook(name="memory", on_finish=on_finish)],
                       profile=profile)


async def memory_parts(features: Features, env: Environment, root: Path) -> MemoryParts:
    """The tools, hooks and session-start profile for the enabled memory variants. `root` is
    the host directory that holds every repository's memory."""
    parts = MemoryParts()
    if not (features.notes or features.facts):
        return parts
    base = root / await repo_key(env)
    chosen: list[MemoryParts] = []
    if features.notes:
        chosen.append(_notes(base / "notes.md"))
    if features.facts:
        chosen.append(await _facts(env, base / "facts.db"))
    for part in chosen:
        parts.tools += part.tools
        parts.hooks += part.hooks
    parts.profile = "\n\n".join(part.profile for part in chosen if part.profile)
    return parts

