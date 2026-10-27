import time

_MISSING = object()


class TTLCache:
    """In-memory cache whose entries expire ``ttl`` seconds after they are set."""

    def __init__(self, ttl, clock=time.monotonic):
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.ttl = ttl
        self._clock = clock
        self._data = {}

    def set(self, key, value):
        self._data[key] = (self._clock() + self.ttl, value)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        expires, value = entry
        if expires <= self._clock():
            del self._data[key]
            return default
        return value

    def delete(self, key):
        self._data.pop(key, None)

    def __contains__(self, key):
        return self.get(key, _MISSING) is not _MISSING

    def __len__(self):
        self._purge()
        return len(self._data)

    def _purge(self):
        now = self._clock()
        for key in [k for k, (expires, _) in self._data.items() if expires <= now]:
            del self._data[key]

    def clear(self):
        self._data.clear()
