"""Build the 'TruthLens first live test' evidence video (MP4) and a companion SRT transcript.

Every manipulation is deliberate and documented so each analysis feature has something to find:
  * base footage: a face-swap deepfake from the labelled DFD set (paid actors; research/evaluation use only)
  * 9.0 s : 1.5 s of an unrelated genuine clip spliced in          -> abrupt temporal change
  * 10.5 s: 1.0 s frozen frame                                      -> duplicated frames
  * audio : synthetic speech-like track (not the person's voice)    -> voice-face sync mismatch
            12-16 s replaced by a louder-noise "other recording"    -> noise-floor shift + splice discontinuities
            16.0 s 150 ms of exact digital silence                  -> digital-silence insertion
  * metadata: creation_time 2023-08-02 (claim says 15 March 2024)   -> cross-modal date conflict
Usage:  python -m evaluation.make_live_test_video [--out ../test_media]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from app.agents.mediainfo import ffmpeg_exe, probe

from .synth import _speech_like, write_wav

DATA = Path(__file__).resolve().parent.parent / "data" / "eval_videos"
FAKE = "dataset/fake/04_06__kitchen_still__ZK95PQDE.mp4"
REAL = "dataset/real/04__walk_down_hall_angry.mp4"
SR = 44100


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-800:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "test_media"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    items = {i["file"]: i["path"] for i in json.loads((DATA / "manifest.json").read_text())["items"]}
    fake, real = items[FAKE], items[REAL]
    ff = ffmpeg_exe()
    work = Path(tempfile.mkdtemp(prefix="tl-live-"))

    # ---- video: A(fake 0-9) + B(real 3-4.5) + C(fake 9.0-9.4 frozen 1.0 s) + D(fake 9.4-22)
    norm = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1,format=yuv420p"
    fc = (f"[0:v]trim=0:9,setpts=PTS-STARTPTS,{norm}[a];"
          f"[1:v]trim=3:4.5,setpts=PTS-STARTPTS,{norm}[b];"
          f"[0:v]trim=9:9.4,setpts=PTS-STARTPTS,{norm},tpad=stop_mode=clone:stop_duration=1.0[c];"
          f"[0:v]trim=9.4:22,setpts=PTS-STARTPTS,{norm}[d];"
          "[a][b][c][d]concat=n=4:v=1:a=0[v]")
    silent = work / "video.mp4"
    run([ff, "-nostdin", "-y", "-loglevel", "error", "-i", fake, "-i", real, "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(silent)])
    dur = probe(silent).duration_s

    # ---- audio: speech-like track with a spliced louder-noise segment and digital silence
    rng = np.random.default_rng(2026)
    x = _speech_like(rng, dur, SR, noise_db=-48)
    other = _speech_like(np.random.default_rng(99), dur, SR, noise_db=-24)
    s0, s1 = int(12.0 * SR), int(16.0 * SR)
    x[s0:s1] = other[s0:s1] * 1.4 + 0.02  # different "recording": louder noise floor, DC step = hard cut
    x[s1: s1 + int(0.15 * SR)] = 0.0      # exact digital silence right after the splice
    wav = work / "audio.wav"
    write_wav(wav, np.clip(x, -1, 1), SR)

    final = out / "truthlens_first_live_test.mp4"
    run([ff, "-nostdin", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav), "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
         "-metadata", "creation_time=2023-08-02T14:05:00Z", "-metadata", "title=Kitchen clip (forwarded)", str(final)])

    srt = out / "truthlens_first_live_test_transcript.srt"
    srt.write_text(
        "1\n00:00:00,500 --> 00:00:04,000\nSpeaker: I recorded this myself on 15 March 2024 at night.\n\n"
        "2\n00:00:04,500 --> 00:00:08,500\nThis is the full 60-second video, filmed on an iPhone.\n\n"
        "3\n00:00:13,000 --> 00:00:15,500\nNothing in this clip was edited or altered.\n\n"
        "4\n00:00:40,000 --> 00:00:44,000\nShare before they delete it!\n", "utf-8")
    info = probe(final)
    print(json.dumps({"video": str(final), "transcript": str(srt), "duration_s": info.duration_s, "video_codec": info.video_codec,
                      "audio_codec": info.audio_codec, "size_mb": round(final.stat().st_size / 1e6, 2)}, indent=1))


if __name__ == "__main__":
    main()
