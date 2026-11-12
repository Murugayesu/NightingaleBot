# Operations runbook

## First deployment

1. Install Docker on the server.
2. `cp .env.example .env`, then set:
   - secrets from `docker compose run --rm --no-deps migrate python -m nursing_bot.cli gen-secrets`
   - `ENVIRONMENT=production`
   - `LLM_API_KEY` (and `EMBEDDING_API_KEY` / `RERANK_API_KEY` if used), and **pin** `LLM_MODEL` to one model
   - leave `SITE_ADDRESS=:80` and `AUTH_COOKIE_SECURE=false` until a domain exists (see below)
3. `docker compose up -d --build`. `migrate` applies migrations and creates the job-queue tables
   before `api` and `worker` start. In production the API refuses to start with default secrets.
4. Create the first administrator:
   `docker compose exec api python -m nursing_bot.cli create-user you@example.org --admin`
   It prints a one-time password; sign in at `http://<server IP>` and choose your own.
5. Load documents (Admin → Documents, or `cli ingest-dir`), then run `cli eval retrieval` and set
   `GATE_THRESHOLD_SIMILARITY` to the suggested value.

## Accounts

| Task | How |
|---|---|
| Someone forgot their password | Admin → Users → **Reset password**, or `docker compose exec api python -m nursing_bot.cli reset-password <email>`. Give the one-time password to them privately (in person or by phone). They are signed out everywhere and must choose a new password at sign-in. |
| Make someone an admin | Admin → Users → **Make admin**, or `cli set-role <email> admin`. At least one active admin always remains. |
| Block an account | Admin → Users → **Disable** (signs it out at once). |
| Delete an account | The user: Settings → Delete my account. An admin: Admin → Users → Delete. Chats and feedback go with it. |
| Too many wrong passwords | After `LOGIN_MAX_FAILURES` (5) the account pauses for `LOGIN_LOCKOUT_MINUTES` (15). An admin reset clears the pause. |
| Close sign-ups | `SIGNUP_ENABLED=false` (existing accounts keep working; admins can still `cli create-user`). |
| Guests off | `GUEST_CHAT_ENABLED=false`: everyone must sign in. |

Usage limits: `USER_DAILY_TOKEN_QUOTA` per account per day, `SESSION_TOKEN_BUDGET` per guest
session, `GLOBAL_DAILY_TOKEN_CAP` for everyone together (a spending breaker; 0 = off). Days are UTC
(they reset at 05:30 IST). Admin → Overview shows today's tokens.

## Plain HTTP now, HTTPS later

Without a domain the app runs on `http://<server IP>`. Passwords and chats then cross the network
**unencrypted**: acceptable on localhost or a trusted network, not on the open internet. The sign-in
pages warn users when they are not on the server itself, and the API logs a warning at start-up.

When a domain points at the server:

1. set `SITE_ADDRESS=<domain>` and `ACME_EMAIL=<your e-mail>`;
2. set `AUTH_COOKIE_SECURE=true`;
3. `docker compose up -d`. Caddy obtains and renews the certificate itself (ports 80 and 443 must be reachable).

Existing sign-ins end once (the cookie changes); nothing else changes.

## Releases and rollback

Images are tagged with `APP_VERSION` (in `.env`, default `2.0.0`).

```bash
# upgrade: build the new release under a new tag
APP_VERSION=2.0.1 docker compose up -d --build
# roll back: start the previous images again (no build). Only roll back across releases
# that did not add a database migration, or restore the backup taken before the upgrade.
APP_VERSION=2.0.0 docker compose up -d
```

Take a backup before every upgrade: `docker compose exec backup sh /scripts/backup.sh`.

### Upgrading from 1.0

- Migration 004 adds the account and conversation tables. 1.0 data (documents, chunks, guest sessions) is untouched.
- The embeddable widget and the old admin panel (`/ui/...`) are gone. The whole site is now the app, and the admin panel is under `/admin` for admin accounts. Create an admin with `cli create-user ... --admin`.
- Settings: `WIDGET_FRAME_ANCESTORS` is no longer used; `WIDGET_WELCOME_MESSAGE` is now `WELCOME_MESSAGE`; `ENABLE_DATABASE_CHAT_HISTORY` now defaults to `false` (guests keep their history in the browser); `MAX_RECENT_CONTEXT_TURNS` defaults to 3. New settings are listed in `.env.example`.
- If the council's website linked to the widget, link to the app instead.

## Secrets with Docker secrets (instead of .env)

Any setting can be a file in `/run/secrets/<SETTING_NAME>`. A secret file **wins over** the same
setting in `.env` or the environment, and an empty `KEY=` line counts as "not set". Mount the
secrets on every service that runs the app code (api, worker and migrate). In a compose override:

