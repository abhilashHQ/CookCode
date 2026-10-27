import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from apiclient import Client, NotFound, RetryError, TransientError, Unauthorized
from apiclient.retry import retry

class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def __call__(self, method, path):
        self.calls.append((method, path))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

def make_client(responses, attempts=4):
    sleeps = []
    transport = FakeTransport(responses)
    return Client(transport, attempts=attempts, sleep=sleeps.append), transport, sleeps

def test_get_user():
    client, _, _ = make_client([(200, {"id": 7, "name": "Mo"})])
    assert client.get_user(7)["name"] == "Mo"

def test_retries_server_errors():
    client, transport, _ = make_client([(503, {}), (200, {"projects": ["a"]})])
    assert client.list_projects() == ["a"]
    assert len(transport.calls) == 2

def test_gives_up_after_attempts():
    client, transport, sleeps = make_client([(500, {})] * 4)
    with pytest.raises(RetryError) as info:
        client.get_user(1)
    assert len(transport.calls) == 4
    assert isinstance(info.value.last, TransientError)
    assert sleeps == [0.5, 1.0, 2.0]

def test_rate_limit_is_retried():
    client, transport, _ = make_client([(429, {}), (429, {}), (200, {"id": 1})])
    assert client.get_user(1) == {"id": 1}
    assert len(transport.calls) == 3

def test_not_found_raised_immediately():
    client, transport, sleeps = make_client([(404, {"error": "no such user"})] + [(200, {})] * 3)
    with pytest.raises(NotFound):
        client.get_user(99)
    assert len(transport.calls) == 1
    assert sleeps == []

@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_raised_immediately(status):
    client, transport, _ = make_client([(status, {})] + [(200, {})] * 3)
    with pytest.raises(Unauthorized):
        client.delete_project(3)
    assert len(transport.calls) == 1

def test_keyboard_interrupt_propagates():
    client, transport, _ = make_client([KeyboardInterrupt()] + [(200, {})] * 3)
    with pytest.raises(KeyboardInterrupt):
        client.get_user(1)
    assert len(transport.calls) == 1

def test_programming_errors_propagate():
    client, transport, _ = make_client([TypeError("bad adapter")] + [(200, {})] * 3)
    with pytest.raises(TypeError):
        client.get_user(1)
    assert len(transport.calls) == 1

def test_decorator_respects_custom_retry_on():
    calls = []
    @retry(attempts=3, retry_on=(ConnectionError,), sleep=lambda s: None)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("reset")
        return "ok"
    assert flaky() == "ok" and len(calls) == 3
'''


def fail(msg):
    print(msg)
    sys.exit(1)


def run_hidden_tests(source):
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_hidden.py"
        test_file.write_text(source)
        env = dict(os.environ, PYTHONPATH=str(WORKDIR), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=180,
        )
    if proc.returncode != 0:
        lines = proc.stdout.splitlines()
        summary = [l for l in lines if l.startswith(("FAILED", "ERROR"))] or (proc.stdout + proc.stderr).strip().splitlines() or ["no output"]
        fail("hidden tests failed: " + summary[0][:300])


def require_tests(relpath, names):
    path = WORKDIR / relpath
    if not path.is_file():
        fail(f"{relpath} was deleted")
    text = path.read_text()
    missing = [n for n in names if f"def {n}(" not in text]
    if missing:
        fail(f"{relpath} lost tests: {', '.join(missing)}")


require_tests("tests/test_client.py", ["test_get_user", "test_retries_server_errors", "test_gives_up_after_attempts"])
run_hidden_tests(HIDDEN)
sys.exit(0)
