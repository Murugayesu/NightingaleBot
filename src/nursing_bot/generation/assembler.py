"""Format retrieved chunks into numbered sources for the prompt, plus citation records."""

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document as LCDocument


@dataclass
class AssembledContext:
    context_text: str
    citations: list[dict[str, Any]]


def citation_from_document(doc: LCDocument) -> dict[str, Any]:
    """The public citation shape (see docs/api-contract.md)."""
    meta = doc.metadata
    return {
        "source_index": meta.get("source_index", 0),
        "chunk_id": meta.get("chunk_id", ""),
        "document_id": meta.get("document_id", ""),
        "title": meta.get("title", ""),
        "page_number": meta.get("page_number"),
        "score": round(float(meta.get("score") or 0.0), 4),
        "snippet": meta.get("snippet", ""),
    }


class ContextAssembler:
    @classmethod
    def assemble(cls, documents: list[LCDocument]) -> AssembledContext:
        blocks = []
        citations = []
        for index, doc in enumerate(documents, start=1):
            doc.metadata["source_index"] = index
            label = [f"[Source {index}]"]
            if doc.metadata.get("title"):
                label.append(f"Document: {doc.metadata['title']}")
            if doc.metadata.get("page_number") is not None:
                label.append(f"Page: {doc.metadata['page_number']}")
            blocks.append(" | ".join(label) + "\n" + doc.page_content.strip())
            citations.append(citation_from_document(doc))
        return AssembledContext(
            context_text="\n\n---\n\n".join(blocks), citations=citations
        )