```yaml
x-app-secrets: &app-secrets
  secrets: [ADMIN_API_KEY, SESSION_SECRET, LLM_API_KEY, EMBEDDING_API_KEY]

services:
  api: *app-secrets
  worker: *app-secrets
  migrate: *app-secrets

secrets:
  ADMIN_API_KEY: { file: ./secrets/ADMIN_API_KEY }
  SESSION_SECRET: { file: ./secrets/SESSION_SECRET }
  LLM_API_KEY: { file: ./secrets/LLM_API_KEY }
  EMBEDDING_API_KEY: { file: ./secrets/EMBEDDING_API_KEY }
```

Keep `./secrets/` out of git and readable only by the deploying user (`chmod 600`). The database
and object-storage passwords are still read by Postgres, MinIO and Compose from `.env`.

## Backups

The database now holds user accounts and their chats, so backups matter more than in 1.0.

- `backup` takes a verified `pg_dump` every `BACKUP_INTERVAL_SECONDS` (default daily) into the
  `backups` volume, keeps `BACKUP_RETENTION_DAYS` days and never fewer than `BACKUP_KEEP_MIN` dumps.
- `backup-sync` mirrors the dumps into the `nursing-backups` bucket.
- `documents-sync` mirrors the uploaded documents into the `nursing-documents-backup` bucket
  (deletions are not mirrored, so a document deleted by mistake can be recovered).
- **Off the machine:** set `BACKUP_TARGET_URL=https://KEY:SECRET@<host>` (S3, R2, another MinIO) and
  create both buckets there. Without it, backups survive a damaged database, not a lost disk.
- **Test the restore monthly:** `docker compose run --rm --entrypoint /bin/sh backup /scripts/restore-test.sh`.
- **Restore for real:**
  ```bash
  docker compose stop api worker
  docker compose run --rm --entrypoint /bin/sh backup /scripts/restore.sh /backups/<file>.dump
  docker compose start api worker
  ```

## Retention

The worker runs a daily clean-up (in inline mode, run `python -m nursing_bot.cli purge-history`
from cron): guest sessions after `CHAT_HISTORY_RETENTION_DAYS`, ended sign-in sessions, usage
counters older than 90 days, and, if `CONVERSATION_RETENTION_DAYS` is set, chats inactive for
that long.

## Monitoring

- Admin → Overview (or `GET /api/v1/admin/health`) reports database, storage, queue and
  stale-embedding status, user and conversation counts, answers by mode and today's tokens.
- Phoenix (`http://localhost:6006`, reach it through an SSH tunnel) shows every request: retrieval
  scores, routing (`rag.route`), prompts (PII-redacted) and token use.
- Logs: `docker compose logs -f api worker` (set `LOG_FORMAT=json` for log shippers).
- Admin → Feedback lists answers users marked "not helpful" (anonymised). Confirmed problems
  become new rows in `eval/evalset.csv`.

## Answer quality

- **Pin the chat model.** `LLM_MODEL=auto` lets a routing gateway pick a different model for every
  request. In live testing on 2026-11-12 the same official question was answered correctly
  (Rs. 365, cited) in 16 of 18 calls and "not available in the records" in 2: the pipeline then
  correctly declined rather than guess, but users see a needless "not found". Use one specific,
  capable model and re-run `cli eval answer` after any change.
- **General answers can be switched off** at once with `GENERAL_ANSWERS_ENABLED=false`
  (everything the documents don't cover is then declined, as in 1.0).
- The answer evaluation fails if any official question is answered from general knowledge
  (`official_fact_leaks` must be 0).

## Changing models

- Changing `LLM_MODEL` takes effect on restart.
- Changing `EMBEDDING_MODEL`: dense search ignores chunks from the old model, so run **Re-index all**
  (Admin → Documents) right away. `stale_embedding_chunks` in the health report shows progress.
- **Free-tier quotas.** Free embedding tiers have per-minute *and* daily limits. Ingestion waits out
  short limits (`EMBEDDING_RATE_LIMIT_RETRIES`), but a daily limit stops it until the reset. For free
  tiers set `WORKER_CONCURRENCY=1` and `EMBEDDING_BATCH_SIZE=16`; for the full corpus (~20,000
  chunks) use a paid embedding key or a local embedding model.
- **Provider outages** return HTTP 503 (`Retry-After: 30`) and a friendly message in the app; failed
  ingestions show the provider error under Admin → Documents and can be re-ingested.

## Worker reliability

- Jobs of a worker that died (crash, redeploy, reboot) are re-queued within 5 minutes.
- Deleting a document cancels its pending work.

## Edge settings

- Behind a cloud load balancer or CDN, set `CADDY_TRUSTED_PROXIES` to its CIDR ranges, otherwise all
  users share one rate-limit bucket. Never set it to ranges your users can connect from.
- Phoenix login is off by default (Phoenix is bound to 127.0.0.1). To enable it see the comment on
  the `phoenix` service in `docker-compose.yml`.

## Evaluation gate

`MIN_HIT_RATE=0.8 MIN_PASS_RATE=0.7 sh scripts/eval-gate.sh` runs both evaluations against the live
configuration and exits non-zero below the thresholds or on any official-fact leak. The `eval-gate`
GitHub workflow runs it on a self-hosted runner on the server (label `nursingbot-vm`).
