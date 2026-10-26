"""Transport policy shared by every streaming HTTP adapter: attempts, retries, cancellation.

A protocol adapter supplies the codec (URL, headers, body, assembler, error parsing). Atomic
responses and logged retries (0022) then hold for every protocol by construction.
"""

import asyncio
import json
import random
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from typing import Any, Protocol, cast

import httpx

from harness.core.cancel import CancelToken
from harness.providers.base import (
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    StreamRetry,
)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, *range(520, 530)})

type JSON = dict[str, Any]


def as_object(value: object) -> JSON | None:
    return cast(JSON, value) if isinstance(value, dict) else None


def as_array(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


class StreamFailure(Exception):
    def __init__(
        self,
        reason: str,
        *,
        retryable: bool,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.status = status
        self.retry_after_s = retry_after_s


class SSEDecoder:
    def __init__(self) -> None:
        self._data: list[str] = []

    def feed(self, line: str) -> str | None:
        if line == "":
            return self.flush()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        if name == "data":
            self._data.append(value.removeprefix(" "))
        return None

    def flush(self) -> str | None:
        if not self._data:
            return None
        data = "\n".join(self._data)
        self._data = []
        return data


def parse_chunk(data: str) -> JSON:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as exc:
        raise StreamFailure(f"malformed stream chunk: {data[:200]!r}", retryable=True) from exc
    parsed = as_object(chunk)
    if parsed is None:
        raise StreamFailure(f"stream chunk is not an object: {data[:200]!r}", retryable=True)
    return parsed


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def server_wait(headers: httpx.Headers, now: float) -> float | None:
    """Seconds the server asks us to wait: Retry-After, or the reset of an exhausted limit."""
    retry_after = _number(headers.get("retry-after"))
    if retry_after is not None:
        return max(retry_after, 0.0)
    reset_ms = _number(headers.get("x-ratelimit-reset"))
    if _number(headers.get("x-ratelimit-remaining")) == 0 and reset_ms is not None:
        return max(reset_ms / 1000 - now, 0.0)
    return None


class Assembler(Protocol):
    def feed(self, chunk: JSON) -> list[StreamEvent]: ...

    def finish(self) -> list[StreamEvent]: ...


class StreamingHttpProvider(ABC):
    """One request per attempt; nothing final is yielded until the response completed."""

    def __init__(
        self,
        *,
        name: str,
        caps: ModelCaps,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
        connect_timeout_s: float = 30.0,
        read_timeout_s: float = 180.0,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.name = name
        self._caps = caps
        self._transport = transport
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._timeout = httpx.Timeout(read_timeout_s, connect=connect_timeout_s)
        self._jitter = jitter
        self._clock = clock

    def capabilities(self, model: str) -> ModelCaps:
        return self._caps

    @abstractmethod
    def build_body(self, request: ModelRequest) -> JSON: ...

    @abstractmethod
    def url(self, model: str) -> str: ...

    @abstractmethod
    def headers(self) -> dict[str, str]: ...

    @abstractmethod
    def assembler(self, model: str) -> Assembler: ...

    @abstractmethod
    def error_failure(self, error: JSON) -> StreamFailure:
        """Classifies a provider error object, from an error body or from inside the stream."""

    def is_terminator(self, data: str) -> bool:
        return False

    async def http_failure(self, response: httpx.Response) -> StreamFailure:
        body = (await response.aread()).decode("utf-8", "replace")
        retry_after = server_wait(response.headers, self._clock())
        try:
            error = as_object((as_object(json.loads(body)) or {}).get("error"))
            if error is not None:
                parsed = self.error_failure(error)
                body = parsed.reason
                if parsed.retry_after_s is not None and retry_after is None:
                    retry_after = parsed.retry_after_s
        except json.JSONDecodeError:
            pass
        status = response.status_code
        return StreamFailure(
            f"HTTP {status}: {body[:500]}",
            retryable=status in RETRYABLE_STATUS,
            status=status,
            retry_after_s=retry_after,
        )

    def _delay(self, attempt: int, failure: StreamFailure) -> float:
        backoff = min(self._backoff_cap_s, self._backoff_base_s * 2 ** (attempt - 1))
        backoff *= 0.5 + self._jitter() / 2
        if failure.retry_after_s is not None:
            backoff = max(backoff, failure.retry_after_s)
        return backoff

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncGenerator[StreamEvent]:
        body = self.build_body(request)
        attempt = 0
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            while True:
                attempt += 1
                if cancel.cancelled:
                    yield StreamEnd(stop_reason="aborted")
                    return
                failure: StreamFailure | None = None
                async with aclosing(self._attempt(client, body, request.model, cancel)) as events:
                    async for event in events:
                        if isinstance(event, StreamFailure):
                            failure = event
                            break
                        yield event
                        if isinstance(event, StreamEnd):
                            return
                assert failure is not None
                wait = failure.retry_after_s
                if failure.retryable and wait is not None and wait > self._backoff_cap_s:
                    yield StreamEnd(
                        stop_reason="error",
                        error=f"{failure.reason} (the server asks to wait {wait:.0f}s, "
                        f"over the {self._backoff_cap_s:.0f}s retry cap)",
                    )
                    return
                if not failure.retryable or attempt > self._max_retries:
                    tries = f" after {attempt} attempts" if attempt > 1 else ""
                    yield StreamEnd(stop_reason="error", error=f"{failure.reason}{tries}")
                    return
                delay = self._delay(attempt, failure)
                yield StreamRetry(
                    attempt=attempt, delay_s=delay, reason=failure.reason, status=failure.status
                )
                try:
                    await asyncio.wait_for(cancel.wait(), timeout=delay)
                except TimeoutError:
                    continue
                yield StreamEnd(stop_reason="aborted")
                return

    async def _attempt(
        self, client: httpx.AsyncClient, body: JSON, model: str, cancel: CancelToken
    ) -> AsyncGenerator[StreamEvent | StreamFailure]:
        queue: asyncio.Queue[StreamEvent | StreamFailure | None] = asyncio.Queue()
        pump = asyncio.create_task(self._pump(client, body, model, queue))
        stopping = asyncio.ensure_future(cancel.wait())
        try:
            while True:
                getting = asyncio.ensure_future(queue.get())
                await asyncio.wait({getting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if not getting.done():
                    getting.cancel()
                    yield StreamEnd(stop_reason="aborted")
                    return
                event = getting.result()
                if event is None:
                    return
                yield event
                if isinstance(event, StreamFailure | StreamEnd):
                    return
        finally:
            stopping.cancel()
            pump.cancel()
            await asyncio.gather(pump, stopping, return_exceptions=True)

    async def _pump(
        self,
        client: httpx.AsyncClient,
        body: JSON,
        model: str,
        queue: asyncio.Queue[StreamEvent | StreamFailure | None],
    ) -> None:
        try:
            async with client.stream(
                "POST", self.url(model), json=body, headers=self.headers()
            ) as response:
                if response.status_code != 200:
                    await queue.put(await self.http_failure(response))
                    return
                assembler = self.assembler(model)
                decoder = SSEDecoder()
                async for line in response.aiter_lines():
                    data = decoder.feed(line)
                    if data is None:
                        continue
                    if self.is_terminator(data):
                        break
                    for event in assembler.feed(parse_chunk(data)):
                        await queue.put(event)
                else:
                    data = decoder.flush()
                    if data is not None and not self.is_terminator(data):
                        for event in assembler.feed(parse_chunk(data)):
                            await queue.put(event)
                for event in assembler.finish():
                    await queue.put(event)
        except StreamFailure as failure:
            await queue.put(failure)
        except httpx.TransportError as exc:
            await queue.put(
                StreamFailure(f"{type(exc).__name__}: {exc or 'transport error'}", retryable=True)
            )
        except Exception as exc:
            await queue.put(StreamFailure(f"{type(exc).__name__}: {exc}", retryable=False))
        finally:
            await queue.put(None)
