from pathlib import Path

import pytest
from pydantic import BaseModel

from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.cancel import CancelToken
from harness.env.local import LocalEnvironment


class ReadArgs(BaseModel):
    path: str
    limit: int = 10


class EchoArgs(BaseModel):
    text: str


async def _read(args: ReadArgs, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(content=args.path)


async def _echo(args: EchoArgs, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(content=args.text)


def _read_tool(name: str = "read") -> Tool[ReadArgs]:
    return Tool(
        name=name,
        description="Read a file.",
        args_model=ReadArgs,
        side_effect="read_only",
        handler=_read,
    )


def _echo_tool(name: str = "echo") -> Tool[EchoArgs]:
    return Tool(
        name=name,
        description="Echo text.",
        args_model=EchoArgs,
        side_effect="mutating",
        handler=_echo,
    )


def test_schema_carries_name_description_and_parameters() -> None:
    schema = _read_tool().schema()
    assert schema.name == "read"
    assert schema.description == "Read a file."
    assert set(schema.parameters["properties"]) == {"path", "limit"}
    assert schema.parameters["required"] == ["path"]


def test_registry_lookup_and_unknown_name() -> None:
    read, echo = _read_tool(), _echo_tool()
    registry = ToolRegistry([read, echo])
    assert registry.get("read") is read
    assert registry.get("echo") is echo
    assert registry.get("missing") is None


def test_registry_keeps_registration_order() -> None:
    registry = ToolRegistry([_echo_tool("zeta"), _read_tool("alpha"), _echo_tool("mid")])
    assert registry.names() == ("zeta", "alpha", "mid")
    assert tuple(schema.name for schema in registry.schemas()) == ("zeta", "alpha", "mid")


def test_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="read"):
        ToolRegistry([_read_tool("read"), _echo_tool("read")])


def test_output_size_is_utf8_byte_length() -> None:
    assert ToolOutput(content="héllo").size == 6


def test_explicit_output_bytes_wins() -> None:
    assert ToolOutput(content="héllo", output_bytes=500).size == 500


def test_output_defaults() -> None:
    output = ToolOutput(content="x")
    assert output.status == "ok"
    assert output.exit_status == "ok"
    assert output.truncated is False


async def test_handler_runs_through_the_tool(tmp_path: Path) -> None:
    ctx = ToolContext(
        env=LocalEnvironment(tmp_path),
        cancel=CancelToken(),
        cwd=".",
        timeout_s=5.0,
        max_output_bytes=1000,
    )
    tool = _echo_tool()
    result = await tool.handler(EchoArgs(text="hi"), ctx)
    assert result.content == "hi"
