from collections.abc import Callable
from typing import Any

import httpx

from cookcode.providers.catalog import PRESETS
from cookcode.providers.discovery import Discovery, discover

type Routes = dict[str, Any]


class Server:
    """A MockTransport handler serving JSON by URL and recording what was asked."""

    def __init__(self, routes: Routes, fail: Callable[[httpx.Request], None] | None = None) -> None:
        self._routes = routes
        self._fail = fail
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._fail is not None:
            self._fail(request)
        route = self._routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


async def ask(
    name: str, server: Server, *, model: str | None = None, api_key: str | None = None,
    base_url: str | None = None,
) -> Discovery:
    preset = PRESETS[name]
    return await discover(
        preset, base_url=base_url or preset.base_url, api_key=api_key, model=model,
        transport=server.transport,
    )


LLAMACPP_MODELS = {
    "object": "list",
    "data": [{"id": "m", "object": "model", "meta": {"n_ctx_train": 131072, "n_vocab": 128256}}],
}


async def test_llamacpp_reads_n_ctx_not_n_ctx_train() -> None:
    server = Server({
        "http://localhost:8080/v1/models": LLAMACPP_MODELS,
        "http://localhost:8080/props": {
            "default_generation_settings": {"n_ctx": 4096}, "total_slots": 1
        },
    })
    found = await ask("llamacpp", server)
    assert found == Discovery(models=("m",), context_window=4096)
    assert server.urls == ["http://localhost:8080/v1/models", "http://localhost:8080/props"]


async def test_llamacpp_without_n_ctx_reports_a_problem() -> None:
    server = Server({
        "http://localhost:8080/v1/models": LLAMACPP_MODELS,
        "http://localhost:8080/props": {"total_slots": 1},
    })
    found = await ask("llamacpp", server)
    assert found.models == ("m",) and found.context_window is None
    assert found.problem is not None


async def test_lmstudio_reads_loaded_context_length() -> None:
    server = Server({
        "http://localhost:1234/v1/models": {"data": [{"id": "a"}, {"id": "b"}]},
        "http://localhost:1234/api/v0/models": {"data": [
            {"id": "a", "state": "loaded", "max_context_length": 131072,
             "loaded_context_length": 8192},
            {"id": "b", "state": "not-loaded", "max_context_length": 32768},
        ]},
    })
    found = await ask("lmstudio", server, model="a")
    assert (found.models, found.context_window) == (("a", "b"), 8192)
    unloaded = await ask("lmstudio", server, model="b")
    assert unloaded.context_window is None and unloaded.problem is not None


async def test_openrouter_reads_context_length_for_the_requested_model() -> None:
    models = {"data": [
        {"id": "x/one", "context_length": 32768},
        {"id": "x/two", "context_length": 262144},
    ]}
    server = Server({"https://openrouter.ai/api/v1/models": models})
    found = await ask("openrouter", server, model="x/two", api_key="k")
    assert (found.models, found.context_window) == (("x/one", "x/two"), 262144)
    assert server.urls == ["https://openrouter.ai/api/v1/models"]


async def test_vllm_reads_max_model_len() -> None:
    server = Server({"http://localhost:8000/v1/models": {"data": [
        {"id": "q", "max_model_len": 16384}
    ]}})
    found = await ask("vllm", server)
    assert found == Discovery(models=("q",), context_window=16384)


async def test_ollama_lists_models_and_reports_no_window() -> None:
    server = Server({"http://localhost:11434/v1/models": {"data": [
        {"id": "qwen2.5-coder:7b"}, {"id": "llama3.2"}
    ]}})
    found = await ask("ollama", server)
    assert found.models == ("qwen2.5-coder:7b", "llama3.2")
    assert found.context_window is None
    assert len(server.requests) == 1


async def test_trained_maximum_is_never_used() -> None:
    server = Server({
        "http://localhost:8080/v1/models": LLAMACPP_MODELS,
        "http://localhost:8080/props": {"default_generation_settings": {}},
        "http://localhost:1234/v1/models": {"data": [{"id": "a"}]},
        "http://localhost:1234/api/v0/models": {"data": [
            {"id": "a", "max_context_length": 131072, "context_length": 131072}
        ]},
    })
    assert (await ask("llamacpp", server)).context_window is None
    assert (await ask("lmstudio", server)).context_window is None


async def test_window_needs_a_model_when_several_are_served() -> None:
    server = Server({"http://localhost:8000/v1/models": {"data": [
        {"id": "a", "max_model_len": 1000}, {"id": "b", "max_model_len": 2000}
    ]}})
    found = await ask("vllm", server)
    assert found.models == ("a", "b") and found.context_window is None


async def test_server_down_is_a_problem_not_an_error() -> None:
    def refuse(request: httpx.Request) -> None:
        raise httpx.ConnectError("connection refused", request=request)

    found = await ask("ollama", Server({}, fail=refuse))
    assert found.models == () and found.context_window is None
    assert found.problem is not None and "localhost:11434" in found.problem


async def test_http_error_and_bad_json_are_problems() -> None:
    denied = Server({"https://openrouter.ai/api/v1/models": httpx.Response(401, text="no")})
    found = await ask("openrouter", denied, model="x", api_key="k")
    assert found.models == () and found.problem is not None and "401" in found.problem
    garbled = Server({"http://localhost:8000/v1/models": httpx.Response(200, text="<html>")})
    found = await ask("vllm", garbled)
    assert found.models == () and found.problem is not None


async def test_custom_base_url_is_followed() -> None:
    server = Server({
        "http://127.0.0.1:9000/v1/models": LLAMACPP_MODELS,
        "http://127.0.0.1:9000/props": {"default_generation_settings": {"n_ctx": 2048}},
    })
    found = await ask("llamacpp", server, base_url="http://127.0.0.1:9000/v1/")
    assert found.context_window == 2048


async def test_no_auth_header_without_a_key() -> None:
    server = Server({"http://localhost:11434/v1/models": {"data": []}})
    await ask("ollama", server)
    assert "authorization" not in server.requests[0].headers


async def test_auth_header_with_a_key() -> None:
    server = Server({"https://openrouter.ai/api/v1/models": {"data": []}})
    await ask("openrouter", server, model="x", api_key="secret")
    assert server.requests[0].headers["authorization"] == "Bearer secret"


async def test_malformed_base_url_is_a_problem() -> None:
    found = await ask("llamacpp", Server({}), base_url="::::")
    assert found.models == () and found.problem is not None
