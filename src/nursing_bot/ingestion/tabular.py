import csv
import io
import json
from collections.abc import Iterable
from typing import Any

from langchain_core.documents import Document


class TabularCardGenerator:
    """Converts structured tabular datasets (CSV, XLSX, JSON) into prose dataset cards."""

    @classmethod
    def from_csv(
        cls,
        content: str,
        default_doc_type: str = "dataset",
        delimiter: str = ",",
    ) -> list[Document]:
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        return cls._from_rows(reader, default_doc_type)

    @classmethod
    def from_xlsx(
        cls,
        content: bytes,
        default_doc_type: str = "dataset",
    ) -> list[Document]:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError(
                "XLSX parsing requires the 'parsers' dependency group."
            ) from exc

        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        docs: list[Document] = []
        for worksheet in workbook.worksheets:
            rows = worksheet.iter_rows(values_only=True)
            headers = next(rows, None)
            if not headers:
                continue
            keys = [
                str(value).strip() if value is not None else "" for value in headers
            ]
            records = (
                {
                    keys[index]: value
                    for index, value in enumerate(row)
                    if index < len(keys) and keys[index]
                }
                for row in rows
            )
            sheet_docs = cls._from_rows(records, default_doc_type)
            for doc in sheet_docs:
                doc.metadata["sheet"] = worksheet.title
            docs.extend(sheet_docs)
        workbook.close()
        return docs

    @classmethod
    def from_json(
        cls,
        content: str,
        default_doc_type: str = "dataset",
    ) -> list[Document]:
        data = json.loads(content)
        records = data if isinstance(data, list) else [data]
        docs: list[Document] = []

        for item in records:
            if isinstance(item, dict):
                title = (
                    item.get("title")
                    or item.get("name")
                    or item.get("College Name")
                    or item.get("Institution")
                    or "Record Entry"
                )
                district = item.get("district") or item.get("District") or ""
                program = item.get("course") or item.get("program") or ""

                prose_lines = [f"Entry: {title}"]
                for k, v in item.items():
                    if isinstance(v, (dict, list)):
                        v_str = json.dumps(v, ensure_ascii=False)
                    else:
                        v_str = str(v)
                    prose_lines.append(f"- {k}: {v_str}")

                prose_text = "\n".join(prose_lines)
                meta = {
                    "doc_type": default_doc_type,
                    "institution": title,
                    "district": district,
                    "program": program,
                }
                docs.append(Document(page_content=prose_text, metadata=meta))
            elif isinstance(item, str) and item.strip():
                docs.append(
                    Document(
                        page_content=item.strip(),
                        metadata={"doc_type": default_doc_type},
                    )
                )

        return docs

    @classmethod
    def _from_rows(
        cls,
        rows: Iterable[dict[str, Any]],
        default_doc_type: str,
    ) -> list[Document]:
        docs: list[Document] = []

        for row in rows:
            cleaned_row = {
                str(k).strip(): (str(v).strip() if v is not None else "")
                for k, v in row.items()
                if k
            }
            if not any(cleaned_row.values()):
                continue

            name = (
                cleaned_row.get("College Name")
                or cleaned_row.get("name")
                or cleaned_row.get("Name")
                or cleaned_row.get("Institution")
                or "Record"
            )
            district = cleaned_row.get("District") or cleaned_row.get("district") or ""
            program = (
                cleaned_row.get("Course")
                or cleaned_row.get("program")
                or cleaned_row.get("Course Name")
                or cleaned_row.get("Category")
                or ""
            )

            prose_lines = [f"Record for {name}:"]
            for field, value in cleaned_row.items():
                if value:
                    prose_lines.append(f"- {field}: {value}")

            prose_text = "\n".join(prose_lines)
            metadata = {
                "doc_type": default_doc_type,
                "institution": name,
                "district": district,
                "program": program,
                **{
                    f"field_{k.lower().replace(' ', '_')}": v
                    for k, v in cleaned_row.items()
                    if k and v
                },
            }
            docs.append(Document(page_content=prose_text, metadata=metadata))

        return docs
