"""Small disposable check through the local UI's actual API proxy."""

import json
import re
import time
import uuid
from pathlib import Path

import httpx

root = Path(__file__).resolve().parent
html = (root / "index.html").read_text(encoding="utf-8")
javascript = (root / "app.js").read_text(encoding="utf-8")
ids = set(re.findall(r'id="([^"]+)"', html))
selectors = set(re.findall(r"\$\(['\"]#([a-zA-Z0-9_-]+)['\"]", javascript))
assert not selectors - ids, f"Missing HTML elements: {selectors - ids}"

marker = "local-ui-" + uuid.uuid4().hex[:10]
document_id = None
sessions = [marker + "-json", marker + "-sse"]
checks = {}
with httpx.Client(base_url="http://127.0.0.1:5173", timeout=120) as client:
    try:
        checks["health"] = client.get("/api/health").json()
        response = client.post(
            "/api/documents/upload",
            files={"file": (marker + ".txt", (root / "sample.txt").read_bytes() + f"\nRun: {marker}\n".encode(), "text/plain")},
            data={"doc_type": "general", "metadata_json": json.dumps({"local_test": marker})},
        )
        response.raise_for_status()
        doc = response.json()
        document_id = doc["id"]
        for _ in range(45):
            status = client.get(f"/api/documents/{document_id}/status").json()
            if status["status"] in {"active", "failed"}:
                break
            time.sleep(2)
        assert status["status"] == "active", status
        checks["ingestion"] = status
        chunks = client.get(f"/api/documents/{document_id}/chunks?limit=10&offset=0")
        chunks.raise_for_status()
        checks["chunks"] = len(chunks.json())
        update = client.patch(f"/api/documents/{document_id}", json={"metadata": {"test_stage": "verified"}})
        update.raise_for_status()
        assert update.json()["metadata"]["test_stage"] == "verified"
        checks["metadata_update"] = "ok"
        payload = {
            "query": "What is the local workspace verification code in this synthetic testing guide?",
            "filters": {"document_id": document_id},
            "session_id": sessions[0],
        }
        response = client.post("/api/chat", json=payload)
        response.raise_for_status()
        answer = response.json()
        checks["json_chat"] = {"is_grounded": answer["is_grounded"], "citations": len(answer["citations"]), "answer": answer["answer"]}
        history = client.get("/api/conversations/" + sessions[0])
        history.raise_for_status()
        assert len(history.json()["messages"]) == 2
        checks["conversation_history"] = "ok"
        response = client.post("/api/chat", json={**payload, "session_id": sessions[1], "stream": True})
        response.raise_for_status()
        events = re.findall(r"^event: (.+)$", response.text, re.MULTILINE)
        assert "token" in events and "end" in events, response.text
        checks["sse_events"] = events
        response = client.get("/api/admin/retrieval-preview", params={"query": "local workspace verification code", "top_k": 3})
        response.raise_for_status()
        checks["retrieval_preview"] = {"items": len(response.json()["items"]), "gate_passed": response.json()["gate_passed"]}
        response = client.get("/api/documents", params={"page": 1, "page_size": 10})
        response.raise_for_status()
        checks["document_list"] = "ok"
        invalid = client.post("/api/chat", json={"query": ""})
        assert invalid.status_code == 422
        checks["validation_error"] = 422
        response = client.delete(f"/api/documents/{document_id}")
        response.raise_for_status()
        for _ in range(15):
            retired = client.get(f"/api/documents/{document_id}").json()
            if retired["status"] == "retired":
                break
            time.sleep(0.1)
        assert retired["status"] == "retired", retired
        checks["retirement"] = "ok"
        print(json.dumps(checks, indent=2))
    finally:
        for session_id in sessions:
            client.delete("/api/conversations/" + session_id)
        if document_id:
            client.delete(f"/api/documents/{document_id}", params={"hard": "true"})
