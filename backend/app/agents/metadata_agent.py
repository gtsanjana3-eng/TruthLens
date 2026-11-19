"""Metadata Analysis Agent ("Metadata Detective").

Reads container/EXIF metadata. Metadata alone is never treated as proof: it is easily
stripped or rewritten, so indicators here are capped at low/medium severity."""
from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import AgentOutput, Indicator, Signal, Timer, aggregate_signals
from .explanations import enrich

NAME = "Metadata Verification Agent"
VERSION = "1.1.0"

EDITORS = ["photoshop", "gimp", "lightroom", "snapseed", "affinity", "pixelmator", "picsart", "facetune", "canva", "paint.net", "capture one", "premiere", "after effects", "davinci", "final cut", "audacity", "adobe audition", "ffmpeg", "lavf", "handbrake"]
GENERATORS = ["stable diffusion", "midjourney", "dall-e", "dall·e", "dalle", "firefly", "novelai", "comfyui", "automatic1111", "invokeai", "imagen", "sora", "runway", "elevenlabs", "tortoise", "bark"]
MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)


_IJG_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
             18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]


def _ijg_quality(table: list[int]) -> int:
    """Estimate the IJG quality factor from a luminance quantisation table (order-independent mean ratio)."""
    scale = sum(q * 100.0 / b for q, b in zip(sorted(table), sorted(_IJG_LUMA))) / 64
    quality = 5000.0 / scale if scale > 100 else (200.0 - scale) / 2.0
    return int(max(1, min(100, round(quality))))


def _exif(path: Path, out: AgentOutput) -> dict:
    from PIL import ExifTags, Image

    info: dict = {}
    with Image.open(path) as im:
        info["format"] = im.format
        info["mode"] = im.mode
        info["width"], info["height"] = im.size
        for k, v in (im.info or {}).items():
            if isinstance(v, (str, int, float)) and k not in ("exif", "icc_profile"):
                info[f"info.{k}"] = str(v)[:500]
        exif = im.getexif()
        tags = {}
        for tag_id, val in exif.items():
            tags[ExifTags.TAGS.get(tag_id, str(tag_id))] = val
        try:
            for tag_id, val in exif.get_ifd(0x8769).items():  # Exif sub-IFD
                tags[ExifTags.TAGS.get(tag_id, str(tag_id))] = val
            gps = exif.get_ifd(0x8825)
            if gps:
                tags["GPSInfo"] = {ExifTags.GPSTAGS.get(k, str(k)): str(v) for k, v in gps.items()}
        except Exception:  # noqa: BLE001
            out.errors.append("Partial EXIF sub-IFD could not be parsed")
        q = getattr(im, "quantization", None)
        if q:
            # Estimate JPEG quality from the luminance table (IJG scaling)
            t0 = list(q.get(0, []))
            if len(t0) == 64:
                info["jpeg_quality_estimate"] = _ijg_quality(t0)
    for k in ("Make", "Model", "Software", "DateTime", "DateTimeOriginal", "DateTimeDigitized", "LensModel", "Artist", "ImageDescription", "OffsetTimeOriginal"):
        if k in tags:
            info[k] = str(tags[k]).strip("\x00 ")[:200]
    if "GPSInfo" in tags and isinstance(tags["GPSInfo"], dict):
        info["GPS"] = tags["GPSInfo"]
    return info


def _parse_exif_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _mp4_meta(path: Path) -> dict:
    """Scan the first/last MB for mvhd creation time and encoder strings (no full parse)."""
    info: dict = {}
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(min(size, 2 * 1024 * 1024))
        tail = b""
        if size > 2 * 1024 * 1024:
            f.seek(max(0, size - 2 * 1024 * 1024))
            tail = f.read()
    for blob in (head, tail):
        i = blob.find(b"mvhd")
        if i > 0 and "creation_time" not in info:
            ver = blob[i + 4]
            try:
                if ver == 1:
                    ct, mt = struct.unpack(">QQ", blob[i + 8: i + 24])
                    ts, dur = struct.unpack(">IQ", blob[i + 24: i + 36])
                else:
                    ct, mt, ts, dur = struct.unpack(">IIII", blob[i + 8: i + 24])
                if ct:
                    info["creation_time"] = (MP4_EPOCH + timedelta(seconds=ct)).isoformat()
                if mt:
                    info["modification_time"] = (MP4_EPOCH + timedelta(seconds=mt)).isoformat()
                if ts:
                    info["container_duration_s"] = round(dur / ts, 3)
            except struct.error:
                pass
        for pat in (rb"Lavf[\d.]+", rb"HandBrake[ \w.]+", rb"com\.apple\.quicktime\.(?:make|model|software)", rb"Android", rb"iPhone[ \w]*"):
            m = re.search(pat, blob)
            if m:
                info.setdefault("encoder_strings", [])
                s = m.group(0).decode("latin-1")[:60]
                if s not in info["encoder_strings"]:
                    info["encoder_strings"].append(s)
    return info


def _audio_meta(path: Path) -> dict:
    import soundfile as sf

    i = sf.info(str(path))
    info = {"samplerate": i.samplerate, "channels": i.channels, "duration_s": round(i.duration, 3), "format": i.format, "subtype": i.subtype}
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:3] == b"ID3":
        for tag in (b"TSSE", b"TENC", b"TIT2", b"TPE1"):
            j = head.find(tag)
            if j > 0:
                sz = int.from_bytes(head[j + 4: j + 8], "big")
                info[tag.decode()] = head[j + 11: j + 10 + min(sz, 80)].decode("latin-1", "replace").strip("\x00")
    m = re.search(rb"ISFT.{4}([ -~]{2,60})", head)
    if m:
        info["software"] = m.group(1).decode("latin-1")
    return info


