# Unstream API — FastAPI + yt-dlp + ffmpeg
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ffmpeg for audio conversion, dumb-init for clean signal handling
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg dumb-init \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first so this layer caches across code changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

RUN useradd -u 1001 -m appuser \
    && mkdir -p /app/downloads /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["dumb-init", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
