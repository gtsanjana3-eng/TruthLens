"""Regression: real-world audio/video formats (phone, WhatsApp, browser recordings) upload and analyse.

Samples are generated with the bundled FFmpeg binary, so the test needs no external files."""
import subprocess

import pytest

from app.agents.mediainfo import ffmpeg_exe, probe
from app.storage import sniff_signature

from .conftest import upload

FF = ffmpeg_exe()
pytestmark = pytest.mark.skipif(FF is None, reason="bundled FFmpeg unavailable")

SINE = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
VID = ["-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15"]
FORMATS = {
    # name: (ffmpeg args, MIME a Windows browser sends, expected media type)
    "voice.m4a": (SINE + ["-c:a", "aac"], "audio/x-m4a", "audio"),
    "voice.aac": (SINE + ["-c:a", "aac"], "audio/aac", "audio"),
    "whatsapp.opus": (SINE + ["-c:a", "libopus"], "audio/ogg", "audio"),
    "recording.webm": (SINE + ["-c:a", "libopus"], "video/webm", "audio"),  # audio-only browser recording
    "song.mp3": (SINE + ["-c:a", "libmp3lame"], "audio/mpeg", "audio"),
    "clip.webm": (VID + SINE + ["-c:v", "libvpx", "-c:a", "libopus"], "video/webm", "video"),
    "clip.mov": (VID + SINE + ["-c:v", "libx264", "-c:a", "aac"], "video/quicktime", "video"),
    "clip.3gp": (["-f", "lavfi", "-i", "testsrc=duration=3:size=176x144:rate=15"] + ["-c:v", "mpeg4"], "video/3gpp", "video"),
    "clip.wmv": (VID + ["-c:v", "wmv2"], "video/x-ms-wmv", "video"),
}


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("formats")
    out = {}
    for name, (args, _, _) in FORMATS.items():
        p = d / name
        subprocess.run([FF, "-nostdin", "-loglevel", "error", "-y", *args, str(p)], check=True, timeout=120)
        out[name] = p
    return out


def test_signatures_recognised(samples):
    assert sniff_signature(samples["voice.m4a"].read_bytes()[:64]) == "isobmff"
    assert sniff_signature(samples["voice.aac"].read_bytes()[:64]) == "adts"
    assert sniff_signature(samples["whatsapp.opus"].read_bytes()[:64]) == "ogg"
    assert sniff_signature(samples["clip.wmv"].read_bytes()[:64]) == "asf"


def test_probe_detects_audio_only_container(samples):
    info = probe(samples["recording.webm"])
    assert info and info.has_audio and not info.has_video and 2.5 < info.duration_s < 3.5


def test_real_world_formats_upload_and_analyse(client, auth, case_id, samples):
    ids = {}
    for name, (_, mime, expected) in FORMATS.items():
        r = upload(client, auth, case_id, samples[name], mime)
        assert r.status_code == 201, (name, r.text)
        assert r.json()["media_type"] == expected, name
        ids[r.json()["id"]] = name
    job = client.post(f"/api/cases/{case_id}/analyze", headers=auth).json()
    res = client.get(f"/api/jobs/{job['id']}/results", headers=auth).json()
    assert res["status"] == "completed", [(r["agent_name"], r["errors"]) for r in res["agent_results"] if r["status"] == "failed"]
    main_agents = {(r["evidence_id"], r["agent_name"]): r for r in res["agent_results"] if r["evidence_id"]}
    for eid, name in ids.items():
        agent = "Audio Analysis Agent" if FORMATS[name][2] == "audio" else "Video Analysis Agent"
        assert main_agents[(eid, agent)]["status"] == "completed", name
    m4a = next(e for e, n in ids.items() if n == "voice.m4a")
    assert any("PCM decode" in l for l in main_agents[(m4a, "Audio Analysis Agent")]["limitations"])


def test_corrupt_media_preserved_as_other(client, auth, case_id, tmp_path):
    p = tmp_path / "broken.m4a"
    p.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 2000)  # valid signature, no decodable stream
    r = upload(client, auth, case_id, p, "audio/x-m4a")
    assert r.status_code == 201 and r.json()["media_type"] == "other"
    assert any("no decodable" in n for n in r.json()["file_metadata"]["content_notes"])


def test_wrong_declared_mime_classified_by_content(client, auth, case_id, samples):
    r = upload(client, auth, case_id, samples["voice.m4a"], "image/png")
    assert r.status_code == 201 and r.json()["media_type"] == "audio"
