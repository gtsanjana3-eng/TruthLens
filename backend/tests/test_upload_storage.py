"""File validation, secure storage, integrity vault."""
import io
import os
import stat

import pytest

from app.storage import ValidationError, classify, evidence_path, safe_display_name, safe_join, sniff_signature
from evaluation.synth import make_audio_pair, make_image_pair

from .conftest import upload


def test_signature_sniffing():
    assert sniff_signature(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert sniff_signature(b"\x89PNG\r\n\x1a\n....") == "png"
    assert sniff_signature(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "wav"
    assert sniff_signature(b"\x00\x00\x00\x18ftypmp42") == "isobmff"
    assert sniff_signature(b"MZ\x90\x00") is None


def test_classify_rejects_mismatch_and_unknown():
    with pytest.raises(ValidationError):
        classify("evil.exe", "application/octet-stream")
    with pytest.raises(ValidationError):
        classify("photo.jpg", "video/mp4")
    ext, rule = classify("Photo.JPG", "image/jpeg")
    assert ext == ".jpg" and rule.media.value == "image"


def test_safe_names_and_traversal(tmp_path):
    assert "/" not in safe_display_name("../../etc/passwd") and safe_display_name("../../etc/passwd") == "passwd"
    assert safe_display_name("..\\..\\win.ini") == "win.ini"
    with pytest.raises(ValidationError):
        safe_join(tmp_path, "../outside.txt")
    assert safe_join(tmp_path, "ab/cd.jpg").is_relative_to(tmp_path.resolve())


def test_upload_image_hash_and_readonly(client, auth, case_id, fixtures_dir):
    g, _ = make_image_pair(fixtures_dir, 101, "copy_move")
    r = upload(client, auth, case_id, g, "image/jpeg", name="../../sneaky name.jpg")
    assert r.status_code == 201, r.text
    ev = r.json()
    import hashlib

    assert ev["sha256"] == hashlib.sha256(g.read_bytes()).hexdigest()
    assert ev["label"] == "E1" and ev["media_type"] == "image" and ev["preview_available"]
    assert ".." not in ev["original_filename"]
    from app.database import SessionLocal
    from app.models import EvidenceFile

    db = SessionLocal()
    stored = evidence_path(db.get(EvidenceFile, ev["id"]).stored_path)
    db.close()
    assert stored.name == f"{ev['sha256']}.jpg"
    assert not os.stat(stored).st_mode & stat.S_IWRITE  # originals are write-protected
    v = client.post(f"/api/evidence/{ev['id']}/verify", headers=auth).json()
    assert v["intact"] and v["current_sha256"] == ev["sha256"]
    vault = client.get(f"/api/cases/{case_id}/vault", headers=auth).json()
    assert [h["purpose"] for h in vault["items"][0]["hashes"]] == ["upload", "verification"]
    # original download returns identical bytes
    d = client.get(f"/api/evidence/{ev['id']}/file", headers=auth)
    assert d.content == g.read_bytes() and "attachment" in d.headers["content-disposition"]


def test_any_extension_accepted_and_classified_by_content(client, auth, case_id, fixtures_dir):
    def post(name, data, ct):
        return client.post(f"/api/cases/{case_id}/evidence", files={"file": (name, io.BytesIO(data), ct)}, headers=auth)

    # executable renamed to .jpg -> preserved as "other", mismatch recorded, never served inline
    r = post("cat.jpg", b"MZ\x90\x00" + b"\x00" * 100, "image/jpeg")
    assert r.status_code == 201 and r.json()["media_type"] == "other"
    assert r.json()["file_metadata"]["content_notes"]
    dl = client.get(f"/api/evidence/{r.json()['id']}/file?inline=true", headers=auth)
    assert dl.headers["content-disposition"].startswith("attachment")
    # unusual extension with plain-text content is analysed as text
    r = post("notes.xyz", b"hello world, plain text", "application/octet-stream")
    assert r.status_code == 201 and r.json()["media_type"] == "text"
    r = post("bin.txt", b"abc\x00def", "text/plain")
    assert r.status_code == 201 and r.json()["media_type"] == "other"
    # wav bytes with a .png name are classified as audio by signature
    _, a = make_audio_pair(fixtures_dir, 102)
    r = upload(client, auth, case_id, a, "image/png", name="sound.png")
    assert r.status_code == 201 and r.json()["media_type"] == "audio" and r.json()["file_metadata"]["content_notes"]
    # an empty file is still rejected (nothing to preserve) and the rejection is audited
    r = post("empty.txt", b"", "text/plain")
    assert r.status_code == 422
    log = client.get(f"/api/cases/{case_id}/audit", headers=auth).json()
    assert any(i["action"] == "evidence.rejected" for i in log["items"])


def test_upload_size_limit(client, auth, case_id, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_image_mb", 1)
    big = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024 + 10))  # PNG signature over the image limit
    r = client.post(f"/api/cases/{case_id}/evidence", files={"file": ("big.png", big, "image/png")}, headers=auth)
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_artifact_path_traversal_blocked(client, auth, case_id, fixtures_dir):
    g, _ = make_image_pair(fixtures_dir, 103, "splice")
    ev = upload(client, auth, case_id, g, "image/jpeg").json()
    for bad in ("../../../config.py", "..%2F..%2Fsecret.jpg", "x.py"):
        assert client.get(f"/api/evidence/{ev['id']}/artifacts/{bad}", headers=auth).status_code in (400, 404)


def test_soft_delete_hides_evidence(client, auth, case_id, fixtures_dir):
    g, _ = make_image_pair(fixtures_dir, 104, "splice")
    ev = upload(client, auth, case_id, g, "image/jpeg").json()
    assert client.delete(f"/api/evidence/{ev['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/evidence/{ev['id']}", headers=auth).status_code == 404
    vault = client.get(f"/api/cases/{case_id}/vault", headers=auth).json()
    assert vault["items"][0]["removed_at"] is not None  # retained in the vault record


def test_retention_purges_with_secure_delete(client, auth, case_id, fixtures_dir):
    from app.database import SessionLocal
    from app.models import AuditLog, EvidenceFile
    from scripts.retention import purge

    g, m = make_image_pair(fixtures_dir, 105, "splice")
    keep = upload(client, auth, case_id, g, "image/jpeg").json()
    gone = upload(client, auth, case_id, m, "image/jpeg").json()
    db = SessionLocal()
    gone_path = evidence_path(db.get(EvidenceFile, gone["id"]).stored_path)
    db.close()
    client.delete(f"/api/evidence/{gone['id']}", headers=auth)
    assert purge(days=1) == {"cases": 0, "evidence": 0, "files_deleted": 0}  # not old enough yet
    stats = purge(days=0)
    assert stats["evidence"] == 1 and stats["files_deleted"] == 1
    assert not gone_path.exists()
    db = SessionLocal()
    assert db.get(EvidenceFile, gone["id"]) is None and db.get(EvidenceFile, keep["id"]) is not None
    runs = db.query(AuditLog).filter(AuditLog.action == "retention.purged").order_by(AuditLog.id).all()
    assert len(runs) == 2 and runs[-1].detail["evidence"] == 1  # every run is audited, including no-op runs
    db.close()
    assert client.post(f"/api/evidence/{keep['id']}/verify", headers=auth).json()["intact"]
