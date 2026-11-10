"""Parse prose documents (PDF, DOCX, TXT, Markdown) into LangChain Documents.

Tabular files (CSV, TSV, XLSX, JSON) are handled by tabular.py instead, because
each row should become its own searchable "card" rather than one blob of text.

All parsers here are synchronous and CPU-bound; the ingestion pipeline runs
them in a worker thread so they never block the event loop.
"""

import io
from pathlib import Path

from langchain_core.documents import Document

PROSE_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".text", ".md", ".markdown"})
TABULAR_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".json"})
SUPPORTED_SUFFIXES = PROSE_SUFFIXES | TABULAR_SUFFIXES
_LEGACY_FORMATS = {".doc": ".docx", ".xls": ".xlsx"}


def file_suffix(filename: str) -> str:
    return Path(filename).suffix.casefold()


def unsupported_reason(filename: str) -> str | None:
    """None if the file type is supported, otherwise a human-readable reason."""
    suffix = file_suffix(filename)
    if suffix in SUPPORTED_SUFFIXES:
        return None
    if suffix in _LEGACY_FORMATS:
        return f"Legacy {suffix} files are not supported; please convert to {_LEGACY_FORMATS[suffix]}."
    supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
    return f"Unsupported file type '{suffix or '(none)'}'. Supported: {supported}."


def _load_pdf(data: bytes, name: str) -> list[Document]:
    from pypdf import PdfReader

    pages = []
    for number, page in enumerate(PdfReader(io.BytesIO(data)).pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(
                Document(page_content=text, metadata={"source": name, "page": number})
            )
    return pages


def _load_docx(data: bytes, name: str) -> list[Document]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(io.BytesIO(data))
    parts: list[str] = []
    # Walk the body in order so tables stay next to the paragraphs that introduce them.
    for child in document.element.body.iterchildren():
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name == "p":
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif local_name == "tbl":
            rows = []
            for row in Table(child, document).rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parts.append("\n".join(rows))
    return (
        [Document(page_content="\n\n".join(parts), metadata={"source": name})]
        if parts
        else []
    )


def _load_text(data: bytes, name: str) -> list[Document]:
    text = data.decode("utf-8-sig", errors="replace")
    return (
        [Document(page_content=text, metadata={"source": name})] if text.strip() else []
    )


def load_prose(data: bytes, filename: str) -> list[Document]:
    """Parse a prose file. Raises ValueError with a readable message on failure."""
    suffix = file_suffix(filename)
    loaders = {
        ".pdf": _load_pdf,
        ".docx": _load_docx,
        ".txt": _load_text,
        ".text": _load_text,
        ".md": _load_text,
        ".markdown": _load_text,
    }
    loader = loaders.get(suffix)
    if loader is None:
        raise ValueError(
            unsupported_reason(filename) or f"'{suffix}' is not a prose format."
        )
    try:
        return loader(data, filename)
    except Exception as exc:
        raise ValueError(f"Failed to parse {filename}: {exc}") from exc
