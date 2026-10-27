# M0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundation every later milestone sits on: the item model, the event log with crash-safe JSONL storage, redaction, the provider contract with a scripted FakeProvider, and the Environment contract with a local implementation. No agent loop yet.

**Architecture:** A `core` package of pure types (items, events, usage, cancellation) with no I/O. Three interface packages (`store`, `providers`, `env`), each with a `base.py` protocol plus one implementation. Everything serializable is a frozen pydantic v2 model, and unknown future types deserialize into fallback models instead of failing.

**Tech Stack:** Python 3.12, uv, pydantic v2, pytest + pytest-asyncio, ruff, pyright (strict on `src`).

**Spec:** `docs/specs/2026-10-22-flagship1-design.md` (sections 4.1, 4.2, 4.3, 4.4, 4.7, 6, 9, 13 row M0, 14). Visual overview: `docs/blueprint.html`, Stage 1.

## Global Constraints

- All commands run inside WSL Ubuntu, from the repo root `~/code/flagship-1`. Linux only: the code uses `fcntl.flock` and `os.killpg`.
- Python `>=3.12`. The only runtime dependency in M0 is `pydantic>=2.9,<3`.
- The package is named `harness` as a placeholder until the codename gate at the end of M0.
- `src/cookcode/core/` never imports I/O modules (`subprocess`, `socket`, `http`, `urllib`, `sys`) and never calls `print`, `input` or `open`. `tests/test_boundaries.py` enforces this.
- All serializable types are frozen pydantic models.
- No filler comments. No em dashes in docs or code.
- Every commit message ends with the trailer line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Done means `uv run pytest -q`, `uv run ruff check` and `uv run pyright` all pass. If ruff only flags import order (I001), run `uv run ruff check --fix` and re-check.

## File Map

```
pyproject.toml                 project, deps, ruff, pyright, pytest config
.gitignore
README.md
src/cookcode/__init__.py        __version__
src/cookcode/py.typed
src/cookcode/core/__init__.py
src/cookcode/core/ids.py        new_id(), utc_now()
src/cookcode/core/cancel.py     CancelToken
src/cookcode/core/usage.py      Usage, StopReason
src/cookcode/core/_union.py     tag_or_fallback() discriminator helper
src/cookcode/core/items.py      Message, Reasoning, ToolCall, ToolResult, OtherItem, UnknownItem, Item
src/cookcode/core/events.py     all event bodies, Event envelope, make_event, JSON line helpers
src/cookcode/redact.py          Redactor
src/cookcode/store/__init__.py
src/cookcode/store/base.py      SessionLog, SessionStore protocols, errors
src/cookcode/store/jsonl.py     JsonlSessionStore, JsonlSessionLog
src/cookcode/providers/__init__.py
src/cookcode/providers/base.py  ModelRequest, ModelCaps, ToolSchema, stream events, Provider
src/cookcode/providers/fake.py  FakeProvider, FakeTurn, say(), call()
src/cookcode/env/__init__.py
src/cookcode/env/base.py        ExecResult, Environment
src/cookcode/env/local.py       LocalEnvironment, _CappedBuffer
tests/sample_events.py         one instance of every event body
tests/test_*.py                one test file per module
docs/decisions/                decision records (Task 11)
docs/walkthroughs/m0.md        walkthrough and cross-questions (Task 11)
```

---

### Task 1: Repo skeleton, tooling and the core boundary test

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/cookcode/__init__.py`, `src/cookcode/py.typed`, `src/cookcode/core/__init__.py`, `src/cookcode/store/__init__.py`, `src/cookcode/providers/__init__.py`, `src/cookcode/env/__init__.py`
- Test: `tests/test_package.py`, `tests/test_boundaries.py`

**Interfaces:**
- Consumes: nothing
- Produces: importable package `harness` with `cookcode.__version__ == "0.0.1"`; the boundary test that every later task must keep green

- [ ] **Step 1: Create the repo and bring the design docs in**

```bash
mkdir -p ~/code/flagship-1 && cd ~/code/flagship-1
git init -b main
mkdir -p docs src/cookcode/core src/cookcode/store src/cookcode/providers src/cookcode/env tests
cp -r "/mnt/c/Users/abhii/Batcave/new projects/flagship 1/docs/." docs/
cp -r "/mnt/c/Users/abhii/Batcave/new projects/flagship 1/reports" .
cp -r "/mnt/c/Users/abhii/Batcave/new projects/flagship 1/research_notes" .
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "harness"
version = "0.0.1"
description = "Flagship 1: a coding agent harness built around a replayable event log"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.9,<3"]

[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "ruff>=0.7", "pyright>=1.1.390"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/cookcode"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
pythonpath = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]

[tool.pyright]
include = ["src", "tests"]
strict = ["src"]
pythonVersion = "3.12"
venvPath = "."
venv = ".venv"
```

- [ ] **Step 3: Write `.gitignore` and `README.md`**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
dist/
```

`README.md`:
```markdown
# harness (working name)

Flagship 1: a coding agent harness built from scratch around a replayable event log.

- Design spec: `docs/specs/2026-10-22-flagship1-design.md`
- Visual overview: `docs/blueprint.html`
- Decision records: `docs/decisions/`

Development runs in WSL Ubuntu: `uv sync`, then `uv run pytest -q`.
```

- [ ] **Step 4: Write the package files**

`src/cookcode/__init__.py`:
```python
__version__ = "0.0.1"
```

`src/cookcode/py.typed`, `src/cookcode/core/__init__.py`, `src/cookcode/store/__init__.py`, `src/cookcode/providers/__init__.py`, `src/cookcode/env/__init__.py`: empty files.

- [ ] **Step 5: Write the failing tests**

`tests/test_package.py`:
```python
import cookcode


def test_version() -> None:
    assert cookcode.__version__ == "0.0.1"
```

`tests/test_boundaries.py`:
```python
import ast
from pathlib import Path

CORE = Path(__file__).parents[1] / "src" / "cookcode" / "core"
FORBIDDEN_MODULES = {"subprocess", "socket", "http", "urllib", "sys", "httpx", "requests"}
FORBIDDEN_CALLS = {"print", "input", "open"}
ALLOWED_COOKCODE_IMPORTS = {"cookcode.core"}


def _core_files() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def _imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_exists() -> None:
    assert _core_files()


def test_core_imports_no_io_modules() -> None:
    for path in _core_files():
        for module in _imported_modules(ast.parse(path.read_text())):
            assert module.split(".")[0] not in FORBIDDEN_MODULES, f"{path.name} imports {module}"


def test_core_imports_only_core_from_harness() -> None:
    for path in _core_files():
        for module in _imported_modules(ast.parse(path.read_text())):
            if module.split(".")[0] == "harness":
                assert any(
                    module == allowed or module.startswith(allowed + ".")
                    for allowed in ALLOWED_COOKCODE_IMPORTS
                ), f"{path.name} imports {module}"


def test_core_never_calls_print_input_open_or_reads_environ() -> None:
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, f"{path.name} calls {node.func.id}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "environ", f"{path.name} reads os.environ"
```

- [ ] **Step 6: Install and run the tests**

Run: `uv sync && uv run pytest -q`
Expected: 5 passed. The boundary tests pass trivially now and guard every later task.

- [ ] **Step 7: Run lint and types**

Run: `uv run ruff check && uv run pyright`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: repo skeleton, tooling and core boundary test" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: IDs, cancellation token and usage

**Files:**
- Create: `src/cookcode/core/ids.py`, `src/cookcode/core/cancel.py`, `src/cookcode/core/usage.py`
- Test: `tests/test_ids.py`, `tests/test_cancel.py`, `tests/test_usage.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `cookcode.core.ids.new_id() -> str` (32 hex chars), `utc_now() -> datetime` (tz-aware UTC)
  - `cookcode.core.cancel.CancelToken` with `.cancelled: bool`, `.reason: str | None`, `.cancel(reason: str = "user") -> None`, `async .wait() -> None`
  - `cookcode.core.usage.Usage` (frozen; `input`, `cache_read`, `cache_write`, `output`, `reasoning`, all `int = 0`; `__add__`; `total_input` property)
  - `cookcode.core.usage.StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "aborted", "error"]`

- [ ] **Step 1: Write the failing tests**

`tests/test_ids.py`:
```python
from datetime import UTC

from cookcode.core.ids import new_id, utc_now


def test_new_id_is_unique_hex() -> None:
    ids = {new_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is UTC
```

`tests/test_cancel.py`:
```python
import asyncio

from cookcode.core.cancel import CancelToken


def test_starts_uncancelled() -> None:
    token = CancelToken()
    assert not token.cancelled
    assert token.reason is None


def test_first_reason_wins() -> None:
    token = CancelToken()
    token.cancel("user")
    token.cancel("timeout")
    assert token.cancelled
    assert token.reason == "user"


async def test_wait_returns_after_cancel_from_another_task() -> None:
    token = CancelToken()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.01)
        token.cancel("user")

    task = asyncio.create_task(cancel_soon())
    await asyncio.wait_for(token.wait(), timeout=1)
    await task
    assert token.cancelled
