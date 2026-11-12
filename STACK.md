# Stack

Minimum release age: 7 days. Versions here are authoritative; do not re-check them without a reason.
Python package pins live in `pyproject.toml` / `uv.lock`, JavaScript pins in `frontend/package.json` / `frontend/package-lock.json`; only versions added or verified since 2026-11-06 are listed.

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
| argon2-cffi | 25.1.0 | 2026-11-12 | PyPI | Password hashing (Argon2id). Last release 2025-06-03: a finished, maintained package (same author as argon2-cffi-bindings 26.1.0, released 2026-08-20). |
| Node.js (frontend build) | 24.22.3 (`node:24.22.3-alpine`) | 2026-11-12 | endoflife.date / Docker Hub | Build stage only. Line 24 matches local development (24.x); LTS, security fixes until 2028-04-30. 24.22.3 was 1 day old: a runtime security patch, taken deliberately. Line 26 (LTS, active) is the next upgrade. |
| react / react-dom | 19.3.1 | 2026-11-12 | npm | |
| react-router | 8.5.1 | 2026-11-12 | npm | Declarative mode (`BrowserRouter`). v8: `react-router-dom` is gone; import from `react-router`. |
| @tanstack/react-query | 5.106.0 | 2026-11-12 | npm | 5.106.1-5.107.1 skipped (< 7 days). |
| vite | 8.3.5 | 2026-11-12 | npm | 8.3.6-8.3.8 skipped (< 7 days). |
| @vitejs/plugin-react | 6.2.1 | 2026-11-12 | npm | 6.3.0 skipped (< 7 days). |
| typescript | 6.0.3 | 2026-11-12 | npm | 7.0.2 exists but typescript-eslint 8.72 supports TypeScript < 6.1. |
| vitest | 5.1.0 | 2026-11-12 | npm | |
| jsdom | 29.1.1 | 2026-11-12 | npm | 30.x requires Node >= 24.15 on the 24 line; local development runs 24.14. |
| @testing-library/react / dom / user-event / jest-dom | 16.3.3 / 10.4.2 / 14.6.8 / 7.0.1 | 2026-11-12 | npm | user-event 14.6.9 skipped (< 7 days). |
| fake-indexeddb | 6.2.7 | 2026-11-12 | npm | Tests only. 6.2.8 skipped (< 7 days). |
| @playwright/test | 1.64.1 | 2026-11-12 | npm | 1.65.0 skipped (< 7 days). Browser: Chromium headless shell 156. |
| eslint / @eslint/js | 10.14.0 / 10.0.1 | 2026-11-12 | npm | Accepted advisory: minimatch 10.2.6 (moderate ReDoS, GHSA-g3hv-75wg-m249) reaches only ESLint, a dev tool matching our own patterns. Fixed minimatch 11 is a new major ESLint does not declare; revisit when ESLint updates. CI fails only on high or critical. |
| typescript-eslint | 8.72.1 | 2026-11-12 | npm | 8.72.2 skipped (< 7 days). |
| eslint-plugin-react-hooks | 7.1.1 | 2026-11-12 | npm | |
| globals | 17.14.0 | 2026-11-12 | npm | |
| @types/react / @types/react-dom / @types/node | 19.3.0 / 19.3.0 / 24.19.2 | 2026-11-12 | npm | @types/node follows the Node 24 line. |
| actions/setup-node | v7.1.0 (949feb2413d6458794dcd2491c4babbbce0c15c1) | 2026-11-12 | GitHub | |
| actions/upload-artifact | v7.0.2 (cf430e030ddbb5b0abf93d22962f4752f3646cd9) | 2026-11-12 | GitHub | CI e2e report on failure. |
