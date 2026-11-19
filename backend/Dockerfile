# TruthLens AI backend – works on Hugging Face Spaces (Docker SDK) and with docker-compose.
# CPU-only PyTorch; the four validated models and the YuNet face detector are baked into the image,
# so the running container never downloads model code or weights.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HOME=/home/user PORT=7860 APP_ENV=production \
    STORAGE_DIR=/home/user/data/evidence PREVIEW_DIR=/home/user/data/previews REPORT_DIR=/home/user/data/reports \
    HF_CACHE_DIR=/home/user/models YUNET_PATH=/home/user/models/yunet/face_detection_yunet_2023mar.onnx

RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libsndfile1 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 user
WORKDIR /home/user/app

# Python dependencies (torch from the CPU index: avoids ~2.5 GB of unused CUDA libraries)
COPY --chown=user requirements.txt requirements-ml.txt ./
RUN pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt -r requirements-ml.txt

USER user
RUN mkdir -p /home/user/data /home/user/models/yunet \
 && curl -fsSL -o "$YUNET_PATH" https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
 && echo "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  $YUNET_PATH" | sha256sum -c -

COPY --chown=user . .
# Download + smoke-test the pinned model revisions at build time; forbid network model access at runtime.
RUN JWT_SECRET=build-only-placeholder-not-used-at-runtime-000000 APP_ENV=development python -m scripts.download_models
ENV HF_OFFLINE=true

EXPOSE 7860
# Tables are created on start-up (idempotent; row-level security enabled on Supabase).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
