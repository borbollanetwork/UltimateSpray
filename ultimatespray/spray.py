"""Client-side helpers for sending requests through UltimateSpray proxies.

``RotatingProxy`` is a thin ``requests.Session`` wrapper that:

* round-robins across one or more proxy base URLs, and
* spoofs a random ``X-My-X-Forwarded-For`` header per request, which the proxy
  maps onto ``X-Forwarded-For`` at the destination.

The API Gateway already rotates the true source IP on every request; the
spoofed header adds an extra layer for targets that read X-Forwarded-For.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable

import requests

FORWARD_HEADER = "X-My-X-Forwarded-For"


def random_ip() -> str:
    """Return a random routable-looking IPv4 address."""
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


class RotatingProxy:
    def __init__(
        self,
        proxy_urls: Iterable[str],
        spoof_forwarded_for: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        urls = [u.rstrip("/") for u in proxy_urls if u]
        if not urls:
            raise ValueError("At least one proxy URL is required")
        self._urls = urls
        self._cycle = itertools.cycle(urls)
        self.spoof_forwarded_for = spoof_forwarded_for
        self.session = session or requests.Session()

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Send ``method`` to ``path`` through the next proxy in rotation."""
        base = next(self._cycle)
        url = f"{base}/{path.lstrip('/')}"
        if self.spoof_forwarded_for:
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault(FORWARD_HEADER, random_ip())
            kwargs["headers"] = headers
        return self.session.request(method, url, **kwargs)

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.request("DELETE", path, **kwargs)
