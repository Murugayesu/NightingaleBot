from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


class StructureSplitter:
    """Structure-aware document chunker combining header splitting with character boundaries."""

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        headers_to_split_on: list[tuple[str, str]] | None = None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.headers_to_split_on = headers_to_split_on or [
            ("#", "header_1"),
            ("##", "header_2"),
            ("###", "header_3"),
            ("####", "header_4"),
        ]
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.headers_to_split_on,
            strip_headers=False,
        )
        self.char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def split_documents(self, documents: list[Document]) -> list[Document]:
        """Split a list of documents into structure-aware chunks."""
        result_chunks: list[Document] = []

        for doc in documents:
            source = doc.metadata.get("source", "")
            is_markdown = source.endswith((".md", ".markdown"))

            if is_markdown:
                try:
                    header_splits = self.header_splitter.split_text(doc.page_content)
                except Exception:  # noqa: BLE001
                    header_splits = [doc]

                for h_doc in header_splits:
                    merged_meta = {**doc.metadata, **h_doc.metadata}
                    sub_chunks = self.char_splitter.split_text(h_doc.page_content)
                    for chunk_text in sub_chunks:
                        if chunk_text.strip():
                            result_chunks.append(
                                Document(
                                    page_content=chunk_text.strip(),
                                    metadata=dict(merged_meta),
                                )
                            )
            else:
                sub_chunks = self.char_splitter.split_documents([doc])
                for sc in sub_chunks:
                    if sc.page_content.strip():
                        result_chunks.append(sc)

        return result_chunks
