import threading
import time


class Throttle:
    """Simple sliding-window limiter: at most ``rpm`` calls in any 60 second window."""

    def __init__(self, rpm, clock=time.monotonic, sleep=time.sleep):
        self.rpm = rpm
        self._clock = clock
        self._sleep = sleep
        self._calls = []
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = self._clock()
            self._calls = [t for t in self._calls if now - t < 60]
            if len(self._calls) >= self.rpm:
                self._sleep(60 - (now - self._calls[0]))
                now = self._clock()
                self._calls = [t for t in self._calls if now - t < 60]
            self._calls.append(now)
