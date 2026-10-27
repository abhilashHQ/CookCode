"""Settings files: the user's in the data directory and the repository's in `.harness/`.

    provider = "nim"
    model = "minimaxai/minimax-m3.1-flash"
    mode = "auto-edit"
    max_turns = 40
    strategy = "mask_batched:k6:b8"
    features = "jobs,verify_first"
    allow = ["bash:pytest *"]
    deny = ["bash:rm -rf *"]

    [[hooks]]
    point = "pre_tool"
    command = "python3 scripts/guard.py"

    [mcp.servers.docs]
    command = "npx"
    args = ["-y", "some-mcp-server"]

A flag beats a resumed session's config, which beats the environment (HARNESS_PROVIDER and
friends), which beats the repository's settings, which beat the user's, which beat the
built-in defaults. Lists of rules and hooks add up across files; MCP servers merge by name,
the repository's winning. Hooks and MCP servers run commands, so they are inert unless the
hooks and mcp features are on."""

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REPO_SETTINGS = ".harness/settings.toml"


class HookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    point: Literal["pre_tool", "stop"]
    command: str
    match: str | None = None
    timeout_s: float = Field(default=30, gt=0, le=600)

    @field_validator("match")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"match is not a valid regex: {exc}") from exc
        return value


class McpServer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}


class Mcp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: dict[str, McpServer] = {}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    mode: Literal["ask", "auto-edit", "yolo"] | None = None
    max_turns: int | None = Field(default=None, ge=1)
    strategy: str | None = None
    features: str | None = None
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    mcp: Mcp = Mcp()

    def over(self, base: "Settings") -> "Settings":
        """These settings on top of `base`."""
        scalars = {
            key: getattr(self, key) if getattr(self, key) is not None else getattr(base, key)
            for key in ("provider", "model", "base_url", "api_key_env", "mode", "max_turns",
                        "strategy", "features")
        }
        return Settings(
            **scalars,
            allow=(*base.allow, *self.allow),
            deny=(*base.deny, *self.deny),
            hooks=(*base.hooks, *self.hooks),
            mcp=Mcp(servers={**base.mcp.servers, **self.mcp.servers}),
        )


def parse_settings(text: str, source: str) -> Settings:
    try:
        return Settings.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"bad settings in {source}: {exc}") from exc


def read_settings(path: Path) -> Settings:
    return parse_settings(path.read_text(), str(path)) if path.is_file() else Settings()


def load_settings(user_file: Path, repo: Path | None) -> Settings:
    """The user's settings with the repository's on top."""
    user = read_settings(user_file)
    return read_settings(repo / REPO_SETTINGS).over(user) if repo is not None else user
