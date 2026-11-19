# Deploying TruthLens AI

| Part | Host | Notes |
|---|---|---|
| Web app (frontend) | **Vercel** (free) | builds `frontend/` from this GitHub repo |
| API + AI models (backend) | **Hugging Face Spaces**, Docker, CPU basic (free, 16 GB RAM) | built from `backend/` |
| Database | **Supabase** PostgreSQL | tables are created automatically, row-level security on |
| Evidence files, reports | **Supabase Storage** (private bucket `truthlens-files`) | created automatically |

Secrets are only ever entered in each service's private settings – never committed.

## 1. Collect from Supabase
* **DATABASE_URL** – Connect → Session pooler URI (same value as `backend/.env`).
* **SUPABASE_URL** – `https://<project-ref>.supabase.co`.
* **SUPABASE_SERVICE_KEY** – Project Settings → API Keys → `service_role` / secret key (server-only secret).
* **JWT_SECRET** – a new random value: `python -c "import secrets;print(secrets.token_urlsafe(48))"`.

## 2. Backend on Hugging Face Spaces
1. huggingface.co → New Space → SDK **Docker** → template **Blank** → hardware **CPU basic** → visibility **Public**
   (the web app must reach it; secrets stay private).
2. Space → Settings → **Variables and secrets** → add the four values above as **Secrets**.
3. Push the backend folder as the Space's code (from the repository root):
   ```bash
   git remote add space https://huggingface.co/spaces/<hf-user>/<space-name>
   git push space `git subtree split --prefix backend main`:main --force
   ```
   When asked for a password, use a Hugging Face **access token** with write permission.
4. Wait for the build (first build ~10–15 min: it installs PyTorch and bakes in the models).
   Check `https://<hf-user>-<space-name>.hf.space/api/health` returns `{"status":"ok",...}`.

## 3. Frontend on Vercel
1. vercel.com → Add New → Project → import this GitHub repository.
2. **Root Directory**: `frontend`. Framework: Vite (auto-detected).
3. Environment variable `VITE_API_BASE` = `https://<hf-user>-<space-name>.hf.space`.
4. Deploy → you get `https://<project>.vercel.app`.

## 4. Connect them
Back in the Space → Settings → Variables and secrets → add variable
`CORS_ORIGINS` = your Vercel address (e.g. `https://truthlens.vercel.app`) → the Space restarts.

## Limits of the free tiers
* Analysis runs on CPU: a video takes roughly 20–60 s.
* A free Space sleeps after a period without visits; the first request then takes about a minute.
* Files and records persist in Supabase; the Space's own disk is only a cache.
