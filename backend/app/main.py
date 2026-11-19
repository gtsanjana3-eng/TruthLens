"""TruthLens AI – FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .database import init_schema
from .routers import analysis, auth, cases, evidence
from .services import isolation, jobs

log = logging.getLogger("truthlens")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.ensure_dirs()
    # Creates missing tables on SQLite and PostgreSQL/Supabase alike (and enables RLS on Supabase).
    init_schema()
    if s.app_env != "test":
        n = jobs.recover_interrupted()
        if n:
            log.info("Recovered %d interrupted job(s)", n)
    yield
    jobs.shutdown()
    isolation.shutdown()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="TruthLens AI", version="1.0.0", lifespan=lifespan,
                  description="Multimodal digital forensics and deepfake-indicator analysis. Results are indicators for human review, not verdicts.",
                  docs_url="/api/docs" if s.app_env != "production" else None, openapi_url="/api/openapi.json" if s.app_env != "production" else None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"],
                       expose_headers=["Content-Disposition", "X-Report-SHA256"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "Validation failed", "errors": errors})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # never leak internals
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    for r in (auth.router, cases.router, evidence.router, analysis.router):
        app.include_router(r)

    @app.get("/api/health")
    def health() -> dict:
        from .agents.video_agent import ffmpeg_exe

        return {"status": "ok", "ffmpeg": bool(ffmpeg_exe()), "process_isolation": s.process_isolation}

    @app.get("/api/capabilities")
    def capabilities() -> dict:
        """Which analyses are available – the UI shows unavailable ones explicitly."""
        from .agents import ml_agent

        agents = ["Image Forensics Agent", "Video Analysis Agent", "Audio Analysis Agent", "Text Analysis Agent",
                  "Metadata Verification Agent", "Lead Investigation Agent"]
        ml_ok, ml_reason = ml_agent.dependencies_available()
        models = []
        for media, mid, rev in (("video", s.hf_video_model, s.hf_video_model_revision), ("image", s.hf_image_model, s.hf_image_model_revision),
                                ("audio", s.hf_audio_model, s.hf_audio_model_revision), ("text", s.hf_text_model, s.hf_text_model_revision)):
            v = ml_agent.validation_for(mid, media) if mid else None
            status = "unavailable" if not (ml_ok and s.ml_enabled and mid) else "decision-grade" if v and v["decision_grade"] else "reference only"
            models.append({"media": media, "name": ml_agent.ROLE[media], "model": mid or None, "status": status,
                           "roc_auc": v["roc_auc"] if v else None,
                           "detail": "" if mid and ml_ok else (ml_reason if not ml_ok else "no model configured"),
                           "url": f"https://huggingface.co/{mid}/tree/{rev}" if mid else None})
        return {"agents": [{"name": n, "status": "available"} for n in agents], "learned_models": models,
                "limits": {"image_mb": s.max_image_mb, "video_mb": s.max_video_mb, "audio_mb": s.max_audio_mb, "text_mb": s.max_text_mb,
                           "video_seconds": s.max_video_seconds, "audio_seconds": s.max_audio_seconds}}

    return app


app = create_app()
