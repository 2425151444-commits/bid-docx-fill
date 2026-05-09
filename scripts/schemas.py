from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any


class SourceType(str, Enum):
    UPLOAD_DOC = "UPLOAD_DOC"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    DYNAMIC = "DYNAMIC"


def normalize_text(value: str) -> str:
    text = value or ""
    text = text.replace("\u3000", " ")
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("：", ":").replace("；", ";").replace("，", ",")
    text = re.sub(r"\s+", "", text)
    return text.strip().lower()


def normalize_field_name(value: str) -> str:
    text = normalize_text(value)
    return re.sub(r"[()\-_:;,\[\]/\"'“”‘’]", "", text)


@dataclass
class DocumentBlock:
    block_id: str
    block_type: str
    section_name: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeBaseEntry:
    field_name: str
    value: str
    source_sheet: str
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedField:
    field_id: str
    field_name: str
    normalized_name: str
    section_name: str
    block_id: str
    block_type: str
    context_text: str
    detected_by: str
    metadata: dict[str, Any] = field(default_factory=dict)
    field_type: str = ""
    source_type: str | None = None
    resolved_value: str = ""
    confidence: float = 0.0
    needs_manual_review: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
