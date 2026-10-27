import pytest

from apiclient import Client, RetryError


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, path):
        self.calls.append((method, path))
        return self.responses.pop(0)


def make_client(responses, attempts=4):
    transport = FakeTransport(responses)
    return Client(transport, attempts=attempts, sleep=lambda s: None), transport


def test_get_user():
    client, _ = make_client([(200, {"id": 7, "name": "Mo"})])
    assert client.get_user(7)["name"] == "Mo"


def test_retries_server_errors():
    client, transport = make_client([(503, {}), (200, {"projects": ["a"]})])
    assert client.list_projects() == ["a"]
    assert len(transport.calls) == 2


def test_gives_up_after_attempts():
    client, transport = make_client([(500, {})] * 4)
    with pytest.raises(RetryError):
        client.get_user(1)
    assert len(transport.calls) == 4
