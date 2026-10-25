import asyncio

from harness.core.cancel import CancelToken


def test_starts_uncancelled() -> None:
    token = CancelToken()
    assert not token.cancelled
    assert token.reason is None


def test_first_reason_wins() -> None:
    token = CancelToken()
    token.cancel("user")
    token.cancel("timeout")
    assert token.cancelled
    assert token.reason == "user"


async def test_wait_returns_after_cancel_from_another_task() -> None:
    token = CancelToken()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.01)
        token.cancel("user")

    task = asyncio.create_task(cancel_soon())
    await asyncio.wait_for(token.wait(), timeout=1)
    await task
    assert token.cancelled
