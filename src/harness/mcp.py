"""MCP client: tools from stdio MCP servers, with their schemas loaded on demand.

Every server's tools are registered as deferred tools named `mcp__<server>__<tool>`. At session
start the system prompt lists their names and one-line descriptions only; the model loads the
schemas it needs with `tool_search(names=[...])`, and they are sent with every request from the
next step on. Which schemas were loaded is read from the log (agent.tools.loaded_tools), so
replay rebuilds the same tool list. MCP tools are `external`: in ask mode each call is asked.

The SDK's connection context managers must be entered and exited in one task, so each server
is owned by a task that connects, reports its tools and then waits to be told to close."""

import asyncio
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field

from harness.agent.tools import TOOL_SEARCH, Tool, ToolContext, ToolOutput
from harness.config import McpServer

if TYPE_CHECKING:
    from mcp import ClientSession, types

PREFIX = "mcp"
CONNECT_TIMEOUT_S = 30.0
DESCRIPTION_CHARS = 160

SEARCH_DESCRIPTION = (
    "Load the schemas of deferred tools (MCP server tools) listed in the system prompt, so you "
    "can call them from your next step on. Pass their exact names."
)


def tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{PREFIX}__{server}__{tool}")[:64]


def _text(result: "types.CallToolResult") -> tuple[str, bool]:
    parts = [item.text if item.type == "text" else f"[{item.type} content]"
             for item in result.content]
    return "\n".join(parts), bool(result.is_error)


def _cause(exc: BaseException) -> str:
    """The innermost error, so a failure inside the SDK's task groups names what went wrong."""
    current: BaseException = exc
    while isinstance(current, BaseExceptionGroup):
        group = cast(BaseExceptionGroup[BaseException], current)
        current = group.exceptions[0]
    return f"{type(current).__name__}: {current}"


@dataclass
class _Server:
    name: str
    session: "ClientSession"
    tools: list["types.Tool"]
    stop: asyncio.Event
    owner: asyncio.Task[None]


@dataclass
class McpHub:
    servers: dict[str, _Server] = field(default_factory=lambda: {})
    errors: dict[str, str] = field(default_factory=lambda: {})

    async def start(self, configured: dict[str, McpServer], cwd: str) -> None:
        for name, spec in configured.items():
            try:
                self.servers[name] = await asyncio.wait_for(self._connect(name, spec, cwd),
                                                            CONNECT_TIMEOUT_S)
            except Exception as exc:
                self.errors[name] = _cause(exc)

    async def _connect(self, name: str, spec: McpServer, cwd: str) -> _Server:
        from mcp import ClientSession, types
        from mcp.client.stdio import StdioServerParameters, stdio_client

        loop = asyncio.get_running_loop()
        ready: asyncio.Future[tuple[ClientSession, list[types.Tool]]] = loop.create_future()
        stop = asyncio.Event()
        params = StdioServerParameters(command=spec.command, args=list(spec.args),
                                       env={**os.environ, **spec.env}, cwd=cwd)

        async def own() -> None:
            try:
                with open(os.devnull, "w") as quiet:
                    async with (
                        stdio_client(params, errlog=quiet) as (read, write),
                        ClientSession(read, write) as session,
                    ):
                        await session.initialize()
                        tools: list[types.Tool] = []
                        cursor: str | None = None
                        while True:
                            page = await session.list_tools(
                                params=None if cursor is None
                                else types.PaginatedRequestParams(cursor=cursor))
                            tools += page.tools
                            cursor = page.next_cursor
                            if not cursor:
                                break
                        ready.set_result((session, tools))
                        await stop.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc if isinstance(exc, Exception) else RuntimeError(exc))
                if not isinstance(exc, Exception):
                    raise

        owner = asyncio.create_task(own(), name=f"mcp:{name}")
        try:
            session, tools = await ready
        except BaseException:
            owner.cancel()
            raise
        return _Server(name, session, tools, stop, owner)

    async def call(self, server: str, tool: str, arguments: dict[str, Any],
                   timeout_s: float) -> tuple[str, bool]:
        result = await self.servers[server].session.call_tool(
            tool, arguments, read_timeout_seconds=timeout_s)
        return _text(result)

    async def aclose(self) -> None:
        for server in self.servers.values():
            server.stop.set()
        owners = [server.owner for server in self.servers.values()]
        if owners:
            await asyncio.wait(owners, timeout=10)
            for owner in owners:
                owner.cancel()
            await asyncio.gather(*owners, return_exceptions=True)
        self.servers.clear()

    def tools(self) -> list[Tool[Any]]:
        built: list[Tool[Any]] = []
        for server in self.servers.values():
            for spec in server.tools:
                built.append(self._tool(server.name, spec))
        return built

    def _tool(self, server: str, spec: "types.Tool") -> Tool[Any]:
        schema: dict[str, Any] = dict(spec.input_schema or {"type": "object"})
        args_model = _passthrough(schema)

        async def run(args: BaseModel, ctx: ToolContext) -> ToolOutput:
            try:
                text, is_error = await self.call(server, spec.name, args.model_dump(),
                                                 ctx.timeout_s)
            except Exception as exc:
                return ToolOutput(content=f"MCP call failed: {type(exc).__name__}: {exc}",
                                  status="error", exit_status="error")
            status = "error" if is_error else "ok"
            return ToolOutput(content=text, status=status, exit_status=status)

        return Tool(name=tool_name(server, spec.name), description=spec.description or "",
                    args_model=args_model, side_effect="external", handler=run, deferred=True)

    def profile(self) -> str:
        lines = [
            f"- {tool.name}: {' '.join(tool.description.split())[:DESCRIPTION_CHARS]}"
            for tool in self.tools()
        ]
        broken = [f"- {name}: {error}" for name, error in self.errors.items()]
        sections: list[str] = []
        if lines:
            sections.append("MCP tools: load their schemas with tool_search(names=[...]) before "
                            "calling them.\n" + "\n".join(lines))
        if broken:
            sections.append("MCP servers that failed to start:\n" + "\n".join(broken))
        return "\n\n".join(sections)


def _passthrough(schema: dict[str, Any]) -> type[BaseModel]:
    """An arguments model that accepts any object and reports the server's own schema, so the
    model sees exactly what the server declared and the server does the validating."""

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="allow")

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return schema

    return Arguments


class SearchArgs(BaseModel):
    names: list[str] = Field(description="Exact names of deferred tools to load.", min_length=1)


def tool_search(deferred: Sequence[Tool[Any]]) -> Tool[SearchArgs]:
    known = {tool.name: tool for tool in deferred}

    async def search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
        found = [name for name in args.names if name in known]
        unknown = [name for name in args.names if name not in known]
        note = f" Unknown: {', '.join(unknown)}." if unknown else ""
        if not found:
            return ToolOutput(content=f"no deferred tools loaded.{note}", status="error",
                              exit_status="error")
        return ToolOutput(content=f"loaded {', '.join(found)}; callable from your next step.{note}")

    return Tool(name=TOOL_SEARCH, description=SEARCH_DESCRIPTION, args_model=SearchArgs,
                side_effect="read_only", handler=search)
