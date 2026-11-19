"""Secure evidence storage: validation (extension + declared MIME + magic bytes),
size limits, safe paths, SHA-256 hashing and write-once original preservation."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from .config import get_settings
from .models import MediaType

CHUNK = 1024 * 1024


class ValidationError(ValueError):
    """Raised when an upload is rejected. Message is safe to show to users."""


@dataclass(frozen=True)
class TypeRule:
    media: MediaType
    mimes: frozenset[str]


EXTENSIONS: dict[str, TypeRule] = {
    ".jpg": TypeRule(MediaType.image, frozenset({"image/jpeg"})),
    ".jpeg": TypeRule(MediaType.image, frozenset({"image/jpeg"})),
    ".png": TypeRule(MediaType.image, frozenset({"image/png"})),
    ".webp": TypeRule(MediaType.image, frozenset({"image/webp"})),
    ".tif": TypeRule(MediaType.image, frozenset({"image/tiff"})),
    ".tiff": TypeRule(MediaType.image, frozenset({"image/tiff"})),
    ".bmp": TypeRule(MediaType.image, frozenset({"image/bmp", "image/x-ms-bmp"})),
    ".mp4": TypeRule(MediaType.video, frozenset({"video/mp4"})),
    ".m4v": TypeRule(MediaType.video, frozenset({"video/mp4", "video/x-m4v"})),
    ".mov": TypeRule(MediaType.video, frozenset({"video/quicktime"})),
    ".avi": TypeRule(MediaType.video, frozenset({"video/x-msvideo", "video/avi", "video/msvideo"})),
    ".webm": TypeRule(MediaType.video, frozenset({"video/webm"})),
    ".mkv": TypeRule(MediaType.video, frozenset({"video/x-matroska"})),
    ".3gp": TypeRule(MediaType.video, frozenset({"video/3gpp", "audio/3gpp"})),
    ".3g2": TypeRule(MediaType.video, frozenset({"video/3gpp2", "audio/3gpp2"})),
    ".wmv": TypeRule(MediaType.video, frozenset({"video/x-ms-wmv", "video/x-ms-asf"})),
    ".mpg": TypeRule(MediaType.video, frozenset({"video/mpeg"})),
    ".mpeg": TypeRule(MediaType.video, frozenset({"video/mpeg"})),
    ".wav": TypeRule(MediaType.audio, frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"})),
    ".flac": TypeRule(MediaType.audio, frozenset({"audio/flac", "audio/x-flac"})),
    ".ogg": TypeRule(MediaType.audio, frozenset({"audio/ogg", "application/ogg"})),
    ".mp3": TypeRule(MediaType.audio, frozenset({"audio/mpeg", "audio/mp3"})),
    ".m4a": TypeRule(MediaType.audio, frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac"})),
    ".aac": TypeRule(MediaType.audio, frozenset({"audio/aac", "audio/x-aac", "audio/aacp"})),
    ".opus": TypeRule(MediaType.audio, frozenset({"audio/opus", "audio/ogg"})),
    ".oga": TypeRule(MediaType.audio, frozenset({"audio/ogg"})),
    ".weba": TypeRule(MediaType.audio, frozenset({"audio/webm"})),
    ".wma": TypeRule(MediaType.audio, frozenset({"audio/x-ms-wma"})),
    ".amr": TypeRule(MediaType.audio, frozenset({"audio/amr", "audio/3gpp"})),
    ".txt": TypeRule(MediaType.text, frozenset({"text/plain"})),
    ".md": TypeRule(MediaType.text, frozenset({"text/markdown", "text/plain", "text/x-markdown"})),
    ".srt": TypeRule(MediaType.text, frozenset({"application/x-subrip", "text/plain", "text/srt"})),
    ".vtt": TypeRule(MediaType.text, frozenset({"text/vtt", "text/plain"})),
}
GENERIC_MIMES = {"application/octet-stream", ""}  # some browsers send these; signature check still applies
# Browsers/OSes disagree on exact media MIME names (e.g. .mp4 as audio/mp4, .webm as audio/webm), so a
# declared type is accepted if it is in the same family. The file *signature* check is the real gate.
MIME_FAMILIES = {MediaType.image: ("image/",), MediaType.video: ("video/", "audio/"), MediaType.audio: ("audio/", "video/"), MediaType.text: ("text/",)}


def sniff_signature(head: bytes) -> str | None:
    """Return a canonical container type from leading magic bytes."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:2] == b"BM":
        return "bmp"
    if head[4:8] == b"ftyp":
        return "isobmff"  # mp4 / mov / m4v
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"  # mkv / webm
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:6] == b"#!AMR\n" or head[:9] == b"#!AMR-WB\n":
        return "amr"
    if head[:16] == bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C"):
        return "asf"  # wmv / wma
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "mpeg_ps"
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
        return "adts"  # raw AAC (layer bits 00 distinguish it from MP3 frames)
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    return None


