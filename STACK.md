# Stack

Minimum release age: 7 days. Versions here are authoritative; do not re-check them without a reason.
Python package pins live in `pyproject.toml` / `uv.lock`; only versions added or verified on 2026-11-06 are listed.

| Technology | Version | Checked | Source | Notes |
|---|---|---|---|---|
| Python (runtime line) | 3.12 | 2026-11-06 | endoflife.date | Kept on the installed line. Security fixes only; EOL 2028-10-31. 3.13–3.15 exist; upgrading is a separate decision. |
| Python (Docker base) | python:3.12.16-slim-trixie | 2026-11-06 | Docker Hub | 3.12.16 released 2026-10-25. CI uses uv's `3.12` (newest available build) because uv-managed builds can lag a patch. |
| uv | 0.13.6 | 2026-11-06 | PyPI / Docker Hub `astral/uv` | 0.13.7 skipped (3.5 days old). Local uv was 0.12.23; lockfile format is compatible. |
| opentelemetry-instrumentation-fastapi | 0.66b2 | 2026-11-06 | PyPI | Contrib instrumentations only publish `b` (beta) versions; pinned to match the installed opentelemetry-instrumentation 0.66b2 / SDK 1.45.2. |
| opentelemetry-instrumentation-sqlalchemy | 0.66b2 | 2026-11-06 | PyPI | Same as above. |
| mypy | 2.4.1 | 2026-11-06 | PyPI | Dev only. |
| ragas | not used | 2026-11-06 | PyPI | 0.4.3 (newest, 2026-01-13) fails to import with the pinned LangChain (`langchain_community.chat_models.vertexai` removed) and downgrades fsspec/jiter/rich. Equivalent metrics implemented in `evaluation/judge.py`. |
| pypdf | 6.21.4 | 2026-11-10 | PyPI | Upgraded from 6.21.2: 7 advisories (crafted PDF → excessive runtime/memory). |
| filelock (transitive) | 3.32.7 | 2026-11-10 | PyPI | 13 advisories, fixed in 4.2.1+, but litellm 1.106.5 requires `filelock<4`. Low exposure (needs a hostile local user). Accepted in `.github/pip-audit-ignore.txt`; remove when litellm allows filelock 4. |
| pgvector/pgvector image | 0.8.7-pg18-trixie | 2026-11-06 | Docker Hub | Already in use; unchanged. |
| otel/opentelemetry-collector-contrib | 0.164.0 | 2026-11-06 | GitHub releases / Docker Hub | |
| arizephoenix/phoenix | version-21.0.0 | 2026-11-06 | GitHub releases / Docker Hub | Only just past the 7-day wait (7.4 days). |
| caddy | 2.11.7-alpine | 2026-11-06 | GitHub releases / Docker Hub | 2.11.8 and 2.11.9 skipped (< 3 days old). |
| cgr.dev/chainguard/minio, minio-client | latest | 2026-11-06 | Chainguard | Free tier publishes `:latest` only (MinIO stopped community images). Pin by digest if you need reproducible builds. |
| actions/checkout | v7.0.1 (3d3c42e5aac5ba805825da76410c181273ba90b1) | 2026-11-06 | GitHub | |
| astral-sh/setup-uv | v10.3.3 (68559e99cc807c1eb7e9238fd43a15bba460eec2) | 2026-11-06 | GitHub | |
