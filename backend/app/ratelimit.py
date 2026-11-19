"""Sliding-window in-memory rate limiter (per client IP + route group).

Single-process by design; for horizontally scaled deployments back this with Redis."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from .config import get_settings

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check(key: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry = int(window - (now - q[0])) + 1
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down", headers={"Retry-After": str(retry)})
        q.append(now)


def limiter(group: str):
    def dep(request: Request) -> None:
        s = get_settings()
        limit = {"auth": s.rate_limit_auth_per_minute, "upload": s.rate_limit_upload_per_minute}.get(group, s.rate_limit_default_per_minute)
        _check(f"{group}:{client_ip(request)}", limit)
    return dep


def reset() -> None:
    with _lock:
        _hits.clear()
