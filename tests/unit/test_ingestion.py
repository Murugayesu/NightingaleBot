import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document as LCDocument

from nursing_bot.ingestion.context import add_context_headers
from nursing_bot.ingestion.loaders import DocumentLoaderFactory
from nursing_bot.ingestion.pipeline import IngestionPipeline
from nursing_bot.ingestion.splitters import StructureSplitter
from nursing_bot.ingestion.tabular import TabularCardGenerator
from nursing_bot.storage.models import Document


def test_document_loader_text():
    text_data = b"This is a nursing regulation document.\nSecond line of policy."
    docs = DocumentLoaderFactory.load_bytes(text_data, "policy.txt")
    assert len(docs) >= 1
    assert "nursing regulation" in docs[0].page_content
    assert docs[0].metadata["source"] == "policy.txt"


def test_document_loader_csv():
    csv_data = b"id,name,role\n1,Nurse Alice,RN\n2,Nurse Bob,Midwife"
    docs = DocumentLoaderFactory.load_bytes(csv_data, "team.csv")
    assert len(docs) == 2
    assert "Alice" in docs[0].page_content
    assert "Bob" in docs[1].page_content


def test_document_loader_docx_includes_tables():
    import io

    import docx

    doc = docx.Document()
    doc.add_paragraph("First paragraph text")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "ColA"
    table.cell(0, 1).text = "ColB"
    table.cell(1, 0).text = "Val1"
    table.cell(1, 1).text = "Val2"
    buf = io.BytesIO()
    doc.save(buf)

    docs = DocumentLoaderFactory.load_bytes(buf.getvalue(), "report.docx")
    assert len(docs) == 1
    content = docs[0].page_content
    assert "First paragraph text" in content
    assert "ColA | ColB" in content
    assert "Val1 | Val2" in content


def test_structure_splitter_markdown():
    splitter = StructureSplitter(chunk_size=100, chunk_overlap=10)
    raw_md = """# Introduction
This is the intro section to the nursing council regulations.

## Architecture
The platform utilizes pgvector and LangChain loaders.
"""
    doc = LCDocument(page_content=raw_md, metadata={"source": "arch.md"})
    chunks = splitter.split_documents([doc])
    assert len(chunks) >= 2
    intro_chunk = next((c for c in chunks if "intro section" in c.page_content), None)
    arch_chunk = next((c for c in chunks if "pgvector" in c.page_content), None)
    assert intro_chunk is not None
    assert arch_chunk is not None


def test_tabular_card_generator():
    csv_text = "Name,Category,Value\nCollege of Nursing,Education,100\nApollo Hospital,Clinical,200"
    cards = TabularCardGenerator.from_csv(csv_text, default_doc_type="institution_list")
    assert len(cards) == 2
    assert "College of Nursing" in cards[0].page_content
    assert cards[0].metadata["doc_type"] == "institution_list"


def test_add_context_headers():
    doc = LCDocument(
        page_content="The retention period is 7 years.",
        metadata={"header_1": "Compliance", "header_2": "Retention", "page": 4},
    )
    contextualized = add_context_headers(doc, document_title="TNNMC Regulations")
    assert contextualized.page_content.startswith("[Document: TNNMC Regulations]")
    assert "Compliance" in contextualized.page_content
    assert "The retention period is 7 years." in contextualized.page_content


@pytest.mark.asyncio
async def test_ingestion_pipeline_end_to_end():
    mock_llm_client = AsyncMock()
    mock_llm_client.embedding_model = "test-embed-model"
    mock_llm_client.aembed_texts = AsyncMock(return_value=[[0.05] * 1536])
    mock_storage = AsyncMock()
    mock_storage.download = AsyncMock(
        return_value=b"System overview and operating rules."
    )

    session = AsyncMock()
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="overview.txt",
        doc_type="policy",
        s3_key="overview.txt",
        content_type="prose",
        status="pending",
        metadata_json={},
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    pipeline = IngestionPipeline(
        llm_client=mock_llm_client,
        storage=mock_storage,
        chunk_size=200,
        chunk_overlap=20,
    )

    res_doc = await pipeline.ingest_document(session, doc_id)
    assert res_doc.status == "completed"
    assert res_doc.chunk_count >= 1
    session.add.assert_called()
    mock_llm_client.aembed_texts.assert_called_once()
