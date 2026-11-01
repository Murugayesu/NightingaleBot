from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document as LCDocument


@dataclass
class AssembledContext:
    context_text: str
    citations: list[dict[str, Any]]


class ContextAssembler:
    """Formats retrieved documents into cited prompt context blocks."""

    @classmethod
    def assemble(cls, documents: list[LCDocument]) -> AssembledContext:
        if not documents:
            return AssembledContext(
                context_text="No official reference documents found.",
                citations=[],
            )

        context_blocks = []
        citations = []

        for idx, doc in enumerate(documents, start=1):
            source_label = f"[Source {idx}]"
            source_meta = []
            if "institution" in doc.metadata:
                source_meta.append(f"Institution: {doc.metadata['institution']}")
            if "source" in doc.metadata:
                source_meta.append(f"Doc: {doc.metadata['source']}")
            if "page_number" in doc.metadata and doc.metadata["page_number"] is not None:
                source_meta.append(f"Page: {doc.metadata['page_number']}")

            meta_str = f" | {' | '.join(source_meta)}" if source_meta else ""
            context_blocks.append(f"{source_label}{meta_str}\n{doc.page_content.strip()}\n")

            citations.append(
                {
                    "source_index": idx,
                    "chunk_id": doc.metadata.get("chunk_id", ""),
                    "document_id": doc.metadata.get("document_id", ""),
                    "score": round(doc.metadata.get("score", 0.0), 4),
                    "metadata": doc.metadata,
                }
            )

        context_text = "\n---\n".join(context_blocks)
        return AssembledContext(context_text=context_text, citations=citations)
