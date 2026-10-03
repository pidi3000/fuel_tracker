# syntax=docker/dockerfile:1

# --- Stage 1: build the web UI ---
FROM node:22-alpine AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Writes to /src/backend/static (see vite.config.ts)
RUN npm run build

# --- Stage 2: the app ---
FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.8.17

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY VERSION /app/VERSION
COPY backend/ ./
COPY --from=ui /src/backend/static ./static

# Set by the release workflow, e.g. 1.2.3 or 1.2.3-test+a1b2c3d
ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION} \
    PATH=/app/.venv/bin:$PATH \
    DATA_DIR=/data \
    FORWARDED_ALLOW_IPS=127.0.0.1

RUN useradd --system --uid 1000 --no-create-home app \
    && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# One worker only: background workers run inside the app process
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
