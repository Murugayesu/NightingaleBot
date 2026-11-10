"""Process-wide logging setup, called once by the API and once by the worker."""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log shippers (Loki, CloudWatch, ...)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install a single stdout handler on the root logger.

    Safe to call more than once: it replaces its own handler instead of adding
    another, so tests and reloads do not duplicate log lines.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_nursing_bot", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler._nursing_bot = True  # type: ignore[attr-defined]
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Third-party loggers that are noisy at INFO.
    for noisy in ("LiteLLM", "litellm", "httpx", "botocore", "aiobotocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
