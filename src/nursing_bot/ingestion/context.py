from langchain_core.documents import Document


def add_context_headers(
    doc: Document,
    document_title: str | None = None,
) -> Document:
    """Prepend context header tags (Document, Section, Page/Sheet) to document page_content."""
    tags: list[str] = []

    title = document_title or doc.metadata.get("source") or doc.metadata.get("title")
    if title:
        tags.append(f"[Document: {title}]")

    sections: list[str] = []
    for level in range(1, 5):
        key = f"header_{level}"
        val = doc.metadata.get(key)
        if val and isinstance(val, str) and val.strip():
            sections.append(val.strip())

    if sections:
        tags.append(f"[Section: {' > '.join(sections)}]")

    if "page" in doc.metadata and doc.metadata["page"] is not None:
        tags.append(f"[Page: {doc.metadata['page']}]")
    elif doc.metadata.get("sheet"):
        tags.append(f"[Sheet: {doc.metadata['sheet']}]")

    if not tags:
        return doc

    header_prefix = " ".join(tags)
    new_content = f"{header_prefix}\n\n{doc.page_content}"

    new_metadata = dict(doc.metadata)
    new_metadata["context_header"] = header_prefix

    return Document(page_content=new_content, metadata=new_metadata)
