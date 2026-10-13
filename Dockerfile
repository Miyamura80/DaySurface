FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    DEV_ENV=prod \
    PORT=8080

# loguru reads these when first imported, so they govern its default handler for
# the import-time logs emitted before setup_logging() replaces it: no DEBUG lines
# and no diagnose/backtrace locals in production from the very first log line.
ENV LOGURU_LEVEL=INFO \
    LOGURU_DIAGNOSE=NO \
    LOGURU_BACKTRACE=NO

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "exec uv run uvicorn api_server.server:app --host 0.0.0.0 --port ${PORT}"]