```

`tests/test_usage.py`:
```python
from cookcode.core.usage import Usage


def test_add_sums_every_field() -> None:
    a = Usage(input=10, cache_read=5, cache_write=1, output=3, reasoning=2)
    b = Usage(input=1, cache_read=1, cache_write=1, output=1, reasoning=1)
    assert a + b == Usage(input=11, cache_read=6, cache_write=2, output=4, reasoning=3)


def test_total_input_counts_cached_tokens() -> None:
    assert Usage(input=100, cache_read=900, cache_write=50).total_input == 1050
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ids.py tests/test_cancel.py tests/test_usage.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.core.ids'` (and the same for cancel and usage)

- [ ] **Step 3: Implement**

`src/cookcode/core/ids.py`:
```python
import uuid
from datetime import UTC, datetime


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)
```

`src/cookcode/core/cancel.py`:
```python
import asyncio


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str = "user") -> None:
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
```

`src/cookcode/core/usage.py`:
```python
from typing import Literal

from pydantic import BaseModel, ConfigDict

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "aborted", "error"]


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input=self.input + other.input,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
            reasoning=self.reasoning + other.reasoning,
        )

    @property
    def total_input(self) -> int:
        return self.input + self.cache_read + self.cache_write
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass, no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(core): ids, cancel token, usage" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Item model with a fallback for unknown types

**Files:**
- Create: `src/cookcode/core/_union.py`, `src/cookcode/core/items.py`
- Test: `tests/test_items.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `cookcode.core._union.tag_or_fallback(known: frozenset[str], fallback: str) -> Callable[[Any], str]`
  - `cookcode.core.items`: `TextPart`, `Message(role, parts)` with `.text` property, `Reasoning(provider, model_family, payload, summary_text)` with `.replayable_to(provider, model_family) -> bool`, `ToolCall(id, provider_call_id, provider_item_id, name, arguments)`, `ToolResultStatus`, `ToolResult(call_id, status, content)`, `OtherItem(provider, raw)`, `UnknownItem(type, **extra)`, `Item` (annotated union), `ITEM_TYPES: frozenset[str]`, `ITEM_ADAPTER: TypeAdapter[Item]`, `text_message(role, text) -> Message`

- [ ] **Step 1: Write the failing tests**

`tests/test_items.py`:
```python
import pytest

from cookcode.core.items import (
    ITEM_ADAPTER,
    ITEM_TYPES,
    Item,
    Message,
    OtherItem,
    Reasoning,
    ToolCall,
    ToolResult,
    UnknownItem,
    text_message,
)

SAMPLES: list[Item] = [
    text_message("user", "fix the bug"),
    Reasoning(provider="anthropic", model_family="claude-5", payload={"signature": "abc"}),
    ToolCall(id="c1", provider_call_id="call_1", provider_item_id="fc_1", name="bash",
             arguments={"cmd": "ls -la"}),
    ToolResult(call_id="c1", status="ok", content="file.py\n"),
    OtherItem(provider="openai", raw={"type": "web_search_call", "id": "ws_1"}),
]


def test_samples_cover_every_item_type() -> None:
    assert {item.type for item in SAMPLES} == ITEM_TYPES


@pytest.mark.parametrize("item", SAMPLES, ids=lambda i: i.type)
def test_round_trip_through_json(item: Item) -> None:
    restored = ITEM_ADAPTER.validate_json(ITEM_ADAPTER.dump_json(item))
    assert restored == item
    assert type(restored) is type(item)


def test_unknown_type_is_preserved_not_rejected() -> None:
    raw = b'{"type": "image_generation", "url": "x.png", "size": 3}'
    item = ITEM_ADAPTER.validate_json(raw)
    assert isinstance(item, UnknownItem)
    assert item.type == "image_generation"
    assert ITEM_ADAPTER.validate_json(ITEM_ADAPTER.dump_json(item)) == item


def test_message_text_joins_parts() -> None:
    message = text_message("assistant", "hello")
    assert isinstance(message, Message)
    assert message.text == "hello"


def test_reasoning_replays_only_to_same_provider_and_family() -> None:
    reasoning = Reasoning(provider="anthropic", model_family="claude-5", payload={})
    assert reasoning.replayable_to("anthropic", "claude-5")
    assert not reasoning.replayable_to("anthropic", "claude-4")
    assert not reasoning.replayable_to("openai", "claude-5")


def test_items_are_frozen() -> None:
    call = SAMPLES[2]
    with pytest.raises(ValueError):
        call.name = "other"  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_items.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.core.items'`

- [ ] **Step 3: Implement the discriminator helper**

`src/cookcode/core/_union.py`:
```python
from collections.abc import Callable
from typing import Any, cast


def tag_or_fallback(known: frozenset[str], fallback: str) -> Callable[[Any], str]:
    def discriminate(value: Any) -> str:
        if isinstance(value, dict):
            tag = cast(dict[str, Any], value).get("type")
        else:
            tag = getattr(value, "type", None)
        return tag if isinstance(tag, str) and tag in known else fallback

    return discriminate
```

- [ ] **Step 4: Implement the items**

`src/cookcode/core/items.py`:
```python
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Tag, TypeAdapter

from cookcode.core._union import tag_or_fallback

Role = Literal["user", "assistant", "system"]
ToolResultStatus = Literal["ok", "error", "interrupted", "denied", "unknown"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class TextPart(_Frozen):
    type: Literal["text"] = "text"
    text: str


class Message(_Frozen):
    type: Literal["message"] = "message"
    role: Role
    parts: tuple[TextPart, ...]

    @property
    def text(self) -> str:
        return "".join(part.text for part in self.parts)


class Reasoning(_Frozen):
    type: Literal["reasoning"] = "reasoning"
    provider: str
    model_family: str
    payload: dict[str, Any]
    summary_text: str | None = None

    def replayable_to(self, provider: str, model_family: str) -> bool:
        return self.provider == provider and self.model_family == model_family


class ToolCall(_Frozen):
    type: Literal["tool_call"] = "tool_call"
    id: str
    provider_call_id: str
    provider_item_id: str | None = None
    name: str
    arguments: dict[str, Any]


class ToolResult(_Frozen):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    status: ToolResultStatus
    content: str


class OtherItem(_Frozen):
    type: Literal["other"] = "other"
    provider: str
    raw: dict[str, Any]


class UnknownItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    type: str


ITEM_TYPES = frozenset({"message", "reasoning", "tool_call", "tool_result", "other"})

Item = Annotated[
    Annotated[Message, Tag("message")]
    | Annotated[Reasoning, Tag("reasoning")]
    | Annotated[ToolCall, Tag("tool_call")]
    | Annotated[ToolResult, Tag("tool_result")]
    | Annotated[OtherItem, Tag("other")]
    | Annotated[UnknownItem, Tag("unknown")],
    Discriminator(tag_or_fallback(ITEM_TYPES, "unknown")),
]

ITEM_ADAPTER: TypeAdapter[Item] = TypeAdapter(Item)


def text_message(role: Role, text: str) -> Message:
    return Message(role=role, parts=(TextPart(text=text),))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. If pyright rejects the `Annotated[...] | Annotated[...]` union, rewrite it as `Union[Annotated[Message, Tag("message")], ...]` with `# noqa: UP007` on that line and rerun.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(core): provider-neutral item model with unknown-type fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Event bodies, envelope and run config

**Files:**
- Create: `src/cookcode/core/events.py`, `tests/sample_events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: `Item`, `ToolCall`, `text_message` (Task 3); `Usage`, `StopReason` (Task 2); `new_id`, `utc_now` (Task 2); `tag_or_fallback` (Task 3)
- Produces:
  - `SCHEMA_VERSION = 1`, `Layer = Literal["model", "internal"]`
  - `Limits(max_turns, max_tokens, max_cost_usd, max_wall_s)`, all optional
  - `RunConfig(provider, model, effort, tools, system_prompt_sha256, harness_version, limits)`, `compute_config_id(config: RunConfig) -> str` (16 hex chars)
  - Bodies, each with a `type` literal and a `LAYER` ClassVar: `SessionStarted` (with `.for_config(config)`), `UserMessage`, `SteerMessage`, `ModelRequestStarted`, `ItemEvent`, `ModelRequestFinished`, `ToolCallStarted`, `ToolCallFinished`, `ApprovalRequested`, `ApprovalResolved`, `CheckpointCreated`, `CompactionApplied`, `Interrupted`, `Repaired`, `BudgetExceeded`, `ErrorEvent`, `SessionFinished`, `OtherEvent`
  - `EventBody` (annotated union), `EVENT_TYPES: frozenset[str]`, `SideEffect = Literal["read_only", "mutating", "external"]`
  - `Event(seq, id, ts, schema_version, layer, body)` (frozen; validates that layer matches body)
  - `make_event(seq: int, body: EventBody) -> Event`, `to_json_line(event: Event) -> str`, `from_json_line(line: str) -> Event`

- [ ] **Step 1: Write the shared samples module**

`tests/sample_events.py`:
```python
from cookcode.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    BudgetExceeded,
    CheckpointCreated,
    CompactionApplied,
    ErrorEvent,
    EventBody,
    Interrupted,
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    Repaired,
    RunConfig,
    SessionFinished,
    SessionStarted,
    SteerMessage,
    ToolCallFinished,
    ToolCallStarted,
    UserMessage,
)
from cookcode.core.items import ToolCall
from cookcode.core.usage import Usage


