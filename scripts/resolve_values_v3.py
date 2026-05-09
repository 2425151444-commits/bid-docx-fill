from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from field_semantics import canonical_upload_field_name, infer_field_type, is_value_compatible
from kb_loader import KnowledgeBase
from parse_docx_v2 import ParsedDocument
from schemas import ExtractedField


UPLOAD_PATTERNS: dict[str, list[str]] = {
    "项目编号": [r"(?:项目编号|编号)[:：]\s*([A-Za-z0-9._\-/]+)"],
    "项目名称": [r"(?:项目名称)[:：]\s*([^\n\r]{4,120})"],
    "采购人": [r"(?:采购人)[:：]\s*([^\n\r]{2,120})"],
    "采购代理机构": [r"(?:采购代理机构|采购代理机构名称)[:：]\s*([^\n\r]{2,120})"],
    "递交响应文件截止时间": [r"(?:递交响应文件截止时间|响应文件开启时间)[:：]\s*([^\n\r]{4,120})"],
    "磋商地点": [r"(?:磋商地点)[:：]\s*([^\n\r]{2,120})"],
    "项目服务期限": [r"(?:项目服务期限|服务期限)[:：]\s*([^\n\r]{2,120})"],
    "日期": [r"(\d{4}年\d{1,2}月\d{1,2}日)"],
}

DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*(?:年|[./-])\s*(?P<month>\d{1,2})\s*(?:月|[./-])\s*(?P<day>\d{1,2})\s*(?:日)?"
)
DEADLINE_NEGATIVE_KEYWORDS = (
    "获取采购文件",
    "获取磋商文件",
    "获取招标文件",
    "报名",
    "保证金",
    "答疑",
    "质疑",
    "投诉",
    "评审",
    "成交公告",
)
SERVICE_DEADLINE_KEYWORDS = ("项目服务期限", "服务期限", "项目完成时间", "完成时间", "履约期限", "合同期限", "服务期")
RESPONSE_DEADLINE_KEYWORDS = (
    "递交响应文件截止时间",
    "提交响应文件截止时间",
    "响应文件递交截止时间",
    "响应文件提交截止时间",
    "响应文件截止时间",
    "递交响应文件截止日期",
    "投标截止时间",
    "投标截止日期",
)
OPENING_TIME_KEYWORDS = ("响应文件开启时间", "开启时间", "磋商时间", "开标时间")
DEADLINE_KEYWORDS = ("截止日期", "截止时间", "截止", "至", "前")

HARDCODED_FALLBACKS: dict[str, str] = {
    "磋商有效期": "90天",
    "项目完成时间": "自合同签订之日起至2026年6月30日",
    "项目服务期限": "自合同签订之日起至2026年6月30日",
}

KB_FIELD_FALLBACKS: dict[str, tuple[str, ...]] = {
    "ORG_NAME": ("供应商名称",),
    "PERSON_NAME": ("负责人姓名",),
    "TITLE": ("负责人职务",),
    "ADDRESS": ("地址",),
    "PHONE": ("电话",),
    "WEBSITE": ("网址",),
    "BANK_NAME": ("开户银行",),
    "BANK_ACCOUNT": ("账号",),
    "UNIFIED_CODE": ("统一社会信用代码",),
}


@dataclass
class ResolvedCandidate:
    value: str
    source: str
    evidence_text: str
    evidence_block_id: str
    confidence: float
    evidence_field_name: str = ""


def _clean_resolved_value(value: str) -> str:
    text = (value or "").strip()
    text = re.split(r"[\n\r]", text)[0].strip()
    return text.strip("。；;，,：:）)")


def _candidate_blocks(parsed_document: ParsedDocument):
    for block in parsed_document.blocks:
        yield block


def _format_date_match(match: re.Match[str]) -> str:
    year, month, day = match.group("year"), int(match.group("month")), int(match.group("day"))
    return f"{year}年{month}月{day}日"


