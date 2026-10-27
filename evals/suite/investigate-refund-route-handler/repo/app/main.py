import json
import sys

from app.routes import dispatch

# Import order matters only in that every module must be imported for its
# routes to be registered.
from app.billing import refunds, payments, approvals  # noqa: F401,E402


def handle(method, path, body=None):
    return dispatch(method, path, {"json": body})


if __name__ == "__main__":
    status, payload = handle(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(status, json.dumps(payload))