def all_bodies() -> list[EventBody]:
    call = ToolCall(id="c1", provider_call_id="call_1", name="bash", arguments={"cmd": "ls"})
    return [
        SessionStarted.for_config(
            RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")
        ),
        UserMessage(text="fix the failing test"),
        SteerMessage(text="also run ruff"),
        ModelRequestStarted(request_id="r1", view="raw", token_estimate=1200),
        ItemEvent(item=call, request_id="r1"),
        ModelRequestFinished(
            request_id="r1", stop_reason="tool_use", usage=Usage(input=1000, output=50)
        ),
        ToolCallStarted(call_id="c1", tool="bash", arguments={"cmd": "ls"}, side_effect="read_only"),
        ToolCallFinished(
            call_id="c1", exit_status="ok", duration_s=0.12, output_bytes=42, truncated=False
        ),
        ApprovalRequested(call_id="c1", risk="read_only", rule=None),
        ApprovalResolved(call_id="c1", decision="allow", reason=None, rule="bash:ls*"),
        CheckpointCreated(checkpoint_id="k1", paths=("src/app.py",), turn=1),
        CompactionApplied(
            strategy="mask", from_seq=3, to_seq=40, tokens_before=90000, tokens_after=30000
        ),
        Interrupted(phase="tool", killed=("bash:c1",)),
        Repaired(call_id="c1", status="unknown", reason="started, never finished"),
        BudgetExceeded(limit="turns", value=50),
        ErrorEvent(kind="provider", retryable=True, message="429 rate limited"),
        SessionFinished(status="done", turns=3, usage=Usage(input=3000, output=200)),
    ]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_events.py`:
```python
import pytest
from pydantic import ValidationError
from sample_events import all_bodies

from cookcode.core.events import (
    EVENT_TYPES,
    SCHEMA_VERSION,
    Event,
    EventBody,
    ItemEvent,
    OtherEvent,
    RunConfig,
    SessionStarted,
    UserMessage,
    compute_config_id,
    from_json_line,
    make_event,
    to_json_line,
)
from cookcode.core.items import text_message


def test_samples_cover_every_event_type() -> None:
    assert {body.type for body in all_bodies()} == EVENT_TYPES


@pytest.mark.parametrize("body", all_bodies(), ids=lambda b: b.type)
def test_every_event_round_trips_through_a_json_line(body: EventBody) -> None:
    event = make_event(7, body)
    line = to_json_line(event)
    assert "\n" not in line
    restored = from_json_line(line)
    assert restored == event
    assert type(restored.body) is type(body)
    assert restored.schema_version == SCHEMA_VERSION


def test_layer_comes_from_the_body_type() -> None:
    assert make_event(0, UserMessage(text="hi")).layer == "model"
    assert make_event(0, ItemEvent(item=text_message("assistant", "ok"))).layer == "model"
    config = RunConfig(provider="fake", model="m", harness_version="0.0.1")
    assert make_event(0, SessionStarted.for_config(config)).layer == "internal"


def test_layer_mismatch_is_rejected() -> None:
    event = make_event(0, UserMessage(text="hi"))
    data = event.model_dump(mode="json")
    data["layer"] = "internal"
    with pytest.raises(ValidationError):
        Event.model_validate(data)


def test_unknown_event_type_from_a_newer_schema_is_preserved() -> None:
    line = (
        '{"seq": 3, "id": "e3", "ts": "2026-10-26T10:00:00Z", "schema_version": 2,'
        ' "layer": "internal", "body": {"type": "lease_renewed", "worker": "w1", "ttl_s": 30}}'
    )
    event = from_json_line(line)
    assert isinstance(event.body, OtherEvent)
    assert event.body.type == "lease_renewed"
    assert from_json_line(to_json_line(event)) == event


def test_config_id_is_stable_and_sensitive_to_changes() -> None:
    a = RunConfig(provider="fake", model="m1", harness_version="0.0.1")
    b = RunConfig(provider="fake", model="m1", harness_version="0.0.1")
    c = RunConfig(provider="fake", model="m2", harness_version="0.0.1")
    assert compute_config_id(a) == compute_config_id(b)
    assert compute_config_id(a) != compute_config_id(c)
    assert len(compute_config_id(a)) == 16
    assert SessionStarted.for_config(a).config_id == compute_config_id(a)


def test_negative_seq_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_event(-1, UserMessage(text="hi"))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.core.events'`

- [ ] **Step 4: Implement**

`src/cookcode/core/events.py`:
```python
import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from cookcode.core._union import tag_or_fallback
from cookcode.core.ids import new_id, utc_now
from cookcode.core.items import Item
from cookcode.core.usage import StopReason, Usage

SCHEMA_VERSION = 1

Layer = Literal["model", "internal"]
SideEffect = Literal["read_only", "mutating", "external"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Limits(_Frozen):
    max_turns: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_s: float | None = None


class RunConfig(_Frozen):
    provider: str
    model: str
    effort: str | None = None
    tools: tuple[str, ...] = ()
    system_prompt_sha256: str = ""
    harness_version: str
    limits: Limits = Limits()


def compute_config_id(config: RunConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class _Body(_Frozen):
    LAYER: ClassVar[Layer] = "internal"


class SessionStarted(_Body):
    type: Literal["session_started"] = "session_started"
    config: RunConfig
    config_id: str

    @classmethod
    def for_config(cls, config: RunConfig) -> "SessionStarted":
        return cls(config=config, config_id=compute_config_id(config))


class UserMessage(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["user_message"] = "user_message"
    text: str


class SteerMessage(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["steer_message"] = "steer_message"
    text: str


class ModelRequestStarted(_Body):
    type: Literal["model_request_started"] = "model_request_started"
    request_id: str
    view: str
    token_estimate: int


class ItemEvent(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["item"] = "item"
    item: Item
    request_id: str | None = None


class ModelRequestFinished(_Body):
    type: Literal["model_request_finished"] = "model_request_finished"
    request_id: str
    stop_reason: StopReason
    usage: Usage
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str | None = None


class ToolCallStarted(_Body):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str
    tool: str
    arguments: dict[str, Any]
    side_effect: SideEffect


class ToolCallFinished(_Body):
    type: Literal["tool_call_finished"] = "tool_call_finished"
    call_id: str
    exit_status: Literal["ok", "error", "timeout", "killed"]
    duration_s: float
    output_bytes: int
    truncated: bool


class ApprovalRequested(_Body):
    type: Literal["approval_requested"] = "approval_requested"
    call_id: str
    risk: SideEffect
    rule: str | None = None


class ApprovalResolved(_Body):
    type: Literal["approval_resolved"] = "approval_resolved"
    call_id: str
    decision: Literal["allow", "deny", "allow_always"]
    reason: str | None = None
    rule: str | None = None


class CheckpointCreated(_Body):
    type: Literal["checkpoint_created"] = "checkpoint_created"
    checkpoint_id: str
    paths: tuple[str, ...]
    turn: int


class CompactionApplied(_Body):
    type: Literal["compaction_applied"] = "compaction_applied"
    strategy: str
    from_seq: int
    to_seq: int
    tokens_before: int
    tokens_after: int


class Interrupted(_Body):
    type: Literal["interrupted"] = "interrupted"
    phase: Literal["streaming", "tool", "idle"]
    killed: tuple[str, ...] = ()


class Repaired(_Body):
    type: Literal["repaired"] = "repaired"
    call_id: str
    status: Literal["interrupted", "unknown"]
    reason: str


class BudgetExceeded(_Body):
    type: Literal["budget_exceeded"] = "budget_exceeded"
    limit: Literal["turns", "tokens", "cost", "wall_clock"]
    value: float


class ErrorEvent(_Body):
    type: Literal["error"] = "error"
    kind: Literal["provider", "tool", "environment", "internal"]
    retryable: bool
    message: str


class SessionFinished(_Body):
    type: Literal["session_finished"] = "session_finished"
    status: Literal["done", "error", "budget_exceeded", "interrupted"]
    turns: int
    usage: Usage


class OtherEvent(_Body):
    model_config = ConfigDict(frozen=True, extra="allow")
    type: str


EVENT_TYPES = frozenset({
    "session_started", "user_message", "steer_message", "model_request_started", "item",
    "model_request_finished", "tool_call_started", "tool_call_finished", "approval_requested",
    "approval_resolved", "checkpoint_created", "compaction_applied", "interrupted", "repaired",
    "budget_exceeded", "error", "session_finished",
})

EventBody = Annotated[
    Annotated[SessionStarted, Tag("session_started")]
    | Annotated[UserMessage, Tag("user_message")]
    | Annotated[SteerMessage, Tag("steer_message")]
    | Annotated[ModelRequestStarted, Tag("model_request_started")]
    | Annotated[ItemEvent, Tag("item")]
    | Annotated[ModelRequestFinished, Tag("model_request_finished")]
    | Annotated[ToolCallStarted, Tag("tool_call_started")]
    | Annotated[ToolCallFinished, Tag("tool_call_finished")]
    | Annotated[ApprovalRequested, Tag("approval_requested")]
    | Annotated[ApprovalResolved, Tag("approval_resolved")]
    | Annotated[CheckpointCreated, Tag("checkpoint_created")]
    | Annotated[CompactionApplied, Tag("compaction_applied")]
    | Annotated[Interrupted, Tag("interrupted")]
    | Annotated[Repaired, Tag("repaired")]
    | Annotated[BudgetExceeded, Tag("budget_exceeded")]
    | Annotated[ErrorEvent, Tag("error")]
    | Annotated[SessionFinished, Tag("session_finished")]
    | Annotated[OtherEvent, Tag("other")],
    Discriminator(tag_or_fallback(EVENT_TYPES, "other")),
]


class Event(_Frozen):
    seq: int = Field(ge=0)
    id: str
    ts: datetime
    schema_version: int = SCHEMA_VERSION
    layer: Layer
    body: EventBody

    @model_validator(mode="after")
    def _layer_matches_body(self) -> "Event":
        if not isinstance(self.body, OtherEvent) and self.layer != type(self.body).LAYER:
            raise ValueError(f"layer {self.layer!r} does not match body type {self.body.type!r}")
        return self


def make_event(seq: int, body: EventBody) -> Event:
    return Event(seq=seq, id=new_id(), ts=utc_now(), layer=type(body).LAYER, body=body)


def to_json_line(event: Event) -> str:
    return event.model_dump_json()


def from_json_line(line: str) -> Event:
    return Event.model_validate_json(line)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. The same union fallback note as Task 3 Step 5 applies if pyright rejects the `|` form.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(core): typed event log envelope with schema-evolution fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Redaction

**Files:**
- Create: `src/cookcode/redact.py`
- Test: `tests/test_redact.py`

**Interfaces:**
- Consumes: nothing
- Produces: `cookcode.redact.JSONValue` (type alias), `Redactor(secret_values: Iterable[str] = (), min_secret_len: int = 8)` with `.redact_text(text: str) -> str`, `.redact(value: JSONValue) -> JSONValue`, `Redactor.from_env(names: Iterable[str], environ: Mapping[str, str]) -> Redactor`. Replacement format: `[REDACTED:<kind>]`.

- [ ] **Step 1: Write the failing tests**

Fake secrets are built by concatenation so secret scanners don't flag the test file.

`tests/test_redact.py`:
```python
import pytest

