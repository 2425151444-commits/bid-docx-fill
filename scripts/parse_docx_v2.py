from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from schemas import DocumentBlock


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


@dataclass
class ParsedDocument:
    input_path: str
    blocks: list[DocumentBlock]
    full_text: str

    def to_dict(self) -> dict:
        return {
            "input_path": self.input_path,
            "block_count": len(self.blocks),
            "full_text": self.full_text,
            "blocks": [block.to_dict() for block in self.blocks],
        }


def _read_text_nodes(element: etree._Element) -> str:
    return "".join(element.xpath(".//w:t/text()", namespaces=NS))


def _is_section_heading(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 80:
        return False
    patterns = [
        r"^第[一二三四五六七八九十0-9]+章",
        r"^第[一二三四五六七八九十0-9]+节",
        r"^[一二三四五六七八九十]+、",
        r"^\([一二三四五六七八九十0-9]+\)",
        r"^（[一二三四五六七八九十0-9]+）",
    ]
    return any(re.match(pattern, stripped) for pattern in patterns)


def parse_docx(docx_path: str) -> ParsedDocument:
    path = Path(docx_path)
    if path.suffix.lower() != ".docx":
        raise ValueError("Current parser only supports .docx input.")
    if not path.exists():
        raise FileNotFoundError(f"Input file does not exist: {docx_path}")

    blocks: list[DocumentBlock] = []
    section_name = "未分段"
    paragraph_index = 0
    table_row_index = 0
    table_paragraph_index = 0

    with ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")

    root = etree.fromstring(xml_bytes)
    body = root.find("w:body", namespaces=NS)
    if body is None:
        raise ValueError("Could not find the Word document body.")

    for child in body:
        tag_name = etree.QName(child.tag).localname
        if tag_name == "p":
            text = _read_text_nodes(child)
            if not text.strip():
                continue
            stripped_text = text.strip()
            if _is_section_heading(stripped_text):
                section_name = stripped_text
            blocks.append(
                DocumentBlock(
                    block_id=f"p-{paragraph_index}",
                    block_type="paragraph",
                    section_name=section_name,
                    text=text,
                    metadata={},
                )
            )
            paragraph_index += 1
            continue

        if tag_name != "tbl":
            continue

        for table_index, row in enumerate(child.xpath("./w:tr", namespaces=NS)):
            row_cells = row.xpath("./w:tc", namespaces=NS)
            cells = [_read_text_nodes(cell) for cell in row_cells]
            if not any(cell.strip() for cell in cells):
                continue

            row_block_id = f"t-{table_row_index}"
            blocks.append(
                DocumentBlock(
                    block_id=row_block_id,
                    block_type="table_row",
                    section_name=section_name,
                    text=" | ".join(cells),
                    metadata={"cells": cells, "table_row_order": table_index},
                )
            )
            table_row_index += 1

            if len(row_cells) == 1:
                only_cell = row_cells[0]
                for paragraph_in_cell_index, paragraph in enumerate(only_cell.xpath("./w:p", namespaces=NS)):
                    paragraph_text = _read_text_nodes(paragraph)
                    if not paragraph_text.strip():
                        continue
                    blocks.append(
                        DocumentBlock(
                            block_id=f"tp-{table_paragraph_index}",
                            block_type="table_paragraph",
                            section_name=section_name,
                            text=paragraph_text,
                            metadata={
                                "row_block_id": row_block_id,
                                "cell_index": 0,
                                "paragraph_in_cell_index": paragraph_in_cell_index,
                            },
                        )
                    )
                    table_paragraph_index += 1

    full_text = "\n".join(block.text for block in blocks)
    return ParsedDocument(input_path=str(path), blocks=blocks, full_text=full_text)
