from __future__ import annotations

from field_semantics import canonical_upload_field_name
from kb_loader import KnowledgeBase
from schemas import ExtractedField


KB_FIRST_FIELD_TYPES = {
    "ORG_NAME",
    "PERSON_NAME",
    "TITLE",
    "ADDRESS",
    "POSTAL_CODE",
    "PHONE",
    "FAX",
    "WEBSITE",
    "BANK_NAME",
    "BANK_ACCOUNT",
    "UNIFIED_CODE",
    "TEXT_DYNAMIC",
}

UPLOAD_FIRST_FIELD_TYPES = {
    "DATE",
    "AMOUNT",
    "PROJECT_NAME",
    "PROJECT_CODE",
    "SERVICE_PERIOD",
}


def _dedupe(sources: list[str]) -> list[str]:
    ordered: list[str] = []
    for source in sources:
        if source not in ordered:
            ordered.append(source)
    return ordered


def _source_priority(field: ExtractedField, knowledge_base: KnowledgeBase) -> list[str]:
    has_upload_rule = canonical_upload_field_name(field.field_name) is not None
    has_kb_rule = knowledge_base.has_field(field.field_name)

    if field.field_type in KB_FIRST_FIELD_TYPES:
        priority = ["KNOWLEDGE_BASE", "UPLOAD_DOC", "DYNAMIC"]
    elif field.field_type in UPLOAD_FIRST_FIELD_TYPES:
        priority = ["UPLOAD_DOC", "KNOWLEDGE_BASE", "DYNAMIC"]
    elif has_kb_rule and not has_upload_rule:
        priority = ["KNOWLEDGE_BASE", "UPLOAD_DOC", "DYNAMIC"]
    elif has_upload_rule and not has_kb_rule:
        priority = ["UPLOAD_DOC", "KNOWLEDGE_BASE", "DYNAMIC"]
    elif has_kb_rule and has_upload_rule:
        priority = ["KNOWLEDGE_BASE", "UPLOAD_DOC", "DYNAMIC"]
    else:
        priority = ["UPLOAD_DOC", "KNOWLEDGE_BASE", "DYNAMIC"]

    return _dedupe(priority)


def classify_fields(fields: list[ExtractedField], knowledge_base: KnowledgeBase) -> list[ExtractedField]:
    for field in fields:
        priority = _source_priority(field, knowledge_base)
        field.metadata["source_priority"] = priority
        field.source_type = priority[0]
    return fields
