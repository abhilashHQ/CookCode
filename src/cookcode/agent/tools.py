import posixpath
from collections.abc import Awaitable, Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from cookcode.core.cancel import CancelToken
from cookcode.core.events import Event, ItemEvent, SideEffect
from cookcode.core.items import ToolCall, ToolResult
from cookcode.env.base import Environment
from cookcode.providers.base import ToolSchema

TOOL_SEARCH = "tool_search"


def loaded_tools(events: Sequence[Event]) -> frozenset[str]:
    """Deferred tools whose schemas the model loaded: the names passed to tool_search calls
    that succeeded. A pure function of the log, so replay sends the same tool list."""
    asked: dict[str, list[str]] = {}
    loaded: set[str] = set()
    for event in events:
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, ToolCall):
            names = body.item.arguments.get("names")
            if body.item.name == TOOL_SEARCH and isinstance(names, list):
                asked[body.item.id] = [str(name) for name in names]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            if body.item.call_id in asked and body.item.status == "ok":
                loaded.update(asked[body.item.call_id])
    return frozenset(loaded)


def _absolute(path: str) -> str:
    return "/" + posixpath.normpath(path).lstrip("/")


def workspace_path(root: str, cwd: str, path: str) -> str:
    resolved = _absolute(posixpath.join(root, cwd, path))
    base = _absolute(root)
    if resolved == base:
        return "."
    prefix = base.rstrip("/") + "/"
    return resolved.removeprefix(prefix) if resolved.startswith(prefix) else resolved


@dataclass(frozen=True)
class ToolContext:
    env: Environment
    cancel: CancelToken
    cwd: str
    timeout_s: float
    max_output_bytes: int
    history: Callable[[], Sequence[Event]] | None = None


class ToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    status: Literal["ok", "error"] = "ok"
    exit_status: Literal["ok", "error", "timeout", "killed"] = "ok"
    output_bytes: int | None = None
    truncated: bool = False
    cwd: str | None = None

    @property
    def size(self) -> int:
        if self.output_bytes is not None:
            return self.output_bytes
        return len(self.content.encode())


@dataclass(frozen=True)
class Tool[A: BaseModel]:
    name: str
    description: str
    args_model: type[A]
    side_effect: SideEffect
    handler: Callable[[A, ToolContext], Awaitable[ToolOutput]]
    touches: Callable[[A], list[str]] | None = None
    deferred: bool = False

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.args_model.model_json_schema(),
        )


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool[Any]]) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def schemas(self, loaded: Collection[str] = ()) -> tuple[ToolSchema, ...]:
        """Every tool's schema, except deferred tools that have not been loaded."""
        return tuple(
            tool.schema()
            for tool in self._tools.values()
            if not tool.deferred or tool.name in loaded
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def __iter__(self) -> Iterator[Tool[Any]]:
        return iter(self._tools.values())
