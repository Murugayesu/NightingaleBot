"""Create a local-only Compose override using the existing model proxy."""

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from nursing_bot.config import get_settings

root = Path(__file__).resolve().parent
s = get_settings()


def container_url(value):
    if not value:
        return value
    parts = urlsplit(value)
    if parts.hostname in {"127.0.0.1", "localhost", "::1"}:
        host = "host.docker.internal"
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return value


def model_name(model, base_url):
    return f"openai/{model}" if base_url and "/" not in model else model


environment = {
    "LLM_MODEL": model_name(s.LLM_MODEL, s.LLM_BASE_URL),
    "EMBEDDING_MODEL": model_name(s.EMBEDDING_MODEL, s.EMBEDDING_BASE_URL),
}
for name in ("LLM_BASE_URL", "EMBEDDING_BASE_URL"):
    if getattr(s, name):
        environment[name] = container_url(getattr(s, name))

# JSON is also valid YAML. Credentials continue to come from the existing .env.
override = {"services": {name: {"environment": environment.copy()} for name in ("api", "worker")}}
mode = "Existing local model proxy"
if s.EMBEDDING_MODEL.strip() == "auto" and s.EMBEDDING_BASE_URL:
    # LiteLLM restricts dimensions for unknown OpenAI model names. A named local
    # alias keeps dimension validation while this bridge sends 'auto' upstream.
    environment["EMBEDDING_MODEL"] = "openai/local-text-embedding-3"
    environment["EMBEDDING_BASE_URL"] = "http://local-model-proxy:8088/v1"
    environment["LLM_BASE_URL"] = "http://local-model-proxy:8088/v1"
    for name in ("api", "worker"):
        override["services"][name] = {
            "environment": environment.copy(),
            "depends_on": {"local-model-proxy": {"condition": "service_started"}},
        }
    override["services"]["local-model-proxy"] = {
        "image": "nursingbot-app:latest",
        "pull_policy": "never",
        "command": ["uvicorn", "model_bridge:app", "--app-dir", "/local", "--host", "0.0.0.0", "--port", "8088"],
        "env_file": [str(root.parent.parent / ".env")],
        "environment": {
            "UPSTREAM_CHAT_BASE_URL": container_url(s.LLM_BASE_URL or s.EMBEDDING_BASE_URL),
            "UPSTREAM_EMBEDDING_BASE_URL": container_url(s.EMBEDDING_BASE_URL),
            "UPSTREAM_CHAT_MODEL": s.LLM_MODEL.removeprefix("openai/"),
            "UPSTREAM_EMBEDDING_MODEL": s.EMBEDDING_MODEL,
        },
        "volumes": [{"type": "bind", "source": str(root / "model_bridge.py"), "target": "/local/model_bridge.py", "read_only": True}],
        "restart": "unless-stopped",
    }
    mode = "Live local model proxy with compatibility adapter"
(root / "compose.local.yml").write_text(json.dumps(override, indent=2), encoding="utf-8")
(root / "runtime.json").write_text(
    json.dumps({"chatModel": environment["LLM_MODEL"], "embeddingModel": environment["EMBEDDING_MODEL"], "mode": mode}),
    encoding="utf-8",
)
print("Prepared local Docker model routing. Credentials stay in .env.")