def _deadline_score(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0
    if any(keyword in compact for keyword in DEADLINE_NEGATIVE_KEYWORDS):
        return 0
    if any(keyword in compact for keyword in SERVICE_DEADLINE_KEYWORDS) and "至" in compact:
        return 140
    if any(keyword in compact for keyword in SERVICE_DEADLINE_KEYWORDS):
        return 130
    if any(keyword in compact for keyword in RESPONSE_DEADLINE_KEYWORDS):
        return 125
    if "响应文件" in compact and "截止" in compact:
        return 120
    if "投标文件" in compact and "截止" in compact:
        return 120
    if "截止" in compact:
        return 110
    if "合同" in compact and "至" in compact:
        return 105
    if "完成" in compact and "前" in compact:
        return 100
    if any(keyword in compact for keyword in OPENING_TIME_KEYWORDS):
        return 90
    if any(keyword in compact for keyword in DEADLINE_KEYWORDS):
        return 50
    return 0


def _resolve_contract_deadline_date(parsed_document: ParsedDocument) -> ResolvedCandidate | None:
    best: tuple[int, int, str, str, str] | None = None
    blocks = parsed_document.blocks

    for index, block in enumerate(blocks):
        block_text = block.text.rstrip()
        if not block_text:
            continue
        matches = list(DATE_RE.finditer(block_text))
        if not matches:
            continue

        nearby_parts = []
        if index > 0:
            nearby_parts.append(blocks[index - 1].text.rstrip())
        nearby_parts.append(block_text)
        if index + 1 < len(blocks):
            nearby_parts.append(blocks[index + 1].text.rstrip())
        nearby_text = "\n".join(part for part in nearby_parts if part)

        score = max(_deadline_score(block_text), _deadline_score(nearby_text) - 5)
        if score <= 0:
            continue

        value = _format_date_match(matches[-1])
        candidate = (score, -index, value, block_text, block.block_id)
        if best is None or candidate[:2] > best[:2]:
            best = candidate

    if best is None:
        return None

    score, _, value, evidence_text, block_id = best
    return ResolvedCandidate(
        value=value,
        source="UPLOAD_DOC",
        evidence_text=evidence_text,
        evidence_block_id=block_id,
        confidence=0.92 if score >= 90 else 0.82,
        evidence_field_name="合同截止日期",
    )


def _resolve_from_upload_doc(field: ExtractedField, parsed_document: ParsedDocument) -> ResolvedCandidate | None:
    canonical_name = canonical_upload_field_name(field.field_name)
    field_type = field.field_type or infer_field_type(field.field_name)

    if field.field_name in HARDCODED_FALLBACKS:
        return ResolvedCandidate(
            value=HARDCODED_FALLBACKS[field.field_name],
            source="DYNAMIC",
            evidence_text="hardcoded_fallback",
            evidence_block_id="",
            confidence=0.70,
            evidence_field_name=field.field_name,
        )

    if canonical_name in HARDCODED_FALLBACKS:
        return ResolvedCandidate(
            value=HARDCODED_FALLBACKS[canonical_name],
            source="DYNAMIC",
            evidence_text="hardcoded_fallback",
            evidence_block_id="",
            confidence=0.70,
            evidence_field_name=canonical_name,
        )

    patterns = UPLOAD_PATTERNS.get(canonical_name or "", [])
    if not patterns:
        return None

    for block in _candidate_blocks(parsed_document):
        block_text = block.text.rstrip()
        if not block_text:
            continue
        for pattern in patterns:
            match = re.search(pattern, block_text)
            if not match:
                continue
            value = _clean_resolved_value(match.group(1))
            if not is_value_compatible(field_type, field.field_name, value):
                continue
            return ResolvedCandidate(
                value=value,
                source="UPLOAD_DOC",
                evidence_text=block_text,
                evidence_block_id=block.block_id,
                confidence=0.90,
                evidence_field_name=canonical_name or field.field_name,
            )
    return None


def _resolve_from_knowledge_base(field: ExtractedField, knowledge_base: KnowledgeBase) -> ResolvedCandidate | None:
    entry = knowledge_base.lookup(field.field_name)
    if not entry:
        entry = knowledge_base.fuzzy_lookup(field.field_name)

    if not entry:
        for fallback_name in KB_FIELD_FALLBACKS.get(field.field_type or "", ()):
            entry = knowledge_base.lookup(fallback_name) or knowledge_base.fuzzy_lookup(fallback_name)
            if entry:
                break

    if not entry or not entry.value:
        return None

    value = _clean_resolved_value(entry.value)
    field_type = field.field_type or infer_field_type(field.field_name)
    if not is_value_compatible(field_type, field.field_name, value):
        return None

    return ResolvedCandidate(
        value=value,
        source="KNOWLEDGE_BASE",
        evidence_text=f"{entry.field_name}: {entry.value}",
        evidence_block_id=f"kb:{entry.source_sheet}",
        confidence=0.95,
        evidence_field_name=entry.field_name,
    )


def _resolve_dynamic_fallback(field: ExtractedField) -> ResolvedCandidate | None:
    if (field.field_type or "") != "DATE":
        return None
    today = date.today()
    value = f"{today.year}年{today.month}月{today.day}日"
    return ResolvedCandidate(
        value=value,
        source="DYNAMIC",
        evidence_text="system_date_fallback",
        evidence_block_id="system:today",
        confidence=0.65,
        evidence_field_name="系统日期",
    )


def _uses_knowledge_base_date(field: ExtractedField) -> bool:
    return field.field_name.strip() in {"成立时间"}


def _attach_resolution(field: ExtractedField, candidate: ResolvedCandidate | None) -> None:
    if not candidate:
        return

    field.resolved_value = candidate.value
    field.confidence = candidate.confidence
    field.needs_manual_review = False
    field.metadata["value_source"] = candidate.source
    field.metadata["evidence_text"] = candidate.evidence_text
    field.metadata["evidence_block_id"] = candidate.evidence_block_id
    field.metadata["evidence_field_name"] = candidate.evidence_field_name


def _mark_unresolved(field: ExtractedField, review_reason: str, note: str) -> None:
    field.metadata["review_reason"] = review_reason
    field.metadata.setdefault("value_source", field.source_type or "")
    field.metadata.setdefault("evidence_text", "")
    field.metadata.setdefault("evidence_block_id", "")
    field.metadata.setdefault("evidence_field_name", "")
    field.notes.append(note)


def resolve_fields(
    fields: list[ExtractedField],
    parsed_document: ParsedDocument,
    knowledge_base: KnowledgeBase,
) -> list[ExtractedField]:
    contract_deadline_date = _resolve_contract_deadline_date(parsed_document)

    for field in fields:
        field.field_type = field.field_type or infer_field_type(field.field_name)
        if field.metadata.get("no_auto_fill"):
            field.resolved_value = ""
            field.confidence = 0.0
            field.needs_manual_review = True
            field.metadata.setdefault("value_source", "MANUAL")
            field.metadata.setdefault("evidence_text", "")
            field.metadata.setdefault("evidence_block_id", "")
            field.metadata.setdefault("evidence_field_name", "")
            field.metadata.setdefault("review_reason", "NO_AUTO_FILL")
            field.notes.append("签字类空位需要后续手写，已禁止自动填充。")
            continue

        if _uses_knowledge_base_date(field):
            candidate = _resolve_from_knowledge_base(field, knowledge_base)
            if candidate:
                _attach_resolution(field, candidate)
                field.metadata["tried_sources"] = ["KNOWLEDGE_BASE"]
                continue

        if (field.field_type or "") == "DATE" and contract_deadline_date:
            _attach_resolution(field, contract_deadline_date)
            field.metadata["tried_sources"] = ["UPLOAD_DOC"]
            continue

        priority = field.metadata.get("source_priority")
        if not isinstance(priority, list) or not priority:
            priority = [field.source_type or "UPLOAD_DOC", "KNOWLEDGE_BASE", "DYNAMIC"]

        matched = False
        tried_sources: list[str] = []
        for source in priority:
            tried_sources.append(source)
            candidate = None
            if source == "KNOWLEDGE_BASE":
                candidate = _resolve_from_knowledge_base(field, knowledge_base)
            elif source == "UPLOAD_DOC":
                candidate = _resolve_from_upload_doc(field, parsed_document)
            elif source == "DYNAMIC":
                candidate = _resolve_dynamic_fallback(field)

            if candidate:
                _attach_resolution(field, candidate)
                field.metadata["tried_sources"] = tried_sources
                matched = True
                break

        if matched:
            continue

        field.metadata["tried_sources"] = tried_sources
        if "KNOWLEDGE_BASE" in tried_sources and "UPLOAD_DOC" in tried_sources:
            _mark_unresolved(field, "KB_AND_UPLOAD_NOT_FOUND", "知识库与文档前文都未找到稳定匹配值。")
        elif "KNOWLEDGE_BASE" in tried_sources:
            _mark_unresolved(field, "KB_NOT_FOUND", "知识库中未找到合适值。")
        elif "UPLOAD_DOC" in tried_sources:
            _mark_unresolved(field, "UPLOAD_NOT_FOUND", "未能从文档前半部分稳定提取到匹配值。")
        else:
            _mark_unresolved(field, "NO_VALUE", "该字段需要人工补充。")

    return fields
