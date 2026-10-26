import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from harness.core.cancel import CancelToken
from harness.providers.base import ModelRequest, StreamEvent
from harness.providers.openai_compat import OpenAICompatProvider

MODEL = "vendor/model-a"
URL = "https://example.test/v1/chat/completions"

type Chunk = dict[str, Any]


def sse(chunks: Sequence[Chunk | str], *, done: bool = True) -> bytes:
    lines = [f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def delta(finish: str | None = None, usage: Chunk | None = None, **fields: Any) -> Chunk:
    chunk: Chunk = {"choices": [{"index": 0, "delta": fields, "finish_reason": finish}]}
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def call_delta(index: int, *, id: str | None = None, name: str | None = None,
               arguments: str | None = None) -> Chunk:
    function: Chunk = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    wire: Chunk = {"index": index, "function": function}
    if id is not None:
        wire["id"] = id
    return delta(tool_calls=[wire])


def text_stream(text: str, usage: Chunk | None = None) -> bytes:
    return sse([delta(content=text), delta("stop", usage=usage or {"prompt_tokens": 10,
                                                                    "completion_tokens": 2})])


def tool_stream(name: str, arguments: str, call_id: str = "call_1") -> bytes:
    return sse([call_delta(0, id=call_id, name=name, arguments=arguments), delta("tool_calls")])


class Script:
    """A MockTransport handler that serves queued responses and records request bodies."""

    def __init__(self, responses: Sequence[httpx.Response | Callable[[], httpx.Response]]) -> None:
        self._responses = list(responses)
        self.bodies: list[Chunk] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        if not self._responses:
            raise AssertionError("unexpected extra request")
        response = self._responses.pop(0)
        return response() if callable(response) else response

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def ok(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def status(
    code: int, message: str = "nope", headers: dict[str, str] | None = None
) -> httpx.Response:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return httpx.Response(code, content=body, headers=headers or {})


class SlowStream(httpx.AsyncByteStream):
    def __init__(self, first: bytes, started: asyncio.Event) -> None:
        self._first = first
        self._started = started
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._first
        self._started.set()
        await asyncio.sleep(30)
        yield b""

    async def aclose(self) -> None:
        self.closed = True


def provider(script: Script, **kwargs: Any) -> OpenAICompatProvider:
    options: dict[str, Any] = {
        "api_key": "test-key",
        "base_url": "https://example.test/v1",
        "transport": script.transport,
        "backoff_base_s": 0.0,
        "jitter": lambda: 1.0,
    }
    options.update(kwargs)
    return OpenAICompatProvider(**options)


async def collect(
    source: OpenAICompatProvider, request: ModelRequest, cancel: CancelToken | None = None
) -> list[StreamEvent]:
    return [event async for event in source.stream(request, cancel or CancelToken())]
