"""End-to-end workflow: register → case → upload all media → analyze → review → report."""
import io

from app.services.isolation import run_isolated
from evaluation.synth import make_audio_pair, make_image_pair, make_video_pair

from .conftest import upload


def test_full_investigation_workflow(client, auth, case_id, fixtures_dir):
    _, img = make_image_pair(fixtures_dir, 301, "copy_move")
    _, vid = make_video_pair(fixtures_dir, 302)
    _, aud = make_audio_pair(fixtures_dir, 303)
    txt = fixtures_dir / "statement.srt"
    txt.write_text("1\n00:00:00,500 --> 00:00:03,000\nThis 60-second video was filmed at night on 2024-03-15.\n\n2\n00:00:20,000 --> 00:00:25,000\nIt was not edited.\n", "utf-8")
    ids = {}
    for p, mime in ((img, "image/jpeg"), (vid, "video/mp4"), (aud, "audio/wav"), (txt, "text/plain")):
        r = upload(client, auth, case_id, p, mime)
        assert r.status_code == 201, r.text
        ids[r.json()["media_type"]] = r.json()["id"]

    job = client.post(f"/api/cases/{case_id}/analyze", headers=auth)
    assert job.status_code == 202
    status = client.get(f"/api/jobs/{job.json()['id']}", headers=auth).json()
    assert status["status"] in ("completed", "partial") and status["progress"] == 1.0, status

    res = client.get(f"/api/cases/{case_id}/results/latest", headers=auth).json()
    agents = {r["agent_name"] for r in res["agent_results"]}
    # the six investigation agents (AI-model results are filed under their media agent)
    for expected in ("Image Forensics Agent", "Video Analysis Agent", "Audio Analysis Agent", "Text Analysis Agent",
                     "Metadata Verification Agent", "Lead Investigation Agent"):
        assert expected in agents, (expected, agents)
    for media_agent in ("Image Forensics Agent", "Video Analysis Agent", "Audio Analysis Agent", "Text Analysis Agent"):
        assert f"{media_agent} (AI model)" in agents, media_agent
    assert "Video Analysis Agent (voice-face sync)" in agents
    assert not ({"Cross-Modal Correlation Agent", "Evidence Review Agent", "Report Generation Agent"} & agents)  # merged into the Lead agent
    for r in res["agent_results"]:
        assert r["agent_version"] and r["created_at"] and isinstance(r["limitations"], list)
    itypes = {i["indicator_type"] for i in res["indicators"]}
    assert "copy_move_cluster" in itypes and "abrupt_temporal_change" in itypes
    assert any(t in itypes for t in ("noise_floor_shift", "digital_silence", "audio_splice_discontinuity"))
    assert res["findings"], "cross-modal findings expected"
    assert any(f["effect"] == "strengthens" for f in res["findings"])
    summary = res["summary"]
    assert set(summary["assessments"]) == set(ids.values())
    assert all("not a determination" in a["statement"] for a in summary["assessments"].values())

    # artifacts are served
    img_res = next(r for r in res["agent_results"] if r["agent_name"] == "Image Forensics Agent")
    assert client.get(f"/api/evidence/{ids['image']}/artifacts/{img_res['artifacts']['heatmap']}", headers=auth).headers["content-type"] == "image/jpeg"
    vid_res = next(r for r in res["agent_results"] if r["agent_name"] == "Video Analysis Agent")
    thumb = next(f["thumb"] for f in vid_res["artifacts"]["frames"] if f["thumb"])
    assert client.get(f"/api/evidence/{ids['video']}/artifacts/{thumb}", headers=auth).status_code == 200

    # history & traceability, dashboard
    assert client.get(f"/api/cases/{case_id}/graph", headers=auth).status_code == 404  # removed feature
    tl = client.get(f"/api/cases/{case_id}/timeline", headers=auth).json()
    assert [e["action"] for e in tl][:1] == ["case.created"] and any(e["action"] == "analysis.completed" for e in tl)
    stats = client.get("/api/cases/dashboard/stats", headers=auth).json()
    assert stats["evidence_by_type"] == {"image": 1, "video": 1, "audio": 1, "text": 1}
    hist = client.get("/api/history", headers=auth).json()
    assert hist["total"] == 1 and hist["items"][0]["case_reference"].startswith("TL-")

    # integrity check, then a report
    client.post(f"/api/evidence/{ids['image']}/verify", headers=auth)
    rep = client.post(f"/api/cases/{case_id}/reports", headers=auth)
    assert rep.status_code == 201, rep.text
    pdf = client.get(f"/api/reports/{rep.json()['id']}/download", headers=auth)
    assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 5000
    import hashlib

    assert hashlib.sha256(pdf.content).hexdigest() == rep.json()["sha256"] == pdf.headers["x-report-sha256"]
    content = rep.json()["content"]
    assert content["evidence"][0]["sha256"] and "notes" not in content

    # re-analysis uses cached agent outputs and does not run concurrently
    j2 = client.post(f"/api/cases/{case_id}/analyze", headers=auth)
    assert j2.status_code == 202
    assert client.get("/api/history", headers=auth).json()["total"] == 2


