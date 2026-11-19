"""Durable file copies in Supabase Storage, tested against an in-memory fake of its REST API."""
import stat

import httpx
import pytest

from app import remote_storage
from app.config import get_settings
from app.storage import evidence_path


@pytest.fixture
def fake_bucket(monkeypatch):
    objects: dict[str, bytes] = {}
    buckets: set[str] = set()

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer test-service-key"
        path = req.url.path.removeprefix("/storage/v1")
        if path == "/bucket" and req.method == "POST":
            name = req.read().decode()
            if "truthlens-files" in buckets:
                return httpx.Response(409, json={"error": "Duplicate", "message": "The resource already exists"})
            buckets.add("truthlens-files")
            return httpx.Response(200, json={"name": name})
        key = path.removeprefix("/object/truthlens-files/")
        if req.method == "POST":
            if key in objects:
                return httpx.Response(409, json={"error": "Duplicate", "message": "The resource already exists"})
            objects[key] = req.read()
            return httpx.Response(200, json={"Key": key})
        if req.method == "GET":
            return httpx.Response(200, content=objects[key]) if key in objects else httpx.Response(404)
        return httpx.Response(405)

    s = get_settings()
    monkeypatch.setattr(s, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(s, "supabase_service_key", "test-service-key")
    monkeypatch.setattr(remote_storage, "_bucket_ready", False)
    monkeypatch.setattr(remote_storage, "_client", lambda: httpx.Client(base_url="https://example.supabase.co/storage/v1",
                                                                        transport=httpx.MockTransport(handler),
                                                                        headers={"Authorization": "Bearer test-service-key"}))
    return objects


def test_disabled_without_credentials(tmp_path):
    assert not remote_storage.enabled()
    f = tmp_path / "x.txt"
    f.write_text("hi")
    assert remote_storage.push("k", f) is False
    assert remote_storage.pull("k", tmp_path / "missing.txt") is False


def test_upload_is_mirrored_and_restored_after_disk_loss(client, auth, case_id, fake_bucket):
    data = b"The meeting took place on 3 March 2025 in Bengaluru."
    r = client.post(f"/api/cases/{case_id}/evidence", files={"file": ("note.txt", data, "text/plain")}, headers=auth)
    assert r.status_code == 201
    ev = r.json()
    keys = [k for k in fake_bucket if k.startswith("evidence/")]
    assert len(keys) == 1 and fake_bucket[keys[0]] == data

    # simulate a host restart that wiped the temporary disk
    local = evidence_path(keys[0].removeprefix("evidence/"))
    local.chmod(stat.S_IREAD | stat.S_IWRITE)
    local.unlink()
    assert not local.exists()

    dl = client.get(f"/api/evidence/{ev['id']}/file", headers=auth)
    assert dl.status_code == 200 and dl.content == data
    v = client.post(f"/api/evidence/{ev['id']}/verify", headers=auth).json()
    assert v["intact"] is True  # restored copy matches the SHA-256 recorded at upload


def test_duplicate_upload_is_not_reuploaded(client, auth, case_id, fake_bucket):
    data = b"same bytes twice"
    for _ in range(2):
        assert client.post(f"/api/cases/{case_id}/evidence", files={"file": ("a.txt", data, "text/plain")}, headers=auth).status_code == 201
    assert len([k for k in fake_bucket if k.startswith("evidence/")]) == 1
