"""Context headers: give every chunk the context it lost when it was cut out.

A chunk like "The fee is Rs. 365." is ambiguous on its own. We prepend:

  1. a template header (always):  [Document: FAQ.pdf] [Section: Fees] [Page: 2]
  2. an LLM-written sentence (optional, CONTEXTUAL_HEADERS_ENABLED):
     "This chunk is from the TNNMC online registration FAQ and states the
      registration fee." (Anthropic's "contextual retrieval" technique)

The prefixed text is what gets embedded and keyword-indexed, so searches match
on document title and topic, not only on the chunk's own words.
"""

import asyncio
import logging

from langchain_core.documents import Document

from nursing_bot.models.litellm_client import LiteLLMClient

logger = logging.getLogger(__name__)

_DOCUMENT_EXCERPT_CHARS = 12_000

_CONTEXT_PROMPT = """<document title="{title}">
{document}
</document>

Here is a chunk from that document:
<chunk>
{chunk}
</chunk>

Write one or two short sentences that situate this chunk within the document
(what it is about and which part it belongs to) to improve search retrieval.
Use the document's own terms. Reply with the sentences only."""


def template_header(doc: Document, document_title: str) -> str:
    tags = [f"[Document: {document_title}]"]
    sections = [
        str(doc.metadata[f"header_{level}"]).strip()
        for level in range(1, 5)
        if str(doc.metadata.get(f"header_{level}") or "").strip()
    ]
    if sections:
        tags.append(f"[Section: {' > '.join(sections)}]")
    if doc.metadata.get("page") is not None:
        tags.append(f"[Page: {doc.metadata['page']}]")
    elif doc.metadata.get("sheet"):
        tags.append(f"[Sheet: {doc.metadata['sheet']}]")
    return " ".join(tags)


def add_context_headers(
    doc: Document, document_title: str, llm_context: str | None = None
) -> Document:
    header = template_header(doc, document_title)
    prefix = f"{header}\n{llm_context.strip()}" if llm_context else header
    metadata = {**doc.metadata, "context_header": prefix}
    return Document(page_content=f"{prefix}\n\n{doc.page_content}", metadata=metadata)


class LLMContextualizer:
    """Generate the optional LLM context sentence for many chunks concurrently."""

    def __init__(
        self, llm_client: LiteLLMClient, model: str | None = None, concurrency: int = 4
    ):
        self.llm_client = llm_client
        self.model = model
        self.semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(self, title: str, document_text: str, chunk: Document) -> str | None:
        prompt = _CONTEXT_PROMPT.format(
            title=title,
            document=document_text[:_DOCUMENT_EXCERPT_CHARS],
            chunk=chunk.page_content,
        )
        async with self.semaphore:
            try:
                extra = {"model": self.model} if self.model else {}
                text = await self.llm_client.acomplete(
                    prompt, temperature=0.0, max_tokens=120, **extra
                )
            # A missing sentence is fine: the template header remains.
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Context sentence failed for a chunk of %s: %s", title, exc
                )
                return None
        return text.strip() or None

    async def contextualize(
        self, title: str, document_text: str, chunks: list[Document]
    ) -> list[str | None]:
        return list(
            await asyncio.gather(*(self._one(title, document_text, c) for c in chunks))
        )
