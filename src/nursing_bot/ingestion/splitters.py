"""Split documents into chunks small enough to embed and cite precisely.

Markdown files are first split on their headings, so each chunk remembers its
section path (header_1 > header_2 ...). Everything is then split by size on
natural boundaries: paragraph, line, sentence, word.
"""

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

_MARKDOWN_HEADERS = [
    ("#", "header_1"),
    ("##", "header_2"),
    ("###", "header_3"),
    ("####", "header_4"),
]


class StructureSplitter:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150):
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=_MARKDOWN_HEADERS, strip_headers=False
        )
        self.char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def split_documents(self, documents: list[Document]) -> list[Document]:
        chunks: list[Document] = []
        for doc in documents:
            source = str(doc.metadata.get("source", ""))
            if source.lower().endswith((".md", ".markdown")):
                sections = [
                    Document(
                        page_content=s.page_content,
                        metadata={**doc.metadata, **s.metadata},
                    )
                    for s in self.header_splitter.split_text(doc.page_content)
                ]
            else:
                sections = [doc]
            for piece in self.char_splitter.split_documents(sections):
                text = piece.page_content.strip()
                if text:
                    chunks.append(
                        Document(page_content=text, metadata=dict(piece.metadata))
                    )
        return chunks
