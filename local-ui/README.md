# Local NursingBot testing UI

Open **http://127.0.0.1:5173** after starting the workspace.

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .artifacts/local-ui/start.ps1
```

The launcher starts the existing Docker backend and a dependency-free Node UI server. The browser talks to the Docker API through a same-origin proxy at `/api`, including multipart uploads and SSE. Everything binds locally; no hosting or deployment is involved.

`configure.py` creates `compose.local.yml` for this local session. When your `.env` points to a host-local OpenAI-compatible proxy, it changes that hostname to `host.docker.internal` inside containers and adds the `openai/` prefix needed by LiteLLM. When the embedding model is `auto`, the local override adds an internal `local-model-proxy` container that maps a LiteLLM-compatible alias back to `auto`, preserving the 1,536-dimensional embedding request. This forwards to your real model proxy. Model credentials still come from `.env`; the UI never receives them. The original `.env`, main Compose file, and backend source are not modified.

Available screens:

- **Chat:** JSON or SSE answers, filters, citations, session history, deleting conversations, and cancelling browser requests.
- **Documents:** uploads, ingestion polling, status/type/content filters, pagination, document records, chunks, metadata updates, retirement, and permanent deletion.
- **Retrieval:** query, district and top-K controls; evidence, rank and relevance-gate inspection.
- **System:** health, metrics, service information, and evaluated reindexing.
- **API explorer:** OpenAPI endpoint list, custom method/path/body requests, validation errors, and raw responses.
- **Request log:** request payloads, response bodies, HTTP status, timings, and SSE events.

The UI calls real APIs and changes real local data. Retire, delete, and reindex controls are explicit actions. Start with a small text document, wait until it is active, then use **Chat with this source**.

Stop only the UI:

```powershell
powershell -ExecutionPolicy Bypass -File .artifacts/local-ui/stop.ps1
```

Stop the Docker backend while keeping stored data:

```powershell
docker compose -f docker-compose.yml -f .artifacts/local-ui/compose.local.yml stop
```

All frontend files are under the already-ignored `.artifacts/` directory. They are not committed. `server.log`, `server-error.log`, and `server.pid` are local runtime files.
