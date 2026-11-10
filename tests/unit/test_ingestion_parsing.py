import io
import json

import pytest
from langchain_core.documents import Document

from nursing_bot.ingestion.context import LLMContextualizer, add_context_headers
from nursing_bot.ingestion.loaders import load_prose, unsupported_reason
from nursing_bot.ingestion.pipeline import parse_document
from nursing_bot.ingestion.splitters import StructureSplitter
from tests.fakes import FakeLLMClient


def _pdf_bytes(pages: list[str]) -> bytes:
    """Build a real multi-page PDF with text, using only pypdf."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_pages_are_one_based():
    docs = load_prose(_pdf_bytes(["First page text", "Second page text"]), "faq.pdf")
    assert [d.metadata["page"] for d in docs] == [1, 2]
    assert "Second page" in docs[1].page_content


def test_docx_keeps_tables_in_order():
    import docx

    document = docx.Document()
    document.add_paragraph("Fees are listed below.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Service", "Fee"
    table.cell(1, 0).text, table.cell(1, 1).text = "Registration", "Rs. 365"
    document.add_paragraph("Pay online.")
    buffer = io.BytesIO()
    document.save(buffer)
    text = load_prose(buffer.getvalue(), "fees.docx")[0].page_content
    assert (
        text.index("Fees are listed")
        < text.index("Registration | Rs. 365")
        < text.index("Pay online")
    )


def test_corrupt_pdf_gives_readable_error():
    with pytest.raises(ValueError, match="Failed to parse broken.pdf"):
        load_prose(b"not a pdf", "broken.pdf")


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("a.pdf", True),
        ("a.JSON", True),
        ("a.doc", False),
        ("a.exe", False),
        ("noext", False),
    ],
)
def test_supported_types(name, ok):
    assert (unsupported_reason(name) is None) is ok


def test_markdown_headers_become_section_metadata():
    md = "# Registration\n\n## Fees\n\nThe fee is Rs. 365.\n\n## Documents\n\nBring photos."
    chunks = StructureSplitter(200, 20).split_documents(
        [Document(page_content=md, metadata={"source": "g.md"})]
    )
    fee = next(c for c in chunks if "365" in c.page_content)
    assert (
        fee.metadata["header_1"] == "Registration"
        and fee.metadata["header_2"] == "Fees"
    )
    header = add_context_headers(fee, "g.md").page_content.split("\n")[0]
    assert header == "[Document: g.md] [Section: Registration > Fees]"


def test_long_text_is_split_with_size_limit():
    text = "Sentence number one is here. " * 200
    chunks = StructureSplitter(300, 30).split_documents(
        [Document(page_content=text, metadata={"source": "a.txt"})]
    )
    assert len(chunks) > 5 and all(len(c.page_content) <= 300 for c in chunks)


def test_csv_rows_become_cards():
    csv_text = "College Name,District,Course\nABC College,Chennai,B.Sc Nursing\n,,\nXYZ,Madurai,GNM\n"
    cards = parse_document(
        csv_text.encode(), "colleges.csv", "institution_list", StructureSplitter()
    )
    assert len(cards) == 2
    assert (
        cards[0].metadata["district"] == "Chennai"
        and "ABC College" in cards[0].page_content
    )


def test_json_records_become_cards():
    data = json.dumps([{"name": "Course A", "district": "Salem"}, "free text"])
    cards = parse_document(data.encode(), "c.json", "dataset", StructureSplitter())
    assert len(cards) == 2 and cards[0].metadata["district"] == "Salem"


def test_xlsx_rows_become_cards():
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Colleges"
    sheet.append(["Name", "District"])
    sheet.append(["ABC", "Erode"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    cards = parse_document(buffer.getvalue(), "c.xlsx", "dataset", StructureSplitter())
    assert len(cards) == 1 and cards[0].metadata["sheet"] == "Colleges"


async def test_llm_contextualizer_adds_sentence_and_survives_failures():
    calls = {"n": 0}

    def answer(prompt: str, _system: str | None) -> str:
        calls["n"] += 1
        if "chunk two" in prompt:
            raise RuntimeError("provider down")
        return "This chunk explains the fee."

    contextualizer = LLMContextualizer(FakeLLMClient(answer), concurrency=2)
    chunks = [Document(page_content="chunk one"), Document(page_content="chunk two")]
    sentences = await contextualizer.contextualize("faq.pdf", "full text", chunks)
    assert sentences == ["This chunk explains the fee.", None]
    with_context = add_context_headers(chunks[0], "faq.pdf", sentences[0])
    assert with_context.page_content.startswith(
        "[Document: faq.pdf]\nThis chunk explains the fee."
    )
