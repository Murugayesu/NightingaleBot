# NursingBot application image: used by the api, worker and migrate services.

FROM astral/uv:0.13.6 AS uv

FROM python:3.12.16-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /usr/local/bin/uv

# Unprivileged user: a compromise of the app must not mean root in the container.
# Code and dependencies stay owned by root, so the app user can read but never
# modify them (and no chown layer duplicates the whole virtualenv).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /tmp --shell /usr/sbin/nologin app

WORKDIR /app

# 1. Dependencies only (cached until pyproject.toml or uv.lock change).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra parsers --no-install-project --no-cache

# 2. The application, migrations and the evaluation set.
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY eval ./eval
RUN uv sync --frozen --no-dev --extra parsers --no-cache

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]

# --no-proxy-headers: the app itself decides which X-Forwarded-For hops to trust
# (TRUSTED_PROXY_NETWORKS in config.py); uvicorn must not rewrite the client address.
CMD ["uvicorn", "nursing_bot.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header"]
