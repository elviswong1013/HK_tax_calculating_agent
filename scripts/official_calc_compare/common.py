"""Shared helpers for the official-calculator comparison drivers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

# Official hosts the drivers are allowed to touch (task scope).
OFFICIAL_HOSTS = {"www.ird.gov.hk", "www.gov.hk"}


def hkt_now() -> str:
    """Current time in Hong Kong as an ISO-8601 string with explicit +08:00."""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


@dataclass
class CaseResult:
    """One driven case: everything the report needs, honestly recorded."""

    name: str
    url: str
    started_at: str = ""
    finished_at: str = ""
    status: str = "pending"  # success / failed
    steps: list[str] = field(default_factory=list)
    official_raw: str = ""  # original text displayed by the official page
    official_value: str | None = None  # parsed official amount (canonical digits)
    extra_observations: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    error: str | None = None

    def step(self, msg: str) -> None:
        self.steps.append(msg)

    def shot(self, path: str) -> None:
        self.screenshots.append(path)

    def finish(self, ok: bool, error: str | None = None) -> None:
        self.finished_at = hkt_now()
        self.status = "success" if ok else "failed"
        self.error = error


def normalize_amount(text: str | None) -> str | None:
    """'$4,250,000.00' / '4 250 000' -> '4250000' (None when absent)."""
    if text is None:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    return digits or None


def build_route_handler(fetcher) -> Callable:
    """Playwright route handler: fulfill official GETs with live-fetched bytes."""

    def handler(route) -> None:
        from urllib.parse import urlparse

        req = route.request
        host = urlparse(req.url).hostname or ""
        if req.method == "GET" and host in OFFICIAL_HOSTS:
            try:
                status, headers, body = fetcher.get(req.url)
                content_type = headers.get("content-type", "text/html; charset=utf-8")
                route.fulfill(status=status, body=body, headers={"content-type": content_type})
                return
            except Exception as exc:  # noqa: BLE001 - recorded by caller via fetcher.log
                fetcher.log.append(f"route handler error for {req.url}: {exc!r}")
        route.abort()

    return handler
