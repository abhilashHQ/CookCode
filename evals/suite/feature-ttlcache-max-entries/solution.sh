set -euo pipefail
cat > cache.py <<'EOF'
import time
from collections import OrderedDict

_MISSING = object()


class TTLCache:
    """In-memory cache whose entries expire ``ttl`` seconds after they are set."""

    def __init__(self, ttl, clock=time.monotonic, max_entries=None):
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if max_entries is not None and max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self.ttl = ttl
        self.max_entries = max_entries
        self._clock = clock
        self._data = OrderedDict()

    def set(self, key, value):
        if key in self._data:
            self._data.move_to_end(key)
        elif self.max_entries is not None and len(self._data) >= self.max_entries:
            self._purge()
            while len(self._data) >= self.max_entries:
                self._data.popitem(last=False)
        self._data[key] = (self._clock() + self.ttl, value)

    def _lookup(self, key):
        entry = self._data.get(key)
        if entry is None:
            return _MISSING
        expires, value = entry
        if expires <= self._clock():
            del self._data[key]
            return _MISSING
        return value

    def get(self, key, default=None):
        value = self._lookup(key)
        if value is _MISSING:
            return default
        self._data.move_to_end(key)
        return value

    def delete(self, key):
        self._data.pop(key, None)

    def __contains__(self, key):
        return self._lookup(key) is not _MISSING

    def __len__(self):
        self._purge()
        return len(self._data)

    def _purge(self):
        now = self._clock()
        for key in [k for k, (expires, _) in self._data.items() if expires <= now]:
            del self._data[key]

    def clear(self):
        self._data.clear()
EOF
