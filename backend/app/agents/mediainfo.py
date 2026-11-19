"""Container/stream probing and safe audio decoding via the bundled FFmpeg binary.

FFmpeg is always invoked with an argument list (no shell), `-nostdin`, and a timeout.
Used to (a) classify audio-only "video" containers (e.g. browser .webm voice recordings),
(b) obtain duration/fps when OpenCV metadata is missing, and (c) decode formats libsndfile
cannot read (AAC/M4A, Opus, WMA, AMR...) into a *derived* PCM WAV for analysis. The original
evidence file is never modified."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


@dataclass
class StreamInfo:
    duration_s: float | None
    has_video: bool
    has_audio: bool
    fps: float | None
    video_codec: str | None
    audio_codec: str | None


def probe(path: str | Path, timeout: int = 30) -> StreamInfo | None:
    exe = ffmpeg_exe()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None
    err = r.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    vids = re.findall(r"Stream #\S+.*?: Video: (\w+)([^\n]*)", err)
    # Cover art in audio files shows up as an attached-picture "video" stream – not real video.
    vids = [v for v in vids if "attached pic" not in v[1]]
    auds = re.findall(r"Stream #\S+.*?: Audio: (\w+)", err)
    fps = None
    if vids:
        f = re.search(r"([\d.]+) fps", vids[0][1]) or re.search(r"([\d.]+) tbr", vids[0][1])
        fps = float(f[1]) if f else None
    if not vids and not auds:
        return None
    return StreamInfo(dur, bool(vids), bool(auds), fps, vids[0][0] if vids else None, auds[0] if auds else None)


def soundfile_can_read(path: str | Path) -> bool:
    try:
        import soundfile as sf

        with sf.SoundFile(str(path)) as f:
            return f.frames > 0
    except Exception:  # noqa: BLE001
        return False


def decode_to_wav(src: str | Path, dest: str | Path, timeout: int = 180) -> bool:
    """Decode any FFmpeg-readable audio to 16-bit PCM WAV at its native sample rate (lossless re-encode of decoded samples)."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-map", "0:a:0", "-c:a", "pcm_s16le", "-t", "1800", "-f", "wav", str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        dest.unlink(missing_ok=True)
        return False
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 100:
        dest.unlink(missing_ok=True)
        return False
    return True
