import json
import urllib.request

from .config import POOL_SIZE, rate_limit
from .throttle import Throttle


class ApiClient:
    def __init__(self, base_url, token, timeout=10, rpm=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.pool_size = POOL_SIZE
        self.throttle = Throttle(rpm if rpm is not None else rate_limit())

    def get(self, path):
        self.throttle.wait()
        req = urllib.request.Request(self.base_url + path, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
