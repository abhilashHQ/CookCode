"""Asks a server, once at startup, which models it serves and how much context it loaded.

Only the effective window is read: llama.cpp's `n_ctx`, LM Studio's `loaded_context_length`,
vLLM's `max_model_len` and OpenRouter's `context_length`. The trained maximum each local server
also reports (`n_ctx_train`, `max_context_length`) can be 128 times larger than what was loaded,
and sizing the context by it overflows. Discovery never raises: whatever it cannot learn comes
back as `problem`, and the caller falls back to configuration."""

import json

import httpx
from pydantic import BaseModel, ConfigDict

from cookcode.providers.catalog import Preset
from cookcode.providers.http import JSON, as_array, as_object


class Discovery(BaseModel):
    model_config = ConfigDict(frozen=True)

    models: tuple[str, ...] = ()
    context_window: int | None = None
    problem: str | None = None


class _Unavailable(Exception):
    pass


async def _get(client: httpx.AsyncClient, url: str) -> JSON:
    try:
        response = await client.get(url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise _Unavailable(f"cannot reach {url}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise _Unavailable(f"{url} answered HTTP {response.status_code}")
    try:
        body = as_object(response.json())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _Unavailable(f"{url} did not answer with JSON") from exc
    if body is None:
        raise _Unavailable(f"{url} did not answer with a JSON object")
    return body


def _by_id(listing: JSON) -> dict[str, JSON]:
    entries: dict[str, JSON] = {}
    for value in as_array(listing.get("data")):
        entry = as_object(value)
        if entry is not None and isinstance(entry.get("id"), str):
            entries[entry["id"]] = entry
    return entries


def _size(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


async def _window(
    preset: Preset, client: httpx.AsyncClient, root: str, listed: dict[str, JSON], model: str
) -> int | None:
    match preset.window_probe:
        case "openrouter_models":
            return _size(listed.get(model, {}).get("context_length"))
        case "vllm_models":
            return _size(listed.get(model, {}).get("max_model_len"))
        case "lmstudio_models":
            loaded = _by_id(await _get(client, f"{root}/api/v0/models"))
            return _size(loaded.get(model, {}).get("loaded_context_length"))
        case "llamacpp_props":
            props = await _get(client, f"{root}/props")
            settings = as_object(props.get("default_generation_settings")) or {}
            return _size(settings.get("n_ctx"))
        case "none":
            return None


async def discover(
    preset: Preset,
    *,
    base_url: str,
    api_key: str | None,
    model: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = 3.0,
) -> Discovery:
    base = base_url.rstrip("/")
    root = base.removesuffix("/v1")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(transport=transport, timeout=timeout_s, headers=headers) as client:
        try:
            listed = _by_id(await _get(client, f"{base}/models"))
        except _Unavailable as exc:
            return Discovery(problem=str(exc))
        models = tuple(listed)
        if preset.window_probe == "none":
            return Discovery(models=models)
        target = model or (models[0] if len(models) == 1 else None)
        if target is None:
            return Discovery(models=models, problem="several models are served; pick one")
        try:
            window = await _window(preset, client, root, listed, target)
        except _Unavailable as exc:
            return Discovery(models=models, problem=str(exc))
    if window is None:
        return Discovery(models=models, problem=f"{base} reports no loaded context for {target}")
    return Discovery(models=models, context_window=window)
