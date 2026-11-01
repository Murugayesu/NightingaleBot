import json
from typing import Any

DOC_COLUMNS = frozenset({"doc_type", "content_type", "filename"})
CHUNK_COLUMNS = frozenset({"status", "document_id", "page_number"})


def normalize_filter_dict(filters: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize filter keys and values, removing None or empty entries."""
    if not filters:
        return {}
    cleaned = {}
    for k, v in filters.items():
        if v is None:
            continue
        cleaned[str(k).strip()] = v
    return cleaned


def build_filter_sql(
    filters: dict[str, Any] | None,
    params: dict[str, Any],
    *,
    chunk_status: str = "active",
) -> str:
    """Construct SQL WHERE predicate clauses with parameter binding."""
    clauses = ["c.status = :chunk_status"]
    params["chunk_status"] = chunk_status

    normalized = normalize_filter_dict(filters)
    for index, (key, value) in enumerate(normalized.items()):
        values = value if isinstance(value, list) else [value]
        if not values:
            continue

        alternatives: list[str] = []
        for value_index, item in enumerate(values):
            param_name = f"f_{index}_{value_index}"
            if key in DOC_COLUMNS:
                alternatives.append(f"LOWER(d.{key}) = :{param_name}")
                params[param_name] = str(item).casefold()
            elif key == "document_id":
                alternatives.append(f"c.document_id = CAST(:{param_name} AS uuid)")
                params[param_name] = str(item)
            elif key == "page_number":
                alternatives.append(f"c.page_number = :{param_name}")
                params[param_name] = item
            else:
                alternatives.append(f"c.metadata @> CAST(:{param_name} AS jsonb)")
                params[param_name] = json.dumps({key: item})

        if alternatives:
            clauses.append(f"({' OR '.join(alternatives)})")

    return " AND ".join(clauses)
