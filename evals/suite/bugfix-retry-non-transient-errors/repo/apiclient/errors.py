class ApiError(Exception):
    def __init__(self, status, message=""):
        super().__init__(f"{status} {message}".strip())
        self.status = status
        self.message = message


class TransientError(ApiError):
    """Rate limiting and server-side failures. Safe to retry."""


class NotFound(ApiError):
    pass


class Unauthorized(ApiError):
    pass


class RetryError(Exception):
    def __init__(self, attempts, last):
        super().__init__(f"gave up after {attempts} attempts: {last}")
        self.attempts = attempts
        self.last = last


def error_for_status(status, message=""):
    if status == 404:
        return NotFound(status, message)
    if status in (401, 403):
        return Unauthorized(status, message)
    if status == 429 or status >= 500:
        return TransientError(status, message)
    return ApiError(status, message)