from cookcode.redact import Redactor

FAKES = {
    "anthropic_key": "sk-ant-" + "a1B2" * 10,
    "openrouter_key": "sk-or-v1-" + "f0" * 32,
    "openai_key": "sk-proj-" + "Zx9_" * 8,
    "aws_access_key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github_token": "ghp_" + "a" * 36,
    "google_api_key": "AIza" + "b" * 35,
    "slack_token": "xoxb-" + "1234567890-abc",
    "bearer_token": "Bearer " + "t0ken." * 5,
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("kind", sorted(FAKES))
def test_each_pattern_is_redacted(kind: str) -> None:
    text = f"before {FAKES[kind]} after"
    redacted = Redactor().redact_text(text)
    assert FAKES[kind] not in redacted
    assert f"[REDACTED:{kind}]" in redacted
    assert redacted.startswith("before ")
    assert redacted.endswith(" after")


def test_explicit_secret_values_are_redacted() -> None:
    redactor = Redactor(secret_values=["hunter2-database-password"])
    assert redactor.redact_text("pw=hunter2-database-password") == "pw=[REDACTED:secret_value]"


def test_short_secret_values_are_ignored() -> None:
    redactor = Redactor(secret_values=["abc"])
    assert redactor.redact_text("abc abc") == "abc abc"


def test_from_env_picks_named_variables_only() -> None:
    environ = {"DB_PASSWORD": "correct-horse-battery", "HOME": "/home/me"}
    redactor = Redactor.from_env(["DB_PASSWORD", "MISSING"], environ)
    assert redactor.redact_text("correct-horse-battery /home/me") == (
        "[REDACTED:secret_value] /home/me"
    )


def test_nested_structures_are_walked() -> None:
    key = FAKES["openai_key"]
    value = {"a": [key, {"b": f"x {key}"}], "n": 3, "ok": True, "none": None}
    assert Redactor().redact(value) == {
        "a": ["[REDACTED:openai_key]", {"b": "x [REDACTED:openai_key]"}],
        "n": 3,
        "ok": True,
        "none": None,
    }


def test_ordinary_text_is_untouched() -> None:
    text = "run sk-short and ask the task-runner about AKIA"
    assert Redactor().redact_text(text) == text


def test_redaction_is_idempotent() -> None:
    once = Redactor().redact_text(f"k={FAKES['anthropic_key']}")
    assert Redactor().redact_text(once) == once
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_redact.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.redact'`

- [ ] **Step 3: Implement**

`src/cookcode/redact.py`:
```python
import re
from collections.abc import Iterable, Mapping

type JSONValue = str | int | float | bool | None | dict[str, JSONValue] | list[JSONValue]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openrouter_key", re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
)


class Redactor:
    def __init__(self, secret_values: Iterable[str] = (), min_secret_len: int = 8) -> None:
        unique = {value for value in secret_values if len(value) >= min_secret_len}
        self._values = sorted(unique, key=len, reverse=True)

    @classmethod
    def from_env(cls, names: Iterable[str], environ: Mapping[str, str]) -> "Redactor":
        return cls(environ[name] for name in names if name in environ)

    def redact_text(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, "[REDACTED:secret_value]")
        for kind, pattern in _PATTERNS:
            text = pattern.sub(f"[REDACTED:{kind}]", text)
        return text

    def redact(self, value: JSONValue) -> JSONValue:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: secret redaction applied before anything is persisted" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: JSONL session store: append, read, lock, fsync policy, torn tail

**Files:**
- Create: `src/cookcode/store/base.py`, `src/cookcode/store/jsonl.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Event`, `EventBody`, `make_event`, `from_json_line` (Task 4); `Redactor` (Task 5)
- Produces:
  - `cookcode.store.base`: `SessionLockedError`, `SessionNotFoundError`, `SessionLog` protocol (`session_id`, `next_seq`, `append(body, *, durable=False) -> Event`, `read(from_seq=0) -> list[Event]`, `close()`), `SessionStore` protocol (`open(session_id, *, create=False) -> SessionLog`, `read(session_id, from_seq=0) -> list[Event]`, `fork(session_id, at_seq, new_session_id) -> None`, `list_sessions() -> list[str]`)
  - `cookcode.store.jsonl`: `DURABLE_TYPES = frozenset({"tool_call_started"})`, `JsonlSessionStore(root: Path, redactor: Redactor | None = None)`, `JsonlSessionLog` (context manager)
  - On disk: `<root>/<session_id>.jsonl` and `<root>/<session_id>.lock`

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
import os
from pathlib import Path

import pytest
from sample_events import all_bodies

from cookcode.core.events import ToolCallStarted, UserMessage
from cookcode.redact import Redactor
from cookcode.store.base import SessionLockedError, SessionNotFoundError
from cookcode.store.jsonl import JsonlSessionStore


def test_append_assigns_sequential_seq_and_reads_back(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        written = [log.append(UserMessage(text=f"m{i}")) for i in range(3)]
    assert [e.seq for e in written] == [0, 1, 2]
    assert store.read("s1") == written
    assert store.read("s1", from_seq=1) == written[1:]


def test_every_event_type_survives_the_store(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        written = [log.append(body) for body in all_bodies()]
    assert store.read("s1") == written


def test_open_missing_session_without_create_fails(tmp_path: Path) -> None:
    with pytest.raises(SessionNotFoundError):
        JsonlSessionStore(tmp_path).open("nope")


def test_reopen_continues_the_sequence(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="one"))
    with store.open("s1") as log:
        assert log.next_seq == 1
        assert log.append(UserMessage(text="two")).seq == 1


def test_second_writer_is_refused_until_the_first_closes(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    first = store.open("s1", create=True)
    with pytest.raises(SessionLockedError):
        store.open("s1")
    first.close()
    store.open("s1").close()


def test_fsync_only_for_write_ahead_events_or_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("cookcode.store.jsonl.os.fsync", lambda fd: calls.append(fd))
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="no fsync"))
        assert calls == []
        log.append(
            ToolCallStarted(call_id="c1", tool="bash", arguments={}, side_effect="mutating")
        )
        assert len(calls) == 1
        log.append(UserMessage(text="forced"), durable=True)
        assert len(calls) == 2


def test_torn_tail_is_ignored_on_read_and_truncated_on_open(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="one"))
    path = tmp_path / "s1.jsonl"
    with path.open("ab") as f:
        f.write(b'{"seq": 1, "id": "x", "ts"')
    assert [e.seq for e in store.read("s1")] == [0]
    with store.open("s1") as log:
        assert log.append(UserMessage(text="two")).seq == 1
    assert path.read_bytes().endswith(b"\n")
    assert store.read("s1")[1].body == UserMessage(text="two")


def test_secrets_never_reach_disk(tmp_path: Path) -> None:
    key = "sk-ant-" + "a1B2" * 10
    store = JsonlSessionStore(tmp_path, Redactor())
    with store.open("s1", create=True) as log:
        event = log.append(UserMessage(text=f"use {key}"))
    assert event.body == UserMessage(text="use [REDACTED:anthropic_key]")
    assert key not in (tmp_path / "s1.jsonl").read_text()


def test_append_after_close_fails(tmp_path: Path) -> None:
    log = JsonlSessionStore(tmp_path).open("s1", create=True)
    log.close()
    with pytest.raises(ValueError):
        log.append(UserMessage(text="late"))


def test_lock_file_descriptor_is_released_on_close(tmp_path: Path) -> None:
    before = len(os.listdir("/proc/self/fd"))
    log = JsonlSessionStore(tmp_path).open("s1", create=True)
    log.close()
    assert len(os.listdir("/proc/self/fd")) == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.store.base'`

- [ ] **Step 3: Implement the protocols**

`src/cookcode/store/base.py`:
```python
from typing import Protocol

from cookcode.core.events import Event, EventBody


class SessionLockedError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


class SessionLog(Protocol):
    @property
    def session_id(self) -> str: ...

    @property
    def next_seq(self) -> int: ...

    def append(self, body: EventBody, *, durable: bool = False) -> Event: ...

    def read(self, from_seq: int = 0) -> list[Event]: ...

    def close(self) -> None: ...


class SessionStore(Protocol):
    def open(self, session_id: str, *, create: bool = False) -> SessionLog: ...

    def read(self, session_id: str, from_seq: int = 0) -> list[Event]: ...

    def fork(self, session_id: str, at_seq: int, new_session_id: str) -> None: ...

    def list_sessions(self) -> list[str]: ...
```

- [ ] **Step 4: Implement the JSONL store**

`src/cookcode/store/jsonl.py`:
```python
import fcntl
import os
from pathlib import Path
from types import TracebackType

from cookcode.core.events import Event, EventBody, from_json_line, make_event
from cookcode.redact import Redactor
from cookcode.store.base import SessionLockedError, SessionNotFoundError

DURABLE_TYPES = frozenset({"tool_call_started"})


def _read_events(path: Path) -> list[Event]:
    events: list[Event] = []
    with path.open("rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            events.append(from_json_line(raw.decode()))
    return events


def _truncate_torn_tail(path: Path) -> None:
    with path.open("r+b") as f:
        data = f.read()
        if data and not data.endswith(b"\n"):
            f.truncate(data.rfind(b"\n") + 1)


class JsonlSessionLog:
    def __init__(self, session_id: str, path: Path, lock_path: Path, redactor: Redactor) -> None:
        self._session_id = session_id
        self._path = path
        self._redactor = redactor
        self._lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            raise SessionLockedError(session_id) from None
        _truncate_torn_tail(path)
        existing = _read_events(path)
        self._next_seq = existing[-1].seq + 1 if existing else 0
        self._file = path.open("ab")
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def append(self, body: EventBody, *, durable: bool = False) -> Event:
        if self._closed:
            raise ValueError(f"session log {self._session_id} is closed")
        event = make_event(self._next_seq, body)
        event = Event.model_validate(self._redactor.redact(event.model_dump(mode="json")))
        self._file.write((event.model_dump_json() + "\n").encode())
        self._file.flush()
        if durable or body.type in DURABLE_TYPES:
            os.fsync(self._file.fileno())
        self._next_seq += 1
        return event

    def read(self, from_seq: int = 0) -> list[Event]:
        return [event for event in _read_events(self._path) if event.seq >= from_seq]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._file.close()
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)

    def __enter__(self) -> "JsonlSessionLog":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class JsonlSessionStore:
    def __init__(self, root: Path, redactor: Redactor | None = None) -> None:
        self._root = root
        self._redactor = redactor or Redactor()
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.jsonl"

    def open(self, session_id: str, *, create: bool = False) -> JsonlSessionLog:
        path = self._path(session_id)
        if not path.exists():
            if not create:
                raise SessionNotFoundError(session_id)
            path.touch()
        lock_path = self._root / f"{session_id}.lock"
        return JsonlSessionLog(session_id, path, lock_path, self._redactor)

    def read(self, session_id: str, from_seq: int = 0) -> list[Event]:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(session_id)
        return [event for event in _read_events(path) if event.seq >= from_seq]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. If pyright flags `self._redactor.redact(event.model_dump(mode="json"))` because `dict[str, Any]` isn't seen as `JSONValue`, wrap the argument in `cast("JSONValue", ...)` (import `cast` from `typing` and `JSONValue` from `cookcode.redact`).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(store): crash-safe JSONL session log with single-writer lock" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Fork and list sessions

**Files:**
- Modify: `src/cookcode/store/jsonl.py` (add `fork` and `list_sessions` to `JsonlSessionStore`)
- Test: `tests/test_store_fork.py`

**Interfaces:**
- Consumes: `JsonlSessionStore` (Task 6)
- Produces: `JsonlSessionStore.fork(session_id: str, at_seq: int, new_session_id: str) -> None` copies events `0..at_seq` inclusive into a new session atomically. `JsonlSessionStore.list_sessions() -> list[str]` returns sorted session IDs.

- [ ] **Step 1: Write the failing tests**

`tests/test_store_fork.py`:
```python
from pathlib import Path

import pytest

from cookcode.core.events import UserMessage
from cookcode.store.jsonl import JsonlSessionStore


def _store_with_five(tmp_path: Path) -> JsonlSessionStore:
    store = JsonlSessionStore(tmp_path)
    with store.open("main", create=True) as log:
        for i in range(5):
            log.append(UserMessage(text=f"m{i}"))
    return store


def test_fork_copies_the_prefix_and_continues_independently(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    store.fork("main", 2, "branch")
    assert [e.seq for e in store.read("branch")] == [0, 1, 2]
    assert store.read("branch") == store.read("main")[:3]
    with store.open("branch") as log:
        assert log.append(UserMessage(text="alt")).seq == 3
    assert len(store.read("main")) == 5


def test_fork_at_a_missing_seq_fails(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    with pytest.raises(ValueError):
        store.fork("main", 99, "branch")


def test_fork_never_overwrites_an_existing_session(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    store.fork("main", 1, "branch")
    with pytest.raises(FileExistsError):
        store.fork("main", 2, "branch")


def test_list_sessions_is_sorted_and_ignores_temp_files(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    store.fork("main", 0, "a-branch")
    (tmp_path / "half.jsonl.tmp").write_text("")
    assert store.list_sessions() == ["a-branch", "main"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store_fork.py -q`
Expected: FAIL with `AttributeError: 'JsonlSessionStore' object has no attribute 'fork'`

- [ ] **Step 3: Implement**

Add these methods to `JsonlSessionStore` in `src/cookcode/store/jsonl.py`:
```python
    def fork(self, session_id: str, at_seq: int, new_session_id: str) -> None:
        prefix = [event for event in self.read(session_id) if event.seq <= at_seq]
        if not prefix or prefix[-1].seq != at_seq:
            raise ValueError(f"seq {at_seq} not found in session {session_id}")
        target = self._path(new_session_id)
        if target.exists():
            raise FileExistsError(new_session_id)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as f:
            for event in prefix:
                f.write((event.model_dump_json() + "\n").encode())
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(target)

    def list_sessions(self) -> list[str]:
        return sorted(path.stem for path in self._root.glob("*.jsonl"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(store): fork a session at any seq, list sessions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Provider contract and the scripted FakeProvider

**Files:**
- Create: `src/cookcode/providers/base.py`, `src/cookcode/providers/fake.py`
- Test: `tests/test_fake_provider.py`

**Interfaces:**
- Consumes: `Item`, `Message`, `ToolCall`, `text_message` (Task 3); `Usage`, `StopReason`, `CancelToken`, `new_id` (Task 2)
- Produces:
  - `cookcode.providers.base`: `ToolSchema(name, description, parameters)`, `ModelRequest(model, system, tools, items, max_output_tokens)`, `ModelCaps(context_window, parallel_tool_calls, reasoning, prompt_caching)`, stream events `ItemStarted(index, kind)`, `ItemDelta(index, text)`, `ItemDone(index, item)`, `UsageReport(usage, raw)`, `StreamEnd(stop_reason, error)`, `StreamEvent` union, `Provider` protocol (`name: str`, `capabilities(model) -> ModelCaps`, `stream(request, cancel) -> AsyncIterator[StreamEvent]`)
  - `cookcode.providers.fake`: `FakeTurn(items, usage, stop_reason, fail_with)`, `FakeScriptExhausted`, `FakeProvider(script, *, chunk_size=8, event_delay_s=0.0, caps=None)` with `.requests: list[ModelRequest]`, helpers `say(text) -> Message`, `call(name, provider_call_id=None, **arguments) -> ToolCall`

- [ ] **Step 1: Write the failing tests**

`tests/test_fake_provider.py`:
```python
import pytest

from cookcode.core.cancel import CancelToken
from cookcode.core.items import text_message
from cookcode.core.usage import Usage
from cookcode.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    UsageReport,
)
from cookcode.providers.fake import FakeProvider, FakeScriptExhausted, FakeTurn, call, say

REQUEST = ModelRequest(model="fake-1", system="be brief", items=(text_message("user", "hi"),))


async def collect(provider: FakeProvider, cancel: CancelToken | None = None) -> list[StreamEvent]:
    return [event async for event in provider.stream(REQUEST, cancel or CancelToken())]


async def test_text_turn_streams_deltas_then_the_finished_item() -> None:
    fake = FakeProvider([FakeTurn(items=(say("hello there, world"),), usage=Usage(output=4))],
                        chunk_size=5)
    events = await collect(fake)
    assert isinstance(events[0], ItemStarted) and events[0].kind == "message"
    deltas = [e.text for e in events if isinstance(e, ItemDelta)]
    assert "".join(deltas) == "hello there, world"
    assert len(deltas) == 4
    done = [e for e in events if isinstance(e, ItemDone)]
    assert [d.item for d in done] == [say("hello there, world")]
    assert events[-2] == UsageReport(usage=Usage(output=4))
    assert events[-1] == StreamEnd(stop_reason="end_turn")


async def test_tool_call_turn_ends_with_tool_use() -> None:
    fake = FakeProvider([FakeTurn(items=(say("checking"), call("bash", cmd="ls")))])
    events = await collect(fake)
    done = [e.item for e in events if isinstance(e, ItemDone)]
    assert done[1].type == "tool_call"
    assert events[-1] == StreamEnd(stop_reason="tool_use")


async def test_requests_are_recorded_in_order() -> None:
    fake = FakeProvider([FakeTurn(items=(say("a"),)), FakeTurn(items=(say("b"),))])
    await collect(fake)
    await collect(fake)
    assert fake.requests == [REQUEST, REQUEST]


async def test_running_past_the_script_raises() -> None:
    fake = FakeProvider([FakeTurn(items=(say("only"),))])
    await collect(fake)
    with pytest.raises(FakeScriptExhausted):
        await collect(fake)


async def test_scripted_failure_ends_with_error() -> None:
    fake = FakeProvider([FakeTurn(items=(), fail_with="overloaded")])
    assert await collect(fake) == [StreamEnd(stop_reason="error", error="overloaded")]


async def test_cancel_mid_stream_keeps_only_finished_items() -> None:
    fake = FakeProvider([FakeTurn(items=(say("first"), say("second")))], event_delay_s=0.01)
    cancel = CancelToken()
    seen: list[StreamEvent] = []
    async for event in fake.stream(REQUEST, cancel):
        seen.append(event)
        if isinstance(event, ItemDone):
            cancel.cancel()
    assert [e.item for e in seen if isinstance(e, ItemDone)] == [say("first")]
    assert seen[-1] == StreamEnd(stop_reason="aborted")


async def test_cancel_before_start_aborts_immediately() -> None:
    fake = FakeProvider([FakeTurn(items=(say("never"),))])
    cancel = CancelToken()
    cancel.cancel()
    assert await collect(fake, cancel) == [StreamEnd(stop_reason="aborted")]


def test_call_helper_builds_distinct_ids() -> None:
    a, b = call("read", path="x.py"), call("read", path="x.py")
    assert a.id != b.id
    assert a.provider_call_id != b.provider_call_id
    assert a.arguments == {"path": "x.py"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fake_provider.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.providers.base'`

- [ ] **Step 3: Implement the contract**

`src/cookcode/providers/base.py`:
```python
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from cookcode.core.cancel import CancelToken
from cookcode.core.items import Item
from cookcode.core.usage import StopReason, Usage


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ToolSchema(_Frozen):
    name: str
    description: str
    parameters: dict[str, Any]


class ModelRequest(_Frozen):
    model: str
    system: str
    tools: tuple[ToolSchema, ...] = ()
    items: tuple[Item, ...]
    max_output_tokens: int | None = None


class ModelCaps(_Frozen):
    context_window: int
    parallel_tool_calls: bool
    reasoning: Literal["none", "opaque", "text"]
    prompt_caching: bool


class ItemStarted(_Frozen):
    type: Literal["item_started"] = "item_started"
    index: int
    kind: str


class ItemDelta(_Frozen):
    type: Literal["item_delta"] = "item_delta"
    index: int
    text: str


class ItemDone(_Frozen):
    type: Literal["item_done"] = "item_done"
    index: int
    item: Item


class UsageReport(_Frozen):
    type: Literal["usage"] = "usage"
    usage: Usage
    raw: dict[str, Any] = Field(default_factory=dict)


class StreamEnd(_Frozen):
    type: Literal["stream_end"] = "stream_end"
    stop_reason: StopReason
    error: str | None = None


StreamEvent = ItemStarted | ItemDelta | ItemDone | UsageReport | StreamEnd


class Provider(Protocol):
    name: str

    def capabilities(self, model: str) -> ModelCaps: ...

    def stream(self, request: ModelRequest, cancel: CancelToken) -> AsyncIterator[StreamEvent]: ...
```

- [ ] **Step 4: Implement the FakeProvider**

`src/cookcode/providers/fake.py`:
```python
import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict

from cookcode.core.cancel import CancelToken
from cookcode.core.ids import new_id
from cookcode.core.items import Item, Message, ToolCall, text_message
from cookcode.core.usage import StopReason, Usage
from cookcode.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    UsageReport,
)

DEFAULT_CAPS = ModelCaps(
    context_window=200_000, parallel_tool_calls=True, reasoning="none", prompt_caching=False
)


class FakeScriptExhausted(Exception):
    pass


class FakeTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[Item, ...]
    usage: Usage = Usage()
    stop_reason: StopReason | None = None
    fail_with: str | None = None


def say(text: str) -> Message:
    return text_message("assistant", text)


def call(name: str, provider_call_id: str | None = None, **arguments: Any) -> ToolCall:
    call_id = new_id()
    return ToolCall(
        id=call_id,
        provider_call_id=provider_call_id or f"call_{call_id[:12]}",
        name=name,
        arguments=arguments,
    )


def _default_stop(items: tuple[Item, ...]) -> StopReason:
    return "tool_use" if any(isinstance(item, ToolCall) for item in items) else "end_turn"


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        script: Iterable[FakeTurn],
        *,
        chunk_size: int = 8,
        event_delay_s: float = 0.0,
        caps: ModelCaps | None = None,
    ) -> None:
        self._script = list(script)
        self._next = 0
        self._chunk_size = chunk_size
        self._event_delay_s = event_delay_s
        self._caps = caps or DEFAULT_CAPS
        self.requests: list[ModelRequest] = []

    def capabilities(self, model: str) -> ModelCaps:
        return self._caps

    async def stream(self, request: ModelRequest, cancel: CancelToken) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        if self._next >= len(self._script):
            raise FakeScriptExhausted(f"no scripted turn for request #{len(self.requests)}")
        turn = self._script[self._next]
        self._next += 1
        if turn.fail_with is not None:
            yield StreamEnd(stop_reason="error", error=turn.fail_with)
            return
        for index, item in enumerate(turn.items):
            if cancel.cancelled:
                yield StreamEnd(stop_reason="aborted")
                return
            yield ItemStarted(index=index, kind=item.type)
            if isinstance(item, Message):
                text = item.text
                for start in range(0, len(text), self._chunk_size):
                    await asyncio.sleep(self._event_delay_s)
                    if cancel.cancelled:
                        yield StreamEnd(stop_reason="aborted")
                        return
                    yield ItemDelta(index=index, text=text[start : start + self._chunk_size])
            await asyncio.sleep(self._event_delay_s)
            if cancel.cancelled:
                yield StreamEnd(stop_reason="aborted")
                return
            yield ItemDone(index=index, item=item)
        yield UsageReport(usage=turn.usage)
        yield StreamEnd(stop_reason=turn.stop_reason or _default_stop(turn.items))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(providers): streaming provider contract and scripted FakeProvider" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Environment contract and local file I/O

**Files:**
- Create: `src/cookcode/env/base.py`, `src/cookcode/env/local.py`
- Test: `tests/test_local_env_files.py`

**Interfaces:**
- Consumes: `CancelToken`, `new_id` (Task 2)
- Produces:
  - `cookcode.env.base`: `ExecResult(exit_code: int | None, output: str, output_bytes: int, duration_s: float, timed_out=False, cancelled=False, truncated=False)`, `Environment` protocol (`exec(cmd, *, cwd=".", timeout_s, max_output_bytes, cancel) -> ExecResult`, `read_file(path) -> bytes`, `write_file(path, data) -> None`)
  - `cookcode.env.local.LocalEnvironment(root: Path)` with `read_file` and `write_file` (atomic, creates parents, keeps the file mode). `exec` arrives in Task 10.
  - The spec's `Environment.snapshot()` is deliberately left out. It lands in M2 together with undo, where its first consumer is.

- [ ] **Step 1: Write the failing tests**

`tests/test_local_env_files.py`:
```python
import stat
from pathlib import Path

import pytest

from cookcode.env.local import LocalEnvironment


async def test_write_then_read_relative_path(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    await env.write_file("a/b/c.txt", b"hello")
    assert await env.read_file("a/b/c.txt") == b"hello"
    assert (tmp_path / "a/b/c.txt").read_bytes() == b"hello"


async def test_absolute_paths_are_used_as_given(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path / "root")
    target = tmp_path / "elsewhere.txt"
    await env.write_file(str(target), b"x")
    assert target.read_bytes() == b"x"


async def test_write_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    await env.write_file("f.txt", b"one")
    await env.write_file("f.txt", b"two")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]
    assert (tmp_path / "f.txt").read_bytes() == b"two"


async def test_write_keeps_the_executable_bit(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    await LocalEnvironment(tmp_path).write_file("run.sh", b"#!/bin/sh\necho bye\n")
    assert script.stat().st_mode & stat.S_IXUSR


async def test_reading_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await LocalEnvironment(tmp_path).read_file("missing.txt")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_local_env_files.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cookcode.env.local'`

- [ ] **Step 3: Implement the contract**

`src/cookcode/env/base.py`:
```python
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from cookcode.core.cancel import CancelToken


class ExecResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int | None
    output: str
    output_bytes: int
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False


class Environment(Protocol):
    async def exec(
        self,
        cmd: str,
        *,
        cwd: str = ".",
        timeout_s: float,
        max_output_bytes: int,
        cancel: CancelToken,
    ) -> ExecResult: ...

    async def read_file(self, path: str) -> bytes: ...

    async def write_file(self, path: str, data: bytes) -> None: ...
```

- [ ] **Step 4: Implement file I/O**

`src/cookcode/env/local.py`:
```python
import asyncio
import os
import shutil
from pathlib import Path

from cookcode.core.ids import new_id


class LocalEnvironment:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._root / candidate

    async def read_file(self, path: str) -> bytes:
        return await asyncio.to_thread(self._resolve(path).read_bytes)

    async def write_file(self, path: str, data: bytes) -> None:
        target = self._resolve(path)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{new_id()}.tmp")
            tmp.write_bytes(data)
            if target.exists():
                shutil.copymode(target, tmp)
            os.replace(tmp, target)

        await asyncio.to_thread(write)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. Pyright reports `LocalEnvironment` as an incomplete `Environment` only where it's assigned to that type, and nothing does that until Task 10.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(env): environment contract, atomic local file I/O" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Local exec: process groups, output caps, timeout and cancel

**Files:**
- Modify: `src/cookcode/env/local.py` (add `_CappedBuffer`, `_kill_group`, `LocalEnvironment.exec`)
- Test: `tests/test_local_env_exec.py`

**Interfaces:**
- Consumes: `ExecResult`, `Environment` (Task 9); `CancelToken` (Task 2)
- Produces: `LocalEnvironment.exec(cmd, *, cwd=".", timeout_s, max_output_bytes, cancel) -> ExecResult`, satisfying the full `Environment` protocol. Semantics: `bash -c` in a new session (own process group), stdin from `/dev/null`, stderr merged into stdout, output capped to head plus tail with a `[... N bytes truncated ...]` marker, the whole group killed on timeout, on cancel, and after normal exit (no background process outlives a command). `exit_code` is `None` when timed out or cancelled.

- [ ] **Step 1: Write the failing tests**

`tests/test_local_env_exec.py`:
```python
import asyncio
import time
from pathlib import Path

from cookcode.core.cancel import CancelToken
from cookcode.env.base import Environment
from cookcode.env.local import LocalEnvironment, _CappedBuffer


def _is_dead(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


async def _wait_dead(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _is_dead(pid):
            return True
        await asyncio.sleep(0.05)
    return False


def test_local_environment_satisfies_the_protocol(tmp_path: Path) -> None:
    env: Environment = LocalEnvironment(tmp_path)
    assert env is not None


def test_capped_buffer_keeps_head_and_tail() -> None:
    buf = _CappedBuffer(10)
    for chunk in (b"abc", b"defgh", b"ijklmnop"):
        buf.feed(chunk)
    assert buf.total == 16
    assert buf.truncated
    assert buf.render() == "abcde\n[... 6 bytes truncated ...]\nlmnop"


def test_capped_buffer_untruncated_is_verbatim() -> None:
    buf = _CappedBuffer(10)
    buf.feed(b"0123456789")
    assert not buf.truncated
    assert buf.render() == "0123456789"


async def test_echo_and_exit_code(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    ok = await env.exec("echo hello", timeout_s=10, max_output_bytes=1000, cancel=CancelToken())
    assert (ok.exit_code, ok.output, ok.output_bytes) == (0, "hello\n", 6)
    bad = await env.exec("exit 3", timeout_s=10, max_output_bytes=1000, cancel=CancelToken())
    assert bad.exit_code == 3


async def test_stderr_is_merged_and_stdin_is_closed(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("echo out; echo err >&2; cat", timeout_s=5, max_output_bytes=1000,
                         cancel=CancelToken())
    assert res.exit_code == 0
    assert res.output == "out\nerr\n"


async def test_cwd_is_relative_to_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    env = LocalEnvironment(tmp_path)
    res = await env.exec("pwd", cwd="sub", timeout_s=5, max_output_bytes=1000, cancel=CancelToken())
    assert res.output.strip() == str((tmp_path / "sub").resolve())


async def test_large_output_is_truncated_head_and_tail(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("head -c 10000 /dev/zero | tr '\\0' a", timeout_s=10,
                         max_output_bytes=1000, cancel=CancelToken())
    assert res.truncated
    assert res.output_bytes == 10000
    assert res.output.startswith("a" * 500 + "\n[... 9000 bytes truncated ...]\n")
    assert res.output.endswith("a" * 500)


async def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("sleep 30 & echo $! > child.pid; wait", timeout_s=0.5,
                         max_output_bytes=1000, cancel=CancelToken())
    assert res.timed_out
    assert res.exit_code is None
    assert res.duration_s < 5
    assert await _wait_dead(int((tmp_path / "child.pid").read_text()))


async def test_background_children_do_not_outlive_the_command(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("sleep 30 & echo $! > child.pid", timeout_s=10, max_output_bytes=1000,
                         cancel=CancelToken())
    assert res.exit_code == 0
    assert res.duration_s < 5
    assert await _wait_dead(int((tmp_path / "child.pid").read_text()))


async def test_cancel_kills_a_running_command(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    cancel = CancelToken()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.2)
        cancel.cancel()

    canceller = asyncio.create_task(cancel_soon())
    res = await env.exec("sleep 30", timeout_s=10, max_output_bytes=1000, cancel=cancel)
    await canceller
    assert res.cancelled
    assert not res.timed_out
    assert res.exit_code is None
    assert res.duration_s < 5


async def test_already_cancelled_never_spawns(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    cancel = CancelToken()
    cancel.cancel()
    res = await env.exec("touch should-not-exist", timeout_s=5, max_output_bytes=1000,
                         cancel=cancel)
    assert res.cancelled
    assert not (tmp_path / "should-not-exist").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_local_env_exec.py -q`
Expected: FAIL with `ImportError: cannot import name '_CappedBuffer' from 'cookcode.env.local'`

- [ ] **Step 3: Implement**

Add to `src/cookcode/env/local.py`. The imports at the top become:
```python
import asyncio
import os
import shutil
import signal
import time
from pathlib import Path

from cookcode.core.cancel import CancelToken
from cookcode.core.ids import new_id
from cookcode.env.base import ExecResult

_READ_CHUNK = 65536
_PUMP_DRAIN_TIMEOUT_S = 5.0
```

Add above `class LocalEnvironment`:
```python
class _CappedBuffer:
    def __init__(self, max_bytes: int) -> None:
        self._half = max(max_bytes // 2, 1)
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._half - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self._tail += chunk
            overflow = len(self._tail) - self._half
            if overflow > 0:
                del self._tail[:overflow]

    @property
    def truncated(self) -> bool:
        return self.total > len(self._head) + len(self._tail)

    def render(self) -> str:
        head = self._head.decode(errors="replace")
        tail = self._tail.decode(errors="replace")
        if not self.truncated:
            return head + tail
        dropped = self.total - len(self._head) - len(self._tail)
        return f"{head}\n[... {dropped} bytes truncated ...]\n{tail}"


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
```

Add this method to `LocalEnvironment`:
```python
    async def exec(
        self,
        cmd: str,
        *,
        cwd: str = ".",
        timeout_s: float,
        max_output_bytes: int,
        cancel: CancelToken,
    ) -> ExecResult:
        started = time.monotonic()
        if cancel.cancelled:
            return ExecResult(exit_code=None, output="", output_bytes=0, duration_s=0.0,
                              cancelled=True)
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            cmd,
            cwd=self._resolve(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        buffer = _CappedBuffer(max_output_bytes)
        stdout = proc.stdout
        assert stdout is not None

        async def pump() -> None:
            while chunk := await stdout.read(_READ_CHUNK):
                buffer.feed(chunk)

        pump_task = asyncio.create_task(pump())
        wait_task = asyncio.create_task(proc.wait())
        cancel_task = asyncio.create_task(cancel.wait())
        done, _ = await asyncio.wait(
            {wait_task, cancel_task}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        timed_out = not done
        cancelled = cancel_task in done and wait_task not in done
        _kill_group(proc.pid)
        await wait_task
        cancel_task.cancel()
        try:
            await asyncio.wait_for(pump_task, timeout=_PUMP_DRAIN_TIMEOUT_S)
        except TimeoutError:
            pump_task.cancel()
        return ExecResult(
            exit_code=None if timed_out or cancelled else proc.returncode,
            output=buffer.render(),
            output_bytes=buffer.total,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=buffer.truncated,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. Run the exec tests five times to catch flakiness: `for i in 1 2 3 4 5; do uv run pytest tests/test_local_env_exec.py -q || break; done`. Expected: five clean runs.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(env): local exec with process-group kill, head+tail output cap, timeout, cancel" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: M0 definition of done: decision records, walkthrough, cross-questions

**Files:**
- Create: `docs/decisions/0000-template.md`, `docs/decisions/0001` through `0008` (names below), `docs/walkthroughs/m0.md`

**Interfaces:**
- Consumes: everything from Tasks 1 to 10
- Produces: the records and walkthrough that make M0 explainable without AI

- [ ] **Step 1: Write the template**

`docs/decisions/0000-template.md`:
```markdown
# NNNN. Title

Status: accepted (M0) | superseded by NNNN
Date: YYYY-MM-DD

## Context
What problem forced a decision. One paragraph.

## Options considered
- Option A: what it is, what it costs.
- Option B: ...

## Decision
What we chose, in one or two sentences.

## Consequences
What this makes easy, what it makes hard, what we now have to maintain.

## What would change it
The observation or requirement that would make us revisit this.
```

- [ ] **Step 2: Write the eight decision records**

Each follows the template, is under 400 words, uses the code as it exists (file and function names), and has no em dashes. Points each must cover:

`0001-event-log-is-source-of-truth.md`
- Options: in-memory message list (pi, mini-swe-agent), per-event files (OpenHands), JSONL per session, SQLite.
- Chose JSONL per session: append-only, human-greppable, one writer, no migrations.
- Consequences: context, resume, replay, exports and Flagship 2 checkpoints are all projections. Queries across sessions need a separate index later.
- Changes if: cross-session queries become hot, or files grow past what a full read on open tolerates.

`0002-event-envelope-and-schema-evolution.md`
- Envelope fields: `seq`, `id`, `ts`, `schema_version`, `layer`, `body`. Layer is derived from the body class and validated.
- Unknown types from newer schemas land in `OtherEvent` / `UnknownItem` via `tag_or_fallback`, so old binaries can read new logs without crashing. Codex's `Other` variant is the precedent.
- Additive changes only within a schema version. Breaking changes bump `SCHEMA_VERSION` and need a read-time migration.

`0003-crash-safety-torn-tail-and-fsync-policy.md`
- A crash mid-write leaves a line without a newline. `_read_events` stops at it, and `_truncate_torn_tail` removes it on open.
- `fsync` only for `DURABLE_TYPES` (`tool_call_started`) or `durable=True`. Write-ahead intent must be on disk before the side effect. Other events are flushed but may be lost on power failure, which only ever loses the newest events, never corrupts older ones.
- Tradeoff: one fsync per tool call (milliseconds) vs. per event.

`0004-redaction-at-write-time.md`
- Options: redact at export, redact at write, never store raw tool output.
- Chose write time: every export, trace and Flagship 2 copy derives from the log, so a secret on disk is a secret everywhere.
- Costs: irreversible, regex false positives and negatives, patterns need upkeep. Explicit secret values from env names cover what regex can't.

`0005-item-model.md`
- Items, not messages: reasoning and tool calls are top-level items in OpenAI Responses and Codex, and Anthropic content blocks map onto items without loss.
- `Reasoning.payload` is stored verbatim and `replayable_to` restricts replay to the same provider and model family, because signatures and encrypted content can't be translated.
- `ToolCall` carries our `id` plus `provider_call_id` (and optional `provider_item_id`), since OpenAI uses two IDs and Anthropic one.

`0006-exec-semantics.md`
- Fresh `bash -c` per call, `start_new_session=True`, stdin `/dev/null`, stderr merged.
- The whole process group is killed on timeout, on cancel and after normal exit. Background processes would otherwise hold the pipe open and escape cancellation.
- Head plus tail capping: commands put context at the start and errors at the end. The truncation marker tells the model what was dropped.
- Shell state (`cd`, `export`) does not persist, by design: it would live outside the log and vanish on resume. The working directory becomes logged agent state in M1/M2.

`0007-cooperative-cancellation.md`
- One `CancelToken` passed down to providers and environments, instead of relying only on `asyncio` task cancellation.
- Providers check it between stream events and end with `stop_reason="aborted"` while keeping finished items. `exec` races `proc.wait()` against `cancel.wait()` and kills the group.
- Why not only `Task.cancel()`: cancellation must leave a consistent log and partial results, which needs explicit check points.

`0008-synchronous-store-io.md`
- The store does blocking file writes inside an async program.
- Appends are small and fsync is rare. The loop awaits the append before running the tool anyway, because write-ahead requires it.
- Revisit with `asyncio.to_thread` if append latency shows up in traces.

- [ ] **Step 3: Write the walkthrough**

`docs/walkthroughs/m0.md` contains:
1. A one-paragraph map of the packages and the dependency rule (with how `tests/test_boundaries.py` enforces it).
2. One section per source file: what it's for, its public names, and the invariant it guarantees (for example, `JsonlSessionLog.append`: seq is strictly increasing, redaction happens before the write, `tool_call_started` is fsynced).
3. The life of one event: from `make_event` through redaction, `model_dump_json`, the file, `_read_events`, back to an `Event` equal to the original.
4. The life of one `exec` call: spawn, pump, race, kill group, drain, result.

- [ ] **Step 4: Write the cross-questions**

Append to `docs/walkthroughs/m0.md` a section "Cross-questions" with each question below and a 2 to 4 sentence answer written from the code:
1. Why event sourcing instead of a plain message list?
2. Why JSONL and not SQLite?
3. What happens if the process dies halfway through writing a line?
4. Why fsync only `tool_call_started`, and what can you lose?
5. How do you change the event schema without breaking old sessions?
6. Why items rather than messages?
7. Why is reasoning stored opaque, and what happens when you switch models?
8. Why redact at write time and not at export? What do you lose?
9. Why a fresh bash process per call instead of a persistent shell?
10. Why kill the process group even after the command exits normally?
11. Why keep the head and tail of long output instead of just the tail?
12. How does cancellation reach a running subprocess?
13. Why is the store synchronous inside an async program?
14. How do you guarantee a single writer per session, and what changes when Flagship 2 runs many workers?

- [ ] **Step 5: Verify docs**

Run: `grep -rnP "\x{2014}" docs/decisions docs/walkthroughs || echo "no em dashes"`
Expected: `no em dashes`

- [ ] **Step 6: Commit**

```bash
git add docs/decisions docs/walkthroughs
git commit -m "docs: M0 decision records, walkthrough and cross-questions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### M0 gate (decided by the user, not an agent)

- [ ] **Acceptance check:** `uv run pytest -q && uv run ruff check && uv run pyright` all pass on a clean checkout. The tests cover a round-trip of every event type (Task 4 and Task 6), fork (Task 7) and redaction (Task 5 and Task 6), which are the spec's M0 acceptance criteria.
- [ ] **Review:** the user reads `docs/walkthroughs/m0.md` and the decision records.
- [ ] **Codename decision:** pick the package name. Rename:

```bash
git mv src/cookcode src/<name>
grep -rl "harness" src tests pyproject.toml README.md | xargs sed -i "s/\bharness\b/<name>/g"
uv sync && uv run pytest -q && uv run ruff check && uv run pyright
git commit -am "chore: rename package to <name>" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Next:** write the M1 plan (loop, steering queue, budgets, interrupts, repair on resume, chaos test).
