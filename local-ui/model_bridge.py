"""Local compatibility adapter for a proxy using 'auto' as its model name."""

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "live upstream proxy"}


@app.post("/v1/{endpoint:path}")
async def forward(endpoint: str, request: Request):
    if endpoint not in {"embeddings", "chat/completions"}:
        return Response(status_code=404)
    payload = await request.json()
    embedding = endpoint == "embeddings"
    prefix = "EMBEDDING" if embedding else "CHAT"
    base = os.environ[f"UPSTREAM_{prefix}_BASE_URL"].rstrip("/")
    payload["model"] = os.environ[f"UPSTREAM_{prefix}_MODEL"]
    key = os.environ.get("EMBEDDING_API_KEY" if embedding else "LLM_API_KEY", "")
    if embedding and not key:
        key = os.environ.get("LLM_API_KEY", "")
    client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10))
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        upstream = await client.send(client.build_request("POST", base + "/" + endpoint, json=payload, headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return Response(content='{"detail":"Local model proxy is unreachable: ' + type(exc).__name__ + '"}', status_code=502, media_type="application/json")

    async def close():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        background=BackgroundTask(close),
    )
