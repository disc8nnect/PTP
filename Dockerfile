# PTP in a container. The app needs only Python; faster-whisper is added so recording works.
# Smaller image without recording (paste transcripts only):  docker build --build-arg STT=false .
# Usually run through compose.yaml, which also starts the local language model (Ollama).
FROM python:3.13.15-slim-bookworm

ARG STT=true
RUN if [ "$STT" = "true" ]; then pip install --no-cache-dir faster-whisper==1.2.1; fi

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PTP_HOST=0.0.0.0 \
    PTP_PORT=8765 \
    PTP_STATE_DIR=/data/state \
    HF_HOME=/data/models \
    HF_HUB_OFFLINE=1

WORKDIR /app
COPY ptp ./ptp
COPY web ./web
COPY data ./data
COPY tests ./tests
COPY demo_check.py compare_models.py ./

# Profile, tasks, recordings (/data/state) and the downloaded speech model (/data/models) live in
# /data, so they survive rebuilds when it is a volume. Runs as a normal user, not root.
# HF_HUB_OFFLINE=1: the speech model is only ever loaded from /data/models, never fetched while
# recording; compose.yaml's whisper-download service turns it off once to download the model.
RUN useradd --create-home --uid 1000 ptp && mkdir -p /data/state /data/models && chown -R ptp /data
USER ptp

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"
CMD ["python", "-m", "ptp.server"]