SIGNATURE_FOR_EXT = {
    ".jpg": {"jpeg"}, ".jpeg": {"jpeg"}, ".png": {"png"}, ".webp": {"webp"}, ".tif": {"tiff"}, ".tiff": {"tiff"},
    ".bmp": {"bmp"}, ".mp4": {"isobmff"}, ".m4v": {"isobmff"}, ".mov": {"isobmff"}, ".avi": {"avi"},
    ".webm": {"matroska"}, ".mkv": {"matroska"}, ".wav": {"wav"}, ".flac": {"flac"}, ".ogg": {"ogg"}, ".mp3": {"mp3"},
    ".3gp": {"isobmff"}, ".3g2": {"isobmff"}, ".wmv": {"asf"}, ".mpg": {"mpeg_ps"}, ".mpeg": {"mpeg_ps"},
    ".m4a": {"isobmff"}, ".aac": {"adts", "mp3"}, ".opus": {"ogg"}, ".oga": {"ogg"}, ".weba": {"matroska"}, ".wma": {"asf"}, ".amr": {"amr"},
}


def safe_display_name(name: str) -> str:
    """Sanitise the user-supplied filename for display/logging only (never used as a path)."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name).strip(" .")
    return (name or "upload")[:200]


def max_bytes_for(media: MediaType) -> int:
    s = get_settings()
    mb = {MediaType.image: s.max_image_mb, MediaType.video: s.max_video_mb, MediaType.audio: s.max_audio_mb, MediaType.text: s.max_text_mb,
          MediaType.other: s.max_video_mb}[media]
    return mb * 1024 * 1024


def classify(filename: str, declared_mime: str | None) -> tuple[str, TypeRule]:
    ext = Path(safe_display_name(filename)).suffix.lower()
    rule = EXTENSIONS.get(ext)
    if not rule:
        raise ValidationError(f"File type '{ext or 'none'}' is not supported")
    mime = (declared_mime or "").split(";")[0].strip().lower()
    if mime not in GENERIC_MIMES and mime not in rule.mimes and not mime.startswith(MIME_FAMILIES[rule.media]):
        raise ValidationError(f"Declared content type '{mime}' does not match the file extension '{ext}'")
    return ext, rule


def _validate_text(path: Path) -> None:
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValidationError("Text evidence contains binary data")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError("Text evidence must be UTF-8 encoded")


@dataclass
class StoredFile:
    relative_path: str
    sha256: str
    size: int
    media: MediaType
    mime: str
    ext: str
    notes: list[str] = field(default_factory=list)
    detected: str = ""


IMAGE_SIGS = {"jpeg", "png", "webp", "tiff", "bmp", "gif", "ico", "heif"}
AUDIO_SIGS = {"wav", "flac", "ogg", "mp3", "amr", "adts"}
VIDEO_SIGS = {"isobmff", "matroska", "avi", "mpeg_ps", "asf", "flv", "mpeg_ts"}
SIG_MIME = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "tiff": "image/tiff", "bmp": "image/bmp", "gif": "image/gif",
            "ico": "image/x-icon", "heif": "image/heif", "wav": "audio/wav", "flac": "audio/flac", "ogg": "audio/ogg", "mp3": "audio/mpeg",
            "amr": "audio/amr", "adts": "audio/aac", "isobmff": "video/mp4", "matroska": "video/x-matroska", "avi": "video/x-msvideo",
            "mpeg_ps": "video/mpeg", "asf": "video/x-ms-asf", "flv": "video/x-flv", "mpeg_ts": "video/mp2t"}


def _extra_signature(head: bytes) -> str | None:
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"avif"):
        return "heif"
    if head[:3] == b"FLV":
        return "flv"
    if len(head) > 188 and head[0] == 0x47 and head[188] == 0x47:
        return "mpeg_ts"
    return None


def _looks_like_text(path: Path) -> bool:
    data = path.read_bytes()[: 2 * 1024 * 1024]
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:  # a multi-byte character cut at the 2 MB boundary is still text
        return e.start >= len(data) - 4
    return True


def detect_content(path: Path, head: bytes) -> tuple[MediaType, str, str]:
    """Classify an upload by its CONTENT (never by its name): (media type, MIME, short description)."""
    sig = _extra_signature(head) or sniff_signature(head)
    if sig in IMAGE_SIGS:
        return MediaType.image, SIG_MIME[sig], f"{sig.upper()} image"
    if sig in AUDIO_SIGS:
        return MediaType.audio, SIG_MIME[sig], f"{sig.upper()} audio"
    if sig in VIDEO_SIGS:
        return MediaType.video, SIG_MIME[sig], f"{sig} media container"
    if _looks_like_text(path):
        return MediaType.text, "text/plain", "UTF-8 text"
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.verify()
            fmt = im.format or "image"
        return MediaType.image, f"image/{fmt.lower()}", f"{fmt} image"
    except Exception:  # noqa: BLE001 - not an image Pillow understands
        pass
    return MediaType.other, "application/octet-stream", "unrecognised binary data"


def store_upload(stream: BinaryIO, filename: str, declared_mime: str | None) -> StoredFile:
    """Accept ANY file: stream to a temp file while hashing (size-capped), classify it by content,
    note extension/content mismatches, then move it into content-addressed, read-only storage."""
    s = get_settings()
    s.ensure_dirs()
    name = safe_display_name(filename)
    ext = Path(name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", ext or ""):
        ext = ""
    hard_limit = max(max_bytes_for(m) for m in MediaType)
    root = Path(s.storage_dir).resolve()
    tmp_dir = root / ".incoming"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    head = b""
    fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > hard_limit:
                    raise ValidationError(f"File exceeds the {hard_limit // (1024 * 1024)} MB upload limit")
                if len(head) < 256:
                    head += chunk[: 256 - len(head)]
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise ValidationError("File is empty")
        media, mime, what = detect_content(tmp, head)
        if media == MediaType.text and size > max_bytes_for(MediaType.text):
            media, mime, what = MediaType.other, "text/plain", "large text file (stored, not analysed)"
        limit = max_bytes_for(media)
        if size > limit:
            raise ValidationError(f"File exceeds the {limit // (1024 * 1024)} MB limit for {media.value} evidence")
        notes: list[str] = []
        expected = EXTENSIONS.get(ext)
        if ext and expected and expected.media != media:
            notes.append(f"File name ends in '{ext}' (a {expected.media.value} type) but the content is {what}.")
        elif ext and expected and media != MediaType.text:
            sig = _extra_signature(head) or sniff_signature(head)
            if sig and sig not in SIGNATURE_FOR_EXT.get(ext, {sig}):
                notes.append(f"File name ends in '{ext}' but the content is {what}.")
        digest = h.hexdigest()
        # Content-addressed path: server-generated, never derived from user input (extension sanitised above).
        rel = Path(digest[:2]) / f"{digest}{ext or '.bin'}"
        dest = safe_join(root, rel.as_posix())
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            tmp.unlink()
        else:
            os.replace(tmp, dest)
            os.chmod(dest, stat.S_IREAD | stat.S_IRGRP)  # originals are write-protected
        return StoredFile(rel.as_posix(), digest, size, media, mime, ext or ".bin", notes, what)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def safe_join(root: Path, relative: str) -> Path:
    """Resolve a stored relative path, refusing anything escaping the root (path traversal)."""
    root = Path(root).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValidationError("Invalid storage path")
    return candidate


def evidence_path(relative: str) -> Path:
    p = safe_join(Path(get_settings().storage_dir), relative)
    if not p.exists():
        from . import remote_storage  # restore the original after a host restart (temporary disks)

        if remote_storage.pull(f"evidence/{relative}", p):
            os.chmod(p, stat.S_IREAD | stat.S_IRGRP)
    return p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def secure_delete(path: Path) -> None:
    """Best-effort overwrite then unlink (SSD wear-levelling means this is not a guarantee)."""
    if not path.exists():
        return
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    size = path.stat().st_size
    with open(path, "r+b") as f:
        remaining = size
        while remaining > 0:
            n = min(CHUNK, remaining)
            f.write(os.urandom(n))
            remaining -= n
        f.flush()
        os.fsync(f.fileno())
    path.unlink()
