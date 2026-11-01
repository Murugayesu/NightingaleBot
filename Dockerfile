FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY pyproject.toml uv.lock README.md ./
RUN uv venv && uv sync --no-dev --extra parsers --no-install-project

COPY src ./src
RUN uv sync --no-dev --extra parsers

COPY . .

EXPOSE 8000
