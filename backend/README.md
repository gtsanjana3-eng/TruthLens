---
title: TruthLens AI API
emoji: 🔍
colorFrom: blue
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Backend API for TruthLens multimodal deepfake forensics
---

# TruthLens AI – backend API

FastAPI backend for TruthLens: forensic agents for images, video, audio and text, four validated
AI detectors (CPU), Supabase/PostgreSQL storage and forensic PDF reports. The web interface is
deployed separately and calls this API.

Configuration is read from environment variables (Space **Settings → Variables and secrets**);
see `.env.example`. Required secrets: `DATABASE_URL`, `JWT_SECRET`, `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY`; variable: `CORS_ORIGINS` (the web app's address).

Health check: `/api/health`
