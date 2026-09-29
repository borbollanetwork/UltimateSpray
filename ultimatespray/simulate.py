"""Offline preview of a proposed authentication test.

This module does not create AWS resources or send network requests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def load_users(path: str) -> list[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read user list: {exc}") from exc
    users = list(dict.fromkeys(line.strip() for line in lines if line.strip()))
    if not users:
        raise ValueError("The user list is empty")
    return users


def validate_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A full HTTP(S) URL is required")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL credentials, query strings and fragments are not accepted")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def build_report(users: list[str], url: str) -> dict:
    return {
        "mode": "simulate",
        "network_requests": 0,
        "target": url,
        "summary": {"users": len(users), "not_tested": len(users)},
        "results": [
            {"username": user, "outcome": "not_tested", "detail": "simulation_only"}
            for user in users
        ],
    }


def write_report(path: str, report: dict) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise ValueError(f"Cannot create report: {exc}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except Exception:
        os.unlink(path)
        raise
