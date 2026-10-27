import json
import urllib.request

from .settings import load

MAX_TIMEOUT = 20.0


class HttpClient:
    def __init__(self, settings=None):
        settings = settings or load()
        self.base_url = settings["base_url"].rstrip("/")
        self.timeout = min(settings["timeout"], MAX_TIMEOUT)
        self.retries = settings["retries"]

    def get_json(self, path):
        last = None
        for _ in range(self.retries + 1):
            try:
                with urllib.request.urlopen(self.base_url + path, timeout=self.timeout) as resp:
                    return json.load(resp)
            except OSError as exc:
                last = exc
        raise last
