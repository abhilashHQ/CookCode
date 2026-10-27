from collections.abc import AsyncGenerator, AsyncIterator, Callable

from cookcode.core.cancel import CancelToken
from cookcode.core.events import Event, EventBody
from cookcode.providers.base import ModelCaps, ModelRequest, Provider, StreamEvent
from cookcode.store.base import SessionLog


class _Observer[T]:
    """Calls a client sink, and turns it off after its first failure so it cannot break the run."""

    def __init__(self, sink: Callable[[T], None]) -> None:
        self._sink: Callable[[T], None] | None = sink
        self.error: Exception | None = None

    def __call__(self, value: T) -> None:
        if self._sink is None:
            return
        try:
            self._sink(value)
        except Exception as exc:
            self._sink, self.error = None, exc


class DisplayProvider:
    """Wraps a provider so a client sees stream events while the loop consumes them."""

    def __init__(self, inner: Provider, sink: Callable[[StreamEvent], None]) -> None:
        self._inner = inner
        self._sink = _Observer(sink)
        self.name = inner.name

    def capabilities(self, model: str) -> ModelCaps:
        return self._inner.capabilities(model)

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        inner = self._inner.stream(request, cancel)
        try:
            async for event in inner:
                self._sink(event)
                yield event
        finally:
            if isinstance(inner, AsyncGenerator):
                await inner.aclose()


class ObservedLog:
    """Wraps a session log so a client sees every event the core appends."""

    def __init__(self, inner: SessionLog, sink: Callable[[Event], None]) -> None:
        self._inner = inner
        self._sink = _Observer(sink)

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    @property
    def next_seq(self) -> int:
        return self._inner.next_seq

    def append(self, body: EventBody, *, durable: bool = False) -> Event:
        event = self._inner.append(body, durable=durable)
        self._sink(event)
        return event

    def read(self, from_seq: int = 0) -> list[Event]:
        return self._inner.read(from_seq)

    def close(self) -> None:
        self._inner.close()
