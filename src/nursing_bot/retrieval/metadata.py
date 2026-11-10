"""Turn user filters into a safe SQL WHERE clause shared by dense and sparse search.

Safety: filter *values* are always bound parameters. Filter *keys* only reach
the SQL text when they are on a fixed allow-list of column names; any other
key is matched inside the JSONB metadata through a bound containment value.
"""

import json
import uuid
from typing import Any

# Columns selected by every retrieval query (dense and sparse return the same shape).
SELECT_COLUMNS = """
    c.id, c.document_id, c.content, c.contextualised_text, c.metadata,
    c.page_number, c.heading_hierarchy, d.filename
"""

_DOCUMENT_COLUMNS = frozenset({"doc_type", "content_type", "filename"})


def normalize_filter_dict(filters: dict[str, Any] | None) -> dict[str, Any]:
    """Drop empty values and flatten the `extra` bag into top-level keys.

    `{"doc_type": "faq", "extra": {"year": 2024}, "district": None}`
    → `{"doc_type": "faq", "year": 2024}`
    """
    if not filters:
        return {}
    flat: dict[str, Any] = {}
    extra = filters.get("extra")
    items = list(filters.items())
    if isinstance(extra, dict):
        items = [(k, v) for k, v in items if k != "extra"] + list(extra.items())
    for key, value in items:
        key = str(key).strip()
        if not key or value is None:
            continue
        if isinstance(value, (list, tuple, set, dict, str)) and len(value) == 0:
            continue
        if isinstance(value, (list, tuple, set)):
            value = [v for v in value if v is not None and v != ""]
            if not value:
                continue
        flat[key] = value
    return flat


def build_filter_sql(
    filters: dict[str, Any] | None,
    params: dict[str, Any],
    *,
    chunk_status: str = "active",
) -> str:
    """Return a WHERE predicate (without the WHERE keyword) and fill `params`.

    Several values for one key are OR-ed; different keys are AND-ed.
    """
    clauses = ["c.status = :chunk_status"]
    params["chunk_status"] = chunk_status

    for key_index, (key, value) in enumerate(normalize_filter_dict(filters).items()):
        values = list(value) if isinstance(value, (list, tuple, set)) else [value]
        alternatives: list[str] = []
        for value_index, item in enumerate(values):
            name = f"f_{key_index}_{value_index}"
            if key in _DOCUMENT_COLUMNS:
                alternatives.append(f"LOWER(d.{key}) = :{name}")
                params[name] = str(item).casefold()
            elif key == "document_id":
                alternatives.append(f"c.document_id = CAST(:{name} AS uuid)")
                params[name] = str(_as_uuid(item))
            elif key == "page_number":
                alternatives.append(f"c.page_number = :{name}")
                params[name] = _as_int(item)
            else:
                alternatives.append(f"c.metadata @> CAST(:{name} AS jsonb)")
                params[name] = json.dumps({key: item})
        clauses.append(f"({' OR '.join(alternatives)})")

    return " AND ".join(clauses)


class InvalidFilterError(ValueError):
    """A filter value has the wrong type (the API turns this into HTTP 422)."""


def _as_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise InvalidFilterError(
            f"document_id filter {value!r} is not a UUID."
        ) from exc


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise InvalidFilterError("page_number filter must be a whole number.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidFilterError(
            f"page_number filter {value!r} is not a whole number."
        ) from exc


def validate_filters(filters: dict[str, Any] | None) -> None:
    """Raise InvalidFilterError before any query runs if a filter value is unusable."""
    build_filter_sql(filters, {})
