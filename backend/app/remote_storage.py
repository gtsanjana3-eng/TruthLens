"""Durable copy of evidence, reports and derived artifacts in a private Supabase Storage bucket.

Hosting platforms with a temporary disk (e.g. Hugging Face Spaces) lose local files on restart.
The local directories stay the working copy; this module mirrors them to Supabase Storage:

* after a file is written locally, `push()` uploads it (originals are content-addressed, so
  re-uploads are skipped),
* when a file is needed but missing locally, `pull()` restores it from the bucket.

Disabled (no-op) unless SUPABASE_URL and SUPABASE_SERVICE_KEY are set. The service key is a
server-side secret: it is only read from the environment and never logged or sent to browsers.
"""
from __future__ import annotations

import logging
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import get_settings

log = logging.getLogger("truthlens.storage")
_bucket_ready = False
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="remote-storage")


def enabled() -> bool:
    s = get_settings()
    return bool(s.supabase_url and s.supabase_service_key)


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(base_url=s.supabase_url.rstrip("/") + "/storage/v1", timeout=120,
                        headers={"Authorization": f"Bearer {s.supabase_service_key}", "apikey": s.supabase_service_key})


def _object_url(key: str) -> str:
    return f"/object/{get_settings().supabase_bucket}/{quote(key)}"


def _ensure_bucket(c: httpx.Client) -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    with _lock:
        if _bucket_ready:
            return
        name = get_settings().supabase_bucket
        r = c.post("/bucket", json={"id": name, "name": name, "public": False})
        if r.status_code not in (200, 201) and "already exists" not in r.text.lower() and r.status_code != 409:
            raise RuntimeError(f"Could not create storage bucket ({r.status_code})")
        _bucket_ready = True


def push(key: str, local: Path) -> bool:
    """Upload local -> bucket/key. Existing objects are kept (content-addressed originals never change)."""
    if not enabled() or not local.is_file():
        return False
    try:
        with _client() as c:
            _ensure_bucket(c)
            ctype = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
            with open(local, "rb") as f:
                r = c.post(_object_url(key), content=f.read(), headers={"Content-Type": ctype, "x-upsert": "false"})
            if r.status_code in (200, 201) or r.status_code == 409 or "duplicate" in r.text.lower() or "already exists" in r.text.lower():
                return True
            log.warning("Remote storage upload failed for %s (%s)", key, r.status_code)
    except Exception as e:  # noqa: BLE001 - durability copy must never break the request
        log.warning("Remote storage upload error for %s: %s", key, type(e).__name__)
    return False


def push_async(key: str, local: Path) -> None:
    if enabled():
        _pool.submit(push, key, Path(local))


def push_dir_async(prefix: str, directory: Path, suffixes: tuple[str, ...] = (".jpg", ".png", ".wav", ".json")) -> None:
    """Mirror every servable artifact under `directory` to bucket/prefix/... (background)."""
    if not enabled() or not directory.is_dir():
        return
    for f in directory.rglob("*"):
        if f.is_file() and f.suffix.lower() in suffixes:
            _pool.submit(push, f"{prefix}/{f.relative_to(directory).as_posix()}", f)


def pull(key: str, local: Path) -> bool:
    """Restore bucket/key -> local if the local copy is missing. Returns True if the file now exists."""
    if local.is_file():
        return True
    if not enabled():
        return False
    try:
        with _client() as c:
            r = c.get(_object_url(key))
            if r.status_code != 200:
                return False
            local.parent.mkdir(parents=True, exist_ok=True)
            tmp = local.with_suffix(local.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(local)
            return True
    except Exception as e:  # noqa: BLE001
        log.warning("Remote storage download error for %s: %s", key, type(e).__name__)
        return False
