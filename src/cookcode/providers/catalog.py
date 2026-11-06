"""Provider presets: where each endpoint lives, which key it needs and what to assume about it.

Local presets need no key, start from a deliberately small context window and wait longer for
the first byte, because prefill on a small GPU is slow. Discovery (`discovery.py`) asks the
server for the window it actually loaded."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cookcode.providers.gemini import GEMINI_BASE_URL
from cookcode.providers.openai_compat import NIM_BASE_URL, OPENROUTER_BASE_URL

WindowProbe = Literal[
    "none", "openrouter_models", "llamacpp_props", "lmstudio_models", "vllm_models"
]

LOCAL_WINDOW = 4096
LOCAL_READ_TIMEOUT_S = 600.0

_LOCAL_URL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:\d+)?(/|$)")


class Preset(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    protocol: Literal["openai", "gemini"]
    base_url: str
    key_env: str | None
    default_model: str | None
    context_window: int
    window_probe: WindowProbe
    local: bool
    read_timeout_s: float = 180.0
    note: str | None = None


def _local(name: str, port: int, probe: WindowProbe, note: str | None = None) -> Preset:
    return Preset(
        name=name,
        protocol="openai",
        base_url=f"http://localhost:{port}/v1",
        key_env=None,
        default_model=None,
        context_window=LOCAL_WINDOW,
        window_probe=probe,
        local=True,
        read_timeout_s=LOCAL_READ_TIMEOUT_S,
        note=note,
    )


PRESETS: dict[str, Preset] = {
    preset.name: preset
    for preset in (
        Preset(
            name="nim",
            protocol="openai",
            base_url=NIM_BASE_URL,
            key_env="NVIDIA_API_KEY",
            default_model="minimaxai/minimax-m3.1-flash",
            context_window=128_000,
            window_probe="none",
            local=False,
        ),
        Preset(
            name="openrouter",
            protocol="openai",
            base_url=OPENROUTER_BASE_URL,
            key_env="OPENROUTER_API_KEY",
            default_model="nvidia/nemotron-3.5-super-vl:free",
            context_window=128_000,
            window_probe="openrouter_models",
            local=False,
        ),
        Preset(
            name="gemini",
            protocol="gemini",
            base_url=GEMINI_BASE_URL,
            key_env="GEMINI_API_KEY",
            default_model="gemini-3.5-flash",
            context_window=1_000_000,
            window_probe="none",
            local=False,
        ),
        _local(
            "ollama",
            11434,
            "none",
            "Ollama loads 4096 tokens of context unless told otherwise, and cuts longer prompts "
            "without an error. Start it with OLLAMA_CONTEXT_LENGTH=32768 ollama serve and pass "
            "--context-window 32768.",
        ),
        _local(
            "llamacpp",
            8080,
            "llamacpp_props",
            "Start llama-server with --jinja so tool calls work, and -c to set the context size.",
        ),
        _local("lmstudio", 1234, "lmstudio_models"),
        _local(
            "vllm",
            8000,
            "vllm_models",
            "Start vllm serve with --enable-auto-tool-choice and --tool-call-parser so tool "
            "calls work.",
        ),
    )
}

CLOUD_PRESETS = tuple(name for name, preset in PRESETS.items() if not preset.local)


def is_local_url(url: str) -> bool:
    return _LOCAL_URL.match(url) is not None


def needs_key(preset: Preset, base_url: str) -> bool:
    return preset.key_env is not None and not is_local_url(base_url)
