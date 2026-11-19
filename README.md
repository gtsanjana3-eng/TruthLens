# TruthLens AI: Multimodal Digital Forensics & Deepfake-Indicator Analysis

INNOVEDGE Hackathon 2026, Problem Statement 04. A full-stack platform that analyses **image, video, audio and text**
evidence with coordinated forensic agents and correlates signals across media. Every indicator is presented with its
confidence, limitations and alternative explanations, and the original evidence is preserved for human review.

> **Indicators, not verdicts.** TruthLens never labels media "fake" or "genuine". Scores are uncalibrated heuristic
> indicators. Learned deepfake classifiers are **not bundled**, and the app marks them *unavailable* rather than faking
> their output.

## Architecture

```
frontend/  React 18 + TypeScript + Tailwind v4 (Vite)   ──/api──►  backend/  FastAPI + SQLAlchemy 2 + Alembic
                                                                   ├─ app/agents/      8 forensic agents + sync analyzer
                                                                   ├─ app/services/    orchestrator, job queue, isolated workers, PDF reports
                                                                   ├─ app/routers/     auth, cases, evidence/vault, analysis/reports/audit
                                                                   ├─ migrations/      Alembic (PostgreSQL / SQLite)
                                                                   ├─ evaluation/      synthetic dataset + metrics runner
                                                                   └─ scripts/         demo seeding, retention purge
```

**Analysis flow:** `POST /api/cases/{id}/analyze` creates a job. A thread-pool queue runs the **orchestrator**, which:

1. For each evidence item, runs the Metadata agent and the modality agent (Image, Video or Audio) in **isolated OS
   processes** with hard timeouts. Text is parsed by the Text agent.
2. For video, extracts the audio track with a bundled FFmpeg binary, runs the Audio agent on it, then runs the
   **Voice-Face Sync analyzer**.
3. Runs the **Cross-Modal Correlation agent**. It checks claims (dates, durations, devices, time of day, "not edited")
   against metadata, transcripts against speech activity, images against video frames (dHash), and audio against the
   video soundtrack.
4. Runs the **Evidence Review agent**, which assigns a rule-based concern level and uncertainty and preserves agent
   disagreements.
5. Runs the **Report Generation agent**, which builds the structured report outline.

Every agent result is stored separately: version, timestamp, observations (measured) kept apart from interpretation,
signals, limitations, errors, and learned-model status. Results are cached by content hash and agent version.

| Agent | Technique (all explainable, no hidden model) |
|---|---|
| Image Forensics | Contrast-normalised ELA, block noise-sigma consistency, SIFT copy-move clustering, spectral periodicity. The heatmap is built **only** from these measured maps. |
| Video Forensics | Streamed ≤10 fps sampling. Robust-z temporal discontinuities, duplicated frames, face-region sharpness consistency (Haar), mouth-motion series, frame thumbnails. |
| Audio Forensics | Splice discontinuities, noise-floor shifts, digital-silence runs, bandwidth cutoff, pitch spread and jitter, clipping, spectrogram, per-second anomaly timeline. |
| Text Analysis | SRT/VTT/plain parsing. Extracts dates, times, durations, locations, devices, time of day, speakers, persuasive wording and "not edited" claims. Checks for internal contradictions. |
| Metadata Analysis | EXIF (incl. GPS and sub-IFD), JPEG quality estimate, PNG text chunks (e.g. Stable Diffusion parameters), C2PA/XMP markers, MP4 `mvhd` times and encoder strings, audio container info. |
| Cross-Modal Correlation | See step 3 above. Outputs supporting, conflicting and uncertain relationships for the Evidence DNA graph. |
| Evidence Review | Concern levels need corroboration to escalate. Uncertainty rises when analyses are unavailable or failed, or when agents disagree. |
| Report Generation | Structured outline: flagged locations, recommended human checks. |
| Voice-Face Sync | Correlates mouth motion with speech energy, searches audio offsets, flags windows. Reports *inconclusive* when no face is tracked. |

## Quick start (local)

