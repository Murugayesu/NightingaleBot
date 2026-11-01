import csv
import io
import os
import tempfile
from pathlib import Path

from langchain_core.documents import Document


class DocumentLoaderFactory:
    """Native Document Loader Factory replacing archived langchain-community.

    Directly parses TXT, Markdown, CSV, TSV, PDF, DOCX, and XLSX files
    into standardized LangChain Document objects without deprecation warnings.
    """

    @classmethod
    def load_bytes(cls, content: bytes, filename: str) -> list[Document]:
        suffix = Path(filename).suffix.casefold()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(content)
            tmp_file.flush()

        try:
            return cls.load_file(tmp_path, filename_override=filename)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @classmethod
    def load_file(
        cls, file_path: str, filename_override: str | None = None
    ) -> list[Document]:
        path = Path(file_path)
        display_name = filename_override or path.name
        suffix = path.suffix.casefold()

        if suffix in {".txt", ".text", ".md", ".markdown"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            return [Document(page_content=text, metadata={"source": display_name})]

        if suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            content = path.read_text(encoding="utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            docs: list[Document] = []
            for i, row in enumerate(reader):
                row_lines = [
                    f"{k}: {v}"
                    for k, v in row.items()
                    if v is not None and str(v).strip()
                ]
                page_content = "\n".join(row_lines)
                if page_content.strip():
                    docs.append(
                        Document(
                            page_content=page_content,
                            metadata={"source": display_name, "row": i},
                        )
                    )
            return docs

        if suffix == ".pdf":
            try:
                from pypdf import PdfReader

                reader = PdfReader(str(path))
                pdf_docs: list[Document] = []
                for page_num, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    if text.strip():
                        pdf_docs.append(
                            Document(
                                page_content=text,
                                metadata={"source": display_name, "page": page_num},
                            )
                        )
                return pdf_docs
            except Exception as e:
                raise ValueError(
                    f"Failed to parse PDF document {display_name}: {e}"
                ) from e

        if suffix in {".docx", ".doc"}:
            try:
                import docx

                doc = docx.Document(str(path))
                parts: list[str] = []
                for p in doc.paragraphs:
                    if p.text.strip():
                        parts.append(p.text.strip())
                for table in doc.tables:
                    table_rows: list[str] = []
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells]
                        if any(row_cells):
                            table_rows.append(" | ".join(row_cells))
                    if table_rows:
                        parts.append("\n".join(table_rows))
                return [
                    Document(
                        page_content="\n\n".join(parts),
                        metadata={"source": display_name},
                    )
                ]
            except Exception as e:
                raise ValueError(
                    f"Failed to parse DOCX document {display_name}: {e}"
                ) from e

        if suffix in {".xlsx", ".xls"}:
            try:
                import openpyxl

                wb = openpyxl.load_workbook(str(path), data_only=True)
                excel_docs: list[Document] = []
                for sheet in wb.sheetnames:
                    ws = wb[sheet]
                    lines = []
                    for row in ws.iter_rows(values_only=True):
                        if any(cell is not None for cell in row):
                            lines.append(
                                " | ".join(
                                    str(cell) if cell is not None else ""
                                    for cell in row
                                )
                            )
                    if lines:
                        excel_docs.append(
                            Document(
                                page_content="\n".join(lines),
                                metadata={"source": display_name, "sheet": sheet},
                            )
                        )
                return excel_docs
            except Exception as e:
                raise ValueError(
                    f"Failed to parse Excel document {display_name}: {e}"
                ) from e

        raise ValueError(f"Unsupported document format '{suffix}' for {display_name}")
