import asyncio


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str = "user") -> None:
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