Requires Python 3.10+ and Node 20+. FFmpeg comes with `imageio-ffmpeg`.

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
```

```bash
cd backend && cp .env.example .env
```

Edit `JWT_SECRET` in `.env`. SQLite is used unless `DATABASE_URL` points at PostgreSQL.

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. **The first account you register becomes administrator.**
API docs: http://localhost:8000/api/docs. (On macOS/Linux use `.venv/bin/` instead of `.venv/Scripts/`.)

### Demo data

```bash
cd backend && .venv/Scripts/python -m scripts.seed_demo
```

This creates a demo account (credentials in `backend/scripts/seed_demo.py`, local use only) and a case with
synthetic image, video, audio and transcript evidence. It then runs the full analysis and generates a PDF report.

## Database, schema and migrations

There are 13 tables: `users`, `investigation_cases`, `case_members`, `evidence_files`, `file_hashes`, `analysis_jobs`,
`agent_results`, `manipulation_indicators`, `cross_modal_findings`, `evidence_relationships`, `investigation_notes`,
`forensic_reports` and `audit_logs`. They have UUID/serial primary keys, foreign keys with cascade rules, indexes on
lookup columns, created/updated timestamps and status enums. Media is **never stored in the database**: originals live
on disk, content-addressed by SHA-256 and write-protected, and the database holds references to them.

```bash
cd backend && DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/truthlens .venv/Scripts/alembic upgrade head
```

To create a new migration after changing models:

```bash
cd backend && .venv/Scripts/alembic revision --autogenerate -m "describe change"
```

With SQLite in development, tables are also created automatically on startup.

## Deployment (Docker)

```bash
POSTGRES_PASSWORD=change-me JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))") docker compose up --build
```

This starts PostgreSQL 16, the backend (runs `alembic upgrade head` then uvicorn, as a non-root user) and nginx serving
the built frontend on http://localhost:8080 with a strict CSP. Schedule the retention purge daily:

```bash
cd backend && .venv/Scripts/python -m scripts.retention --dry-run
```

**Scaling note:** the job queue is in-process (`app/services/jobs.py`) and the rate limiter is in-memory. For several
API replicas, swap both for Redis-backed equivalents (Celery/RQ and a Redis limiter). `jobs.enqueue` is the only seam
that needs changing.

## Security controls

* bcrypt (cost 12) password hashing, a password policy, generic login errors with timing equalisation, and a 5-attempt
  lockout.
* JWT (HS256) sent only in the `Authorization` header. Production refuses to start with a weak secret.
* Roles: admin, investigator, reviewer. Object-level access control returns 404 for cases you can't see. Shared members
  are read-only unless granted edit rights.
* Uploads are checked against an extension allow-list, the declared MIME type **and** magic-byte signatures. Size and
  duration limits apply, text must be UTF-8 with no NUL bytes, filenames are sanitised, storage paths are generated by
  the server, and `safe_join` blocks path traversal.
* Untrusted media is decoded in **separate worker processes** with timeouts, and FFmpeg is invoked with an argument
  list (no shell). Text evidence is never executed or treated as instructions.
* Pydantic validation, ORM-parameterised queries, a sliding-window rate limiter (auth, upload, default), and a CORS
  allow-list.
* Security headers are set. Originals are served with `Content-Security-Policy: sandbox` and as downloads.
* Errors never expose internals: a generic 500 response, and validation messages broken down by field.
* A **hash-chained audit log** (`GET /api/audit/verify`), upload and verification hashes, soft deletion, and a
  retention purge with overwrite-then-unlink.

## Testing

```bash
cd backend && .venv/Scripts/python -m pytest
```

```bash
cd frontend && npm test
```

```bash
cd backend && .venv/Scripts/python -m evaluation.run_eval --pairs 12
```

* **Backend: 46 pytest tests.** They cover auth and authorization, lockout, rate limiting, token tampering,
  audit-chain tampering, the production secret check, upload validation, path traversal, read-only storage, the
  integrity vault, retention and secure deletion, unit tests for every agent on known manipulations, corrupt and
  short media, cross-modal logic, review-agent wording, sync analysis, isolated-worker timeout and recovery, failed
  agents being surfaced, and a **full end-to-end workflow** (register, case, four media types, analyze, review note,
  PDF with hash check and section text, caching, history).
* **Frontend: 17 Vitest + Testing Library tests.** They cover accessible primitives, chart text alternatives, the API
  client (token header, errors), routing guards, form validation, login, case creation and the indicator review flow.
* **Evaluation:** see `backend/evaluation/RESULTS.md` (measured) and `backend/evaluation/README.md` (method and
  limits).

## Hackathon demo workflow (≈5 min)

1. Run `scripts.seed_demo`, then sign in. The dashboard shows the case, charts and recent activity.
2. **Overview:** the claimed context, five evidence items, triage per item and reviewer notes.
3. **Image:** switch Original / Suspicion heatmap / ELA, see the copy-move box, open *alternative explanations*, and
   add a review note with a verdict.
4. **Video:** step through frames with the arrow keys, jump to the next flagged frame, click the timeline, and see the
   voice-face sync result (*inconclusive* on the synthetic clip because no face is visible).
5. **Audio:** click flagged segments to play them, then view the anomaly timeline and spectrogram.
6. **Text:** the transcript cues and extracted claims. Then **Evidence graph** shows conflicts such as "60-second" vs
   an 8 s clip.
7. **Confidence & findings:** the per-evidence breakdown, preserved disagreements, unavailable models, and the
   multi-agent room.
8. **Integrity vault:** re-verify a hash. **Timeline:** the audit trail. **Report:** generate and download the PDF,
   whose SHA-256 is shown.

## The nine features and the models behind them

| # | Feature | Implementation |
|---|---|---|
| 1 | Image forensics | `haywoodsloan/ai-image-detector-deploy` (Swin-v2, Apache-2.0) |
| 2 | Video deepfake detection | YuNet face detector + `hchcsuim/…DFDC…faces-expand50` (Swin-tiny, Apache-2.0) on face crops from 24 frames, plus temporal and voice-face sync checks |
| 3 | AI voice detection | `abhishtagatya/wav2vec2-base-960h-itw-deepfake` (wav2vec2, trained on In-the-Wild deepfakes) on 4 s windows |
| 4 | AI text analysis | `fakespot-ai/roberta-base-ai-text-detection-v1` (RoBERTa, Apache-2.0), plus claim extraction and comparison with the media |
| 5 | Image manipulation analysis | OpenCV: ELA, noise consistency, SIFT copy-move, heatmap (indicators only) |
| 6 | Metadata & authenticity | Pillow EXIF, MP4 atoms, C2PA markers, SHA-256 at upload and on demand |
| 7 | Multi-agent investigation | Image Forensics, Video Analysis, Audio Analysis, Text Analysis and Metadata Verification agents feed a Lead Investigation Agent (cross-modal correlation) |
| 8 | Forensic reports | PDF report with history and audit trail |
| 9 | Human review | Accept / Reject / Needs further review with comments, stored separately from the AI output |

Each model was chosen by screening 2 candidates on a screening split, then measured **once** on held-out labelled data
(`python -m evaluation.eval_modality …`, video: `run_video_eval.py`):

| Model | Held-out data | ROC-AUC | When it says "likely AI" | When it says "no indication" |
|---|---|---|---|---|
| Image | 200 images (`Parveshiiii/AI-vs-Real`) | **0.95** | right 93/110 (85%) | right 83/90 |
| Video | 80 videos (DFD face swaps) | **0.85** | right 15/15 | right 28/37 |
| Voice | 200 clips (`garystafford/deepfake-audio-detection`) | **0.86** | right 12/12 | right 99/182 (low – misses many clones) |
| Text | 206 texts (`textsightai/…-2026`) | **0.96** | right 54/90 (low – human ESL/academic text gets flagged) | right 75/75 |

The app's **confidence score** is exactly these measured hit-rates (with 95% ranges), never a raw model output.

Removed (measured near chance or worse than the chosen model): prithivMLmods Deep-Fake-Detector-v2 (0.50),
dima806 deepfake (0.48), Organika sdxl-detector (0.53), CommunityForensics (0.46), MelodyMachine voice (0.52),
OpenAI RoBERTa GPT-2 detector (0.45), Ateeqq image (more false positives), hchcsuim expand30 (lower screening AUC).

Setup (CPU only):

```bash
cd backend && .venv/Scripts/pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu && .venv/Scripts/pip install -r requirements-ml.txt
```

```bash
cd backend && .venv/Scripts/python -m scripts.download_models
```

## Plugging in other learned models

Each agent reports a `ModelCheck` with status `unavailable` for its learned detector. To add one (for example an ONNX
face-swap classifier), call it inside the agent's `analyze()`. Append its score as a `Signal`, record the model
name/version in a `ModelCheck(status="completed")`, add its limitations, and evaluate it with `run_eval.py` on a
licensed benchmark before trusting it.

## Known limitations

* Classical detectors cannot reliably catch high-quality deepfakes, diffusion images or neural voice clones.
* The evaluation is synthetic. Real-world accuracy has **not** been measured.
* PostgreSQL support is implemented and the migration renders for it, but the automated test run used SQLite (no
  PostgreSQL server was available). Docker files were written but not built in this environment.
* Face and mouth tracking uses Haar cascades (near-frontal faces only).
* C2PA manifests are detected but signatures are not verified.

See `SCORE_READINESS.md` for the evidence-based checklist.
