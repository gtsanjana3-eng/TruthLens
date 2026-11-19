"""Isolated media-processing workers.

Untrusted media is decoded in a separate OS process with a hard timeout, so a decoder
crash, hang or memory blow-up cannot take down the API process. On timeout the worker
pool is terminated and recreated."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from ..config import get_settings

log = logging.getLogger(__name__)
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()


class AnalysisTimeout(RuntimeError):
    pass


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=max(1, get_settings().worker_threads))
        return _pool


def _reset_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            for p in list(getattr(_pool, "_processes", {}).values()):
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


def run_isolated(fn: Callable[..., Any], *args: Any, timeout: float | None = None) -> Any:
    s = get_settings()
    timeout = timeout or s.analysis_timeout_seconds
    if not s.process_isolation:
        return fn(*args)
    fut = _get_pool().submit(fn, *args)
    try:
        return fut.result(timeout=timeout)
    except FutureTimeout:
        log.warning("Analysis worker timed out after %ss; recycling pool", timeout)
        _reset_pool()
        raise AnalysisTimeout(f"Analysis exceeded {timeout}s and was terminated")
    except Exception as e:  # BrokenProcessPool etc.
        if "BrokenProcessPool" in type(e).__name__:
            _reset_pool()
        raise


def shutdown() -> None:
    _reset_pool()