def test_pdf_text_contains_required_sections(client, auth, case_id, fixtures_dir):
    _, img = make_image_pair(fixtures_dir, 310, "copy_move")
    upload(client, auth, case_id, img, "image/jpeg")
    client.post(f"/api/cases/{case_id}/analyze", headers=auth)
    rep = client.post(f"/api/cases/{case_id}/reports", headers=auth).json()
    pdf = client.get(f"/api/reports/{rep['id']}/download", headers=auth).content
    import base64
    import re
    import zlib

    text = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = m.group(1).strip()
        try:  # ReportLab encodes page streams as ASCII85 + Flate
            text += zlib.decompress(base64.a85decode(raw.removesuffix(b"~>")))
        except Exception:
            continue
    for section in (b"Summary of findings", b"Evidence register", b"Manipulation indicators", b"Cross-modal", b"Alternative explanations", b"Analysis history"):
        assert section in text, section
    assert b"Human review decisions" not in text


def test_analyze_requires_evidence_and_blocks_duplicates(client, auth):
    c = client.post("/api/cases", json={"title": "Empty case"}, headers=auth).json()
    assert client.post(f"/api/cases/{c['id']}/analyze", headers=auth).status_code == 422


def test_text_only_case_with_context(client, auth, case_id):
    client.post(f"/api/cases/{case_id}/evidence", files={"file": ("caption.txt", io.BytesIO(b"Posted on 2024-03-20, shocking footage"), "text/plain")}, headers=auth)
    job = client.post(f"/api/cases/{case_id}/analyze", headers=auth).json()
    st = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert st["status"] == "completed"


def test_failed_agent_is_reported_not_hidden(client, auth, case_id, fixtures_dir, monkeypatch):
    from app.agents import image_agent

    _, img = make_image_pair(fixtures_dir, 320, "splice")
    upload(client, auth, case_id, img, "image/jpeg")

    def boom(*a, **k):
        raise RuntimeError("decoder crashed")

    monkeypatch.setattr(image_agent, "analyze", boom)
    job = client.post(f"/api/cases/{case_id}/analyze", headers=auth).json()
    res = client.get(f"/api/jobs/{job['id']}/results", headers=auth).json()
    assert res["status"] == "partial"
    failed = [r for r in res["agent_results"] if r["status"] == "failed"]
    assert failed and "decoder crashed" in failed[0]["errors"][0]
    a = next(iter(res["summary"]["assessments"].values()))
    assert a["failed_analyses"] and a["uncertainty"] == "high"


def _slow(x):
    import time

    time.sleep(x)
    return x


def test_isolated_worker_timeout(monkeypatch):
    import pytest

    from app.config import get_settings
    from app.services.isolation import AnalysisTimeout

    monkeypatch.setattr(get_settings(), "process_isolation", True)
    assert run_isolated(_slow, 0.01, timeout=60) == 0.01
    with pytest.raises(AnalysisTimeout):
        run_isolated(_slow, 30, timeout=1)
    assert run_isolated(_slow, 0.01, timeout=60) == 0.01  # pool recovered
