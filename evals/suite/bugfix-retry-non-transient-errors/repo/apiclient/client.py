import time

from .errors import error_for_status
from .retry import retry


class Client:
    """Thin JSON API client.

    ``transport`` is a callable ``(method, path) -> (status, body)``; production
    code passes an HTTP adapter, tests pass a fake.
    """

    def __init__(self, transport, attempts=4, backoff=0.5, sleep=time.sleep):
        self.transport = transport
        self._request = retry(attempts=attempts, backoff=backoff, sleep=sleep)(self._request_once)

    def _request_once(self, method, path):
        status, body = self.transport(method, path)
        if status >= 400:
            message = body.get("error", "") if isinstance(body, dict) else ""
            raise error_for_status(status, message)
        return body

    def get_user(self, user_id):
        return self._request("GET", f"/users/{user_id}")

    def list_projects(self):
        return self._request("GET", "/projects")["projects"]

    def delete_project(self, project_id):
        self._request("DELETE", f"/projects/{project_id}")
