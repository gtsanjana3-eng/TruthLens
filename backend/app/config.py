"""Application configuration loaded from environment variables / .env.

Secrets are never hardcoded: JWT_SECRET must be supplied in production
(APP_ENV=production refuses to start with the development default).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEV_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field("development", description="development | test | production")
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'truthlens.db').as_posix()}"
    jwt_secret: str = _DEV_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    storage_dir: Path = BACKEND_DIR / "data" / "evidence"
    preview_dir: Path = BACKEND_DIR / "data" / "previews"
    report_dir: Path = BACKEND_DIR / "data" / "reports"
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"
    # Optional durable file storage (Supabase Storage). Needed on hosts with a temporary disk, e.g. Hugging Face Spaces.
    supabase_url: str = ""            # https://<project-ref>.supabase.co
    supabase_service_key: str = ""    # server-side secret (service_role / secret key) – never sent to browsers
    supabase_bucket: str = "truthlens-files"

    # Upload limits
    max_image_mb: int = 25
    max_video_mb: int = 300
    max_audio_mb: int = 60
    max_text_mb: int = 2
    max_video_seconds: int = 600
    max_audio_seconds: int = 900

    # Processing
    process_isolation: bool = True  # run media analysis in a separate worker process
    analysis_timeout_seconds: int = 300
    worker_threads: int = 2
    video_max_sampled_frames: int = 240

    # Rate limiting (requests per window per client+route group)
    rate_limit_auth_per_minute: int = 20
    rate_limit_upload_per_minute: int = 30
    rate_limit_default_per_minute: int = 300

    # Retention
    retention_days: int = 365

    # Learned AI / deepfake classifiers (Hugging Face). Optional: requires requirements-ml.txt.
    # Revisions are pinned to exact commits; only safetensors weights are loaded; remote code is never executed.
    ml_enabled: bool = True
    # Face model A: Swin-tiny trained on DFDC face crops (selected by screening; see evaluation/screening_results.json).
    hf_video_model: str = "hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand50-aligned_unaugmentation"
    hf_video_model_revision: str = "5c9aa417b1e9118441877de06536b37b25019889"
    # YuNet face detector (opencv_zoo, MIT). sha256 is verified before use.
    yunet_path: Path = BACKEND_DIR / "data" / "models" / "yunet" / "face_detection_yunet_2023mar.onnx"
    yunet_sha256: str = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    # One validated model per media type (selection + measurements: evaluation/*_RESULTS.md). Empty = feature unavailable.
    hf_image_model: str = "haywoodsloan/ai-image-detector-deploy"                   # held-out AUC 0.95 (evaluation/IMAGE_RESULTS.md)
    hf_image_model_revision: str = "4ae1822603cb1ec980c217cfde1ef1b8e0477a16"
    hf_audio_model: str = "abhishtagatya/wav2vec2-base-960h-itw-deepfake"          # held-out AUC 0.86 (evaluation/AUDIO_RESULTS.md)
    hf_audio_model_revision: str = "cb2350dec8246c67a5924462659c1796214f019f"
    hf_text_model: str = "fakespot-ai/roberta-base-ai-text-detection-v1"          # held-out AUC 0.96 (evaluation/TEXT_RESULTS.md)
    hf_text_model_revision: str = "f9cdb14d1f8b105f597d80fa7b56f20c6ea0e9db"
    hf_cache_dir: Path = BACKEND_DIR / "data" / "models"
    hf_offline: bool = False  # set true after scripts/download_models.py to forbid network access at analysis time
    ml_video_frames: int = 24
    ml_timeout_seconds: int = 900  # first run includes the one-time model download

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        return normalise_database_url(v)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        if self.app_env == "production" and (self.jwt_secret == _DEV_SECRET or len(self.jwt_secret) < 32):
            raise ValueError("JWT_SECRET must be set to a strong value (>=32 chars) in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        for d in (self.storage_dir, self.preview_dir, self.report_dir):
            Path(d).mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)


def _encode_credentials(url: str) -> str:
    """Percent-encode the user/password so passwords may contain @ : / ? # % etc. as typed.

    A host name never contains '@', so the LAST '@' separates the credentials from host:port/database;
    the FIRST ':' in the credentials separates user from password (user names have no ':').
    Already-encoded values are left as they are (decode, then encode)."""
    scheme, sep, rest = url.partition("://")
    at = rest.rfind("@")
    if not sep or at == -1:
        return url
    creds, hostpart = rest[:at], rest[at + 1:]
    user, colon, password = creds.partition(":")

    def enc(s: str) -> str:
        return quote(unquote(s), safe="")

    return f"{scheme}://{enc(user)}{':' + enc(password) if colon else ''}@{hostpart}"


def normalise_database_url(url: str) -> str:
    """Accept connection strings exactly as Supabase (or any Postgres host) shows them.

    * postgres:// / postgresql://  -> postgresql+psycopg:// (the installed psycopg 3 driver)
    * Supabase hosts always use TLS: sslmode=require is added unless already specified.
    """
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
            break
    if url.startswith("postgresql"):
        url = _encode_credentials(url)
    if url.startswith("postgresql") and ("supabase.co" in url or "supabase.com" in url) and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
