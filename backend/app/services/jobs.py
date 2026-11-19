"""Background job queue.

An in-process thread-pool queue that dispatches analysis jobs; the CPU-heavy media decoding
inside each job runs in isolated worker processes (see isolation.py). Job state lives in the
database, so progress can be polled and interrupted jobs are recovered on restart.
For multi-node deployments this module is the single seam to swap for Celery/RQ + Redis."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import select

from ..config import get_settings
from ..database import SessionLocal
from ..models import AnalysisJob, JobStatus, utcnow
from .orchestrator import run_job

log = logging.getLogger(__name__)
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_futures: dict[str, Future] = {}
SYNC_MODE = False  # tests set this to run jobs inline


def _ex() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=get_settings().worker_threads, thread_name_prefix="analysis")
        return _executor


def enqueue(job_id: str) -> None:
    if SYNC_MODE:
        run_job(job_id)
        return
    _futures[job_id] = _ex().submit(run_job, job_id)


def recover_interrupted() -> int:
    """Mark jobs left 'running' by a crash as failed and re-queue 'queued' ones."""
    db = SessionLocal()
    n = 0
    try:
        for job in db.scalars(select(AnalysisJob).where(AnalysisJob.status.in_([JobStatus.running, JobStatus.queued]))):
            if job.status == JobStatus.running:
                job.status, job.error, job.finished_at, job.stage = JobStatus.failed, "Interrupted by server restart", utcnow(), "Failed"
            else:
                enqueue(job.id)
            n += 1
        db.commit()
    finally:
        db.close()
    return n


def shutdown() -> None:
    global _executor
    if _executor:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
