# Operations runbook

## First deployment

1. Install Docker on the VM and point the domain's DNS at it.
2. `cp .env.example .env`, then set:
   - secrets from `docker compose run --rm --no-deps migrate python -m nursing_bot.cli gen-secrets`
   - `ENVIRONMENT=production`, `SITE_ADDRESS=<domain>`, `ACME_EMAIL`
   - `LLM_API_KEY` (and `EMBEDDING_API_KEY` / `RERANK_API_KEY` if used)
   - `WIDGET_FRAME_ANCESTORS=https://<council website>` (plain origins, comma-separated, no quotes)
3. `docker compose up -d --build`. The `migrate` service applies migrations and creates the job-queue tables before `api` and `worker` start. In production the API refuses to start with default secrets.
4. Load documents (admin panel → Upload, or `cli ingest-dir`), then run `cli eval retrieval` and set `GATE_THRESHOLD_SIMILARITY` to the suggested value.

### Upgrading an existing installation (from 0.2.x)

- Add `POSTGRES_PASSWORD` to `.env`. Use the password the existing volume was created with (`postgres` for earlier dev setups), then rotate it:
  `docker compose exec postgres psql -U postgres -c "ALTER USER postgres PASSWORD '<new>'"`, update `.env`, and run `docker compose up -d`.
- Migration 003 makes `documents.content_hash` unique. If you uploaded the same file twice, it stops and prints a query listing the duplicates to delete.
- Page numbers become 1-based. Existing chunks are shifted by the migration.
- After upgrading, press **Maintenance → Re-index all documents** in the admin panel once. That applies the new chunk size and headers.

## Secrets with Docker secrets (instead of .env)

Any setting can be a file in `/run/secrets/<SETTING_NAME>`. For example, in a compose override:

```yaml
services:
  api:
    secrets: [ADMIN_API_KEY, SESSION_SECRET, LLM_API_KEY]
secrets:
  ADMIN_API_KEY: { file: ./secrets/admin_api_key.txt }
```

## Backups

- `backup` takes a `pg_dump` every `BACKUP_INTERVAL_SECONDS` (default daily) into the `backups` volume and keeps `BACKUP_RETENTION_DAYS` days.
- `backup-sync` mirrors that volume into the `nursing-backups` bucket. For off-site safety, point `S3_*` at a different provider or add a bucket replication rule.
- **Test the restore monthly:** `docker compose run --rm --entrypoint /bin/sh backup /scripts/restore-test.sh`.
- **Restore for real:**
  ```bash
  docker compose stop api worker
  docker compose run --rm --entrypoint /bin/sh backup /scripts/restore.sh /backups/<file>.dump
  docker compose start api worker
  ```

## Monitoring

- `GET /api/v1/admin/health` (admin key) reports database, storage, queue and stale-embedding status. It always returns 200; read `status`.
- Phoenix (`http://localhost:6006`, reach it through an SSH tunnel) shows every request: retrieval scores, prompts (PII-redacted) and token use.
- Logs: `docker compose logs -f api worker` (set `LOG_FORMAT=json` for log shippers).

## Changing models

- Changing `LLM_MODEL` takes effect on restart.
- Changing `EMBEDDING_MODEL`: dense search ignores chunks from the old model, so run **Re-index all** right away. `stale_embedding_chunks` in the health check shows progress.

## AI provider settings learned from live testing

- **Pin the chat model.** `LLM_MODEL=auto` lets a routing gateway pick a different model for every
  request. In testing, the same question was answered correctly by some routed models and wrongly
  ("not available in the records") by others. Use one specific, capable model, e.g.
  `LLM_MODEL=gemini-3.5-flash` behind the gateway, and re-run `cli eval answer` after any change.
- **Embedding model must never change silently.** Dense search only compares vectors from the model
  recorded on each chunk. `auto` is acceptable only if the gateway maps it to one fixed embedding
  family (yours maps to `gemini-embedding-001`). After a deliberate change: Maintenance → Re-index all.
- **Free-tier quotas.** Free embedding tiers have per-minute *and* daily limits. Ingestion waits out
  short limits (`EMBEDDING_RATE_LIMIT_RETRIES`), but a daily limit stops it until the reset
  (midnight Pacific for Gemini). Budget roughly one embedding call per 16–64 chunks; a 1,500-row
  spreadsheet is 1,500 chunks. For free tiers set `WORKER_CONCURRENCY=1` and `EMBEDDING_BATCH_SIZE=16`.
  For the full corpus (~20,000 chunks) use a paid embedding key or a local embedding model.
- **Provider outages** return HTTP 503 (`Retry-After: 30`) from the API and a friendly message in the
  widget; failed ingestions show the provider error in the admin panel and can be re-ingested.

## Worker reliability

- Jobs of a worker that died (crash, redeploy, reboot) are re-queued automatically within 5 minutes
  (`retry_stalled_jobs_task`). Documents never stay in "processing" forever.
- Deleting a document cancels its pending work: queued jobs skip it and a running ingestion stops at
  the next embedding batch.
- Inline mode (no worker): run `python -m nursing_bot.cli purge-history` daily from cron to enforce
  `CHAT_HISTORY_RETENTION_DAYS`.

## Edge and observability settings

- Behind a cloud load balancer or CDN, set `CADDY_TRUSTED_PROXIES` to its CIDR ranges, otherwise all
  users share one rate-limit bucket. Never set it to ranges your users can connect from.
- Phoenix login is off by default (Phoenix is bound to 127.0.0.1). To enable it see the comment on the
  `phoenix` service in `docker-compose.yml` (needs `PHOENIX_SECRET` and a `PHOENIX_API_KEY`).
- `BACKUP_TARGET_URL` sends backups off the VM (S3/R2). Without it they only survive database damage,
  not loss of the VM's disk.

## Evaluation gate

`MIN_HIT_RATE=0.8 MIN_PASS_RATE=0.7 sh scripts/eval-gate.sh` runs both evaluations against the live configuration and exits non-zero below the thresholds. The `eval-gate` GitHub workflow runs it on a self-hosted runner on the VM (label `nursingbot-vm`).
