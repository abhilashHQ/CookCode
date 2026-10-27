import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import httpx

from cookcode.redact import JSONValue, Redactor

CASSETTE_VERSION = 1
KEPT_HEADERS = ("content-type", "retry-after")
DIFF_CONTEXT = 40

type JSON = dict[str, Any]


class CassetteMismatch(AssertionError):
    pass


def _request_json(request: httpx.Request) -> JSON:
    return cast(JSON, json.loads(request.content or b"{}"))


def first_difference(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        left, right = cast(JSON, expected), cast(JSON, actual)
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                side = "missing" if key not in right else "unexpected"
                return f"{path}.{key}: {side}"
            found = first_difference(left[key], right[key], f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(expected, list):
        left_items, right_items = cast(list[Any], expected), cast(list[Any], actual)
        for index, (left, right) in enumerate(zip(left_items, right_items, strict=False)):
            found = first_difference(left, right, f"{path}[{index}]")
            if found is not None:
                return found
        if len(left_items) != len(right_items):
            return f"{path}: expected {len(left_items)} entries, got {len(right_items)}"
        return None
    if isinstance(expected, str) and isinstance(actual, str) and expected != actual:
        at = next(
            (i for i, (a, b) in enumerate(zip(expected, actual, strict=False)) if a != b),
            min(len(expected), len(actual)),
        )
        if at > DIFF_CONTEXT:
            start = at - DIFF_CONTEXT
            return (
                f"{path} at char {at}: expected ...{expected[start : at + 80]!r}, "
                f"got ...{actual[start : at + 80]!r}"
            )
    if expected != actual:
        return f"{path}: expected {expected!r:.200}, got {actual!r:.200}"
    return None


class Cassette:
    """Recorded HTTP exchanges: request bodies and raw response bytes, in order."""

    def __init__(self, interactions: list[JSON] | None = None) -> None:
        self.interactions: list[JSON] = interactions or []

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        data = json.loads(path.read_text())
        if data.get("version") != CASSETTE_VERSION:
            raise ValueError(f"{path}: unsupported cassette version {data.get('version')!r}")
        return cls(data["interactions"])

    def save(self, path: Path, redactor: Redactor | None = None) -> None:
        redactor = redactor or Redactor()
        data = cast(JSONValue, {"version": CASSETTE_VERSION, "interactions": self.interactions})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(redactor.redact(data), indent=1, ensure_ascii=False) + "\n")


class _TeeStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, on_close: Callable[[bytes], None]) -> None:
        self._inner = inner
        self._on_close = on_close
        self._chunks: list[bytes] = []
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._chunks.append(chunk)
            yield chunk

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._inner.aclose()
        finally:
            self._on_close(b"".join(self._chunks))


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, cassette: Cassette, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._cassette = cassette
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        interaction: JSON = {
            "request": {
                "method": request.method,
                "url": str(request.url),
                "json": _request_json(request),
            },
            "response": {
                "status": response.status_code,
                "headers": {
                    name: response.headers[name]
                    for name in KEPT_HEADERS
                    if name in response.headers
                },
                "body": "",
            },
        }
        self._cassette.interactions.append(interaction)

        def finished(body: bytes) -> None:
            interaction["response"]["body"] = body.decode("utf-8", "replace")

        stream = cast(httpx.AsyncByteStream, response.stream)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=_TeeStream(stream, finished),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    """Serves recorded responses in order. Strict replay fails on any request that differs from
    the tape; sequenced replay (strict=False) serves the response anyway and notes the drift."""

    def __init__(self, cassette: Cassette, *, strict: bool = True) -> None:
        self._cassette = cassette
        self._strict = strict
        self.position = 0
        self.drift: list[str] = []

    def _differs(self, message: str) -> None:
        if self._strict:
            raise CassetteMismatch(message)
        self.drift.append(message)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.position >= len(self._cassette.interactions):
            raise CassetteMismatch(
                f"request #{self.position + 1} was not recorded: {request.method} {request.url}"
            )
        recorded = self._cassette.interactions[self.position]
        self.position += 1
        expected = recorded["request"]
        if expected["method"] != request.method or expected["url"] != str(request.url):
            self._differs(
                f"request #{self.position}: expected {expected['method']} {expected['url']}, "
                f"got {request.method} {request.url}"
            )
        difference = first_difference(expected["json"], _request_json(request))
        if difference is not None:
            self._differs(f"request #{self.position} body differs at {difference}")
        response = recorded["response"]
        return httpx.Response(
            response["status"],
            headers=response["headers"],
            content=response["body"].encode(),
            request=request,
        )

    def assert_exhausted(self) -> None:
        left = len(self._cassette.interactions) - self.position
        if left:
            raise CassetteMismatch(f"{left} recorded request(s) were never made")
