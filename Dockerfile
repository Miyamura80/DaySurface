FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS base

# UV_COMPILE_BYTECODE: ship .pyc so no module is compiled at boot (faster start, and
# no SyntaxWarnings from third-party sources such as textblob on every boot).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    DEV_ENV=prod \
    PORT=8080

# loguru reads these when first imported, so they govern its default handler for
# the import-time logs emitted before setup_logging() replaces it: no DEBUG lines
# and no diagnose/backtrace locals in production from the very first log line.
ENV LOGURU_LEVEL=INFO \
    LOGURU_DIAGNOSE=NO \
    LOGURU_BACKTRACE=NO

# ca-certificates: listed explicitly so each build pulls the post-eMudhra trust store
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "uv>=0.13.4"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# The venv is complete, so stop `uv run` (here and in railway.json) from re-syncing
# it at start: that sync pulled in the dev group and printed ~6 stderr lines per boot.
ENV UV_NO_SYNC=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "exec uv run uvicorn api_server.server:app --host 0.0.0.0 --port ${PORT}"]
