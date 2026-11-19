"""Small media helpers designed to run inside isolated workers."""
from __future__ import annotations

from pathlib import Path


def inspect_media(path: str, media: str) -> dict | None:
    """Return {'duration_s', 'has_video', 'has_audio', ...} or None when the file is not decodable.

    Uses FFmpeg stream probing (handles webm/opus/m4a/aac/3gp/wmv...); falls back to
    OpenCV / libsndfile when FFmpeg is unavailable."""
    from ..agents.mediainfo import probe

    info = probe(path)
    if info is not None:
        return {"duration_s": info.duration_s, "has_video": info.has_video, "has_audio": info.has_audio, "fps": info.fps,
                "video_codec": info.video_codec, "audio_codec": info.audio_codec}
    if media == "audio":
        import soundfile as sf

        try:
            return {"duration_s": float(sf.info(path).duration), "has_video": False, "has_audio": True}
        except Exception:  # noqa: BLE001
            return None
    from ..agents.video_agent import probe as cv_probe

    try:
        i = cv_probe(path)
    except Exception:  # noqa: BLE001
        return None
    if i["frame_count"] <= 0:
        return None
    return {"duration_s": i["duration_s"], "has_video": True, "has_audio": None}


def media_duration(path: str, media: str) -> float | None:
    info = inspect_media(path, media)
    return info["duration_s"] if info else None


def make_image_preview(src: str, dest: str, max_side: int = 640) -> bool:
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    h, w = img.shape[:2]
    s = min(1.0, max_side / max(h, w))
    img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if ok:
        Path(dest).write_bytes(enc.tobytes())
    return bool(ok)
