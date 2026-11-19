"""Seed a demonstration investigation through the real HTTP API.

Creates a demo investigator account (test credentials below – for local demos only), a case
with a claimed context, synthetic evidence for all four media types, runs the multi-agent
analysis and generates a report.

Usage (backend running on :8000):  python -m scripts.seed_demo [--base http://127.0.0.1:8000]
"""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import httpx

from evaluation.synth import make_audio_pair, make_image_pair, make_video_pair

# Local demo/test account – never use for real data.
DEMO_EMAIL = "demo.investigator@example.com"
DEMO_PASSWORD = "DemoPassw0rd!2026"
DEMO_NAME = "Demo Investigator"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=120)
    r = c.post("/api/auth/register", json={"email": DEMO_EMAIL, "full_name": DEMO_NAME, "password": DEMO_PASSWORD})
    if r.status_code == 409:
        r = c.post("/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    r.raise_for_status()
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    case = c.post("/api/cases", headers=h, json={
        "title": "Viral 'night-time protest' clip – authenticity review",
        "description": "Demo case: social-media post with photo, video, voice note and transcript.",
        "context_statement": "This 60-second video was filmed at night on 15 March 2024 in Bengaluru with an iPhone and has not been edited.",
        "priority": "high"}).json()
    work = Path(tempfile.mkdtemp(prefix="tl-demo-"))
    _, img = make_image_pair(work, 42, "copy_move")
    genuine_img, _ = make_image_pair(work, 43, "splice")
    _, vid = make_video_pair(work, 44, seconds=8)
    _, aud = make_audio_pair(work, 45)
    srt = work / "transcript.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:03,500\nSpeaker: We recorded this on 15 March 2024 at night.\n\n"
                   "2\n00:00:04,000 --> 00:00:07,500\nNothing here was edited, this is the full 60-second video.\n\n"
                   "3\n00:00:30,000 --> 00:00:33,000\nShare before they delete it!\n", "utf-8")
    files = [(img, "image/jpeg", "Image attached to the viral post"), (genuine_img, "image/jpeg", "Reference photo from the same account"),
             (vid, "video/mp4", "Downloaded video clip"), (aud, "audio/wav", "Voice note forwarded with the clip"), (srt, "text/plain", "Transcript supplied by the poster")]
    for p, mime, desc in files:
        with open(p, "rb") as f:
            c.post(f"/api/cases/{case['id']}/evidence", headers=h, files={"file": (p.name, f, mime)}, data={"description": desc}).raise_for_status()
    job = c.post(f"/api/cases/{case['id']}/analyze", headers=h).json()
    while True:
        st = c.get(f"/api/jobs/{job['id']}", headers=h).json()
        print(f"  {st['progress'] * 100:5.1f}%  {st['stage']}")
        if st["status"] not in ("queued", "running"):
            break
        time.sleep(1)
    c.post(f"/api/cases/{case['id']}/reports", headers=h).raise_for_status()
    print(f"Demo case {case['reference']} ready – status {st['status']}. Sign in with the demo account defined in scripts/seed_demo.py.")


if __name__ == "__main__":
    main()
