FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 truthlens
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=truthlens:truthlens . .
RUN mkdir -p /data && chown truthlens:truthlens /data
USER truthlens
EXPOSE 8000
# Apply migrations, then serve. Media decoding runs in isolated worker processes.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