def _scan_markers(path: Path) -> dict:
    with open(path, "rb") as f:
        blob = f.read(min(path.stat().st_size, 4 * 1024 * 1024)).lower()
    return {"c2pa_manifest": b"c2pa" in blob or b"jumb" in blob, "xmp_history": b"stevt:action" in blob or b"xmpmm:history" in blob,
            "generator_hits": [g for g in GENERATORS if g.encode() in blob]}


def analyze(path: str, evidence_id: str, media_type: str, file_info: dict) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    p = Path(path)
    with Timer() as t:
        meta: dict = {"size_bytes": p.stat().st_size, **{k: v for k, v in file_info.items() if v is not None}}
        try:
            if media_type == "image":
                meta.update(_exif(p, out))
            elif media_type == "video":
                meta.update(_mp4_meta(p))
            elif media_type == "audio":
                meta.update(_audio_meta(p))
            if media_type != "text":
                meta.update(_scan_markers(p))
        except Exception as e:  # noqa: BLE001
            out.errors.append(f"Metadata parsing error: {type(e).__name__}")
        for k, v in meta.items():
            out.observe(k, v)

        software = " ".join(str(meta.get(k, "")) for k in ("Software", "software", "TSSE", "TENC", "info.Software", "info.parameters")).lower()
        software += " " + " ".join(meta.get("encoder_strings", [])).lower()
        gen_hits = set(meta.get("generator_hits", [])) | {g for g in GENERATORS if g in software}
        if "info.parameters" in meta or "info.prompt" in meta or "info.workflow" in meta:
            gen_hits.add("embedded generation parameters")
        if gen_hits:
            out.indicators.append(enrich(Indicator("metadata", "generator_metadata", "medium",
                f"Metadata references generative tooling: {', '.join(sorted(gen_hits))}", location={"field": "software/text chunks"}, score=0.8)))
            out.signals.append(Signal("Generator reference in metadata", 0.8, 1.0, "suspicious"))
        editors = [e for e in EDITORS if e in software]
        if editors:
            out.indicators.append(enrich(Indicator("metadata", "editing_software_tag", "low",
                f"Processed with: {', '.join(editors)}", location={"field": "Software/encoder"}, score=0.4)))
            out.signals.append(Signal("Editing software recorded", 0.4, 0.5, "suspicious", "Editing is common and not necessarily deceptive"))
        if meta.get("xmp_history"):
            out.observe("XMP edit history present", True)
        if meta.get("c2pa_manifest"):
            out.observe("Content Credentials (C2PA) marker", "present – signature NOT verified by this tool")

        if media_type == "image":
            fmt = meta.get("format")
            if fmt == "JPEG" and not meta.get("Make") and not meta.get("DateTimeOriginal"):
                out.indicators.append(enrich(Indicator("metadata", "missing_camera_metadata", "info",
                    "No camera make/model or capture time in a JPEG", location={"field": "EXIF"}, score=0.2)))
                out.signals.append(Signal("Camera metadata absent", 0.2, 0.3, "suspicious", "Very common after social-media upload"))
            elif meta.get("Make"):
                out.signals.append(Signal("Camera metadata present", 0.0, 0.3, "reassuring", "Metadata can be forged; weak reassurance only"))
            dto, dt = _parse_exif_dt(meta.get("DateTimeOriginal")), _parse_exif_dt(meta.get("DateTime"))
            if dto and dt and abs((dt - dto).total_seconds()) > 120:
                out.indicators.append(enrich(Indicator("metadata", "timestamp_mismatch", "low",
                    f"Modify time ({dt}) differs from capture time ({dto}) – file was re-saved after capture",
                    location={"field": "DateTime vs DateTimeOriginal"}, score=0.35)))
                out.signals.append(Signal("Re-save after capture", 0.35, 0.4, "suspicious"))
        if media_type == "video" and meta.get("creation_time", "").startswith("1904"):
            out.observe("Creation time", "zeroed – typical of re-encoding")

        for note in file_info.get("content_notes") or []:
            out.indicators.append(enrich(Indicator("metadata", "extension_mismatch", "low", note, location={"field": "file name / extension"}, score=0.3)))
            out.signals.append(Signal("File extension does not match content", 0.3, 0.5, "suspicious", "Often innocent (renamed or mis-saved files)"))
        if file_info.get("detected_content"):
            out.observe("Content detected from file signature", file_info["detected_content"])
        out.suspicion_score = aggregate_signals(out.signals)
        out.confidence = 0.5
        out.limitations += ["Metadata is easily stripped, altered or forged; it is never treated as proof on its own.",
                            "C2PA markers are detected but cryptographic signatures are not verified.",
                            "Container parsing is partial (first/last 2 MB scanned for video atoms)."]
        out.interpretation = (f"{len(out.indicators)} metadata observation(s) worth noting." if out.indicators
                              else "No notable metadata anomalies (absence of anomalies is not evidence of authenticity).")
        out.artifacts = {"metadata": {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool, dict, list))}}
    out.duration_ms = t.ms
    return out.to_dict()
