import pytest

from ultimatespray.spray import FORWARD_HEADER, RotatingProxy, random_ip


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return "ok"


def test_random_ip_shape():
    parts = random_ip().split(".")
    assert len(parts) == 4
    assert all(1 <= int(p) <= 254 for p in parts)


def test_requires_at_least_one_url():
    with pytest.raises(ValueError):
        RotatingProxy([])


def test_round_robin_and_spoof_header():
    session = FakeSession()
    proxy = RotatingProxy(
        ["https://a.example/stage/", "https://b.example/stage/"], session=session
    )
    proxy.get("/login")
    proxy.get("/login")
    proxy.get("/login")
    urls = [c[1] for c in session.calls]
    assert urls == [
        "https://a.example/stage/login",
        "https://b.example/stage/login",
        "https://a.example/stage/login",
    ]
    assert all(FORWARD_HEADER in c[2]["headers"] for c in session.calls)


def test_spoof_can_be_disabled():
    session = FakeSession()
    proxy = RotatingProxy(
        ["https://a.example/"], spoof_forwarded_for=False, session=session
    )
    proxy.get("/x")
    assert "headers" not in session.calls[0][2] or FORWARD_HEADER not in (
        session.calls[0][2].get("headers", {})
    )
