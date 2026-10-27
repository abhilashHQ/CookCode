import functools
import time

from .errors import RetryError, TransientError


def retry(attempts=3, retry_on=(TransientError,), backoff=0.5, sleep=time.sleep):
    """Retry the wrapped call with exponential backoff.

    Only exceptions listed in ``retry_on`` are worth another attempt.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    last = exc
                    if attempt < attempts:
                        sleep(backoff * 2 ** (attempt - 1))
            raise RetryError(attempts, last)

        return wrapper

    return decorator
