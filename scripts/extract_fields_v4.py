from __future__ import annotations

import re

from field_semantics import infer_field_type, looks_like_date_placeholder, looks_like_placeholder
from parse_docx_v2 import ParsedDocument
from schemas import ExtractedField, normalize_field_name
from scope_detector import ScopeDetection, detect_fill_scope, iter_fill_scope_blocks as iter_detected_scope_blocks


SLOT_CHARS = "._·•…—-＿Xx"
SLOT_CLUSTER = rf"(?:[{re.escape(SLOT_CHARS)}]{{2,}}|[ \u00A0]{{4,}})"
DATE_SLOT_CLUSTER = rf"(?:[ _{re.escape(SLOT_CHARS)}]{{0,120}}年\s*[ _{re.escape(SLOT_CHARS)}]{{0,80}}月\s*[ _{re.escape(SLOT_CHARS)}]{{0,80}}日|XX年XX月XX日|{SLOT_CLUSTER})"

BLANK_RE = re.compile(DATE_SLOT_CLUSTER)
COLON_LABEL_RE = re.compile(r"^(?P<label>[^:：]{1,60})[:：](?P<rest>.*)$")
INLINE_HINT_AFTER_RE = re.compile(
    rf"(?P<blank>{SLOT_CLUSTER})\s*[（(]\s*(?P<hint>[^（）()]{2,40})\s*[）)]"
)
QUOTED_PROJECT_NAME_RE = re.compile(
    rf"[“\"](?P<blank>{SLOT_CLUSTER})[”\"]\s*项目"
)
NAKED_PROJECT_TITLE_RE = re.compile(r"^\s*[Xx＿_]{2,}\s*项目\s*$")
ANCHOR_HINT_RE = re.compile(
    rf"(?P<before>{SLOT_CLUSTER})?"
    rf"\s*[（(]\s*(?P<hint>[^（）()：:\s]{{2,40}})"
    rf"(?P<after_inner>{SLOT_CLUSTER})?\s*[）)]"
    rf"(?P<after_outer>\s*(?:(?:[:：]\s*(?:{SLOT_CLUSTER})?)|{SLOT_CLUSTER}|[“\"]{SLOT_CLUSTER}[”\"]?))?"
)
BEFORE_HINT_SLOT_RE = re.compile(
    rf"(?P<blank>(?:[Xx＿_]{{2,}}|[ \u00A0]{{4,}}))\s*[（(]\s*(?P<hint>[^（）()：:\s]{{2,40}})\s*[）)]"
)
BARE_PARENTHESES_HINT_RE = re.compile(
    r"[（(]\s*(?P<hint>项目编号|采购项目编号|项目名称|供应商名称|供应商全称|采购人名称|采购代理机构名称|职务名称)\s*[）)]"
)
PAREN_COLON_HINT_RE = re.compile(
    r"[（(]\s*(?P<hint>项目编号|采购项目编号|项目名称|供应商名称|供应商全称|采购人名称|采购代理机构名称)\s*[:：]\s*(?P<blank>[Xx＿_]{2,}|[ \u00A0]{4,})\s*[）)]"
)
X_BEFORE_HINT_RE = re.compile(
    r"(?P<blank>[Xx＿_]{2,})\s*[（(]\s*(?P<hint>[^（）()：:\s]{2,40})\s*[）)]"
)
SENTENCE_CLAUSE_RE = re.compile(
    rf"(?P<prefix>[^。；;\n]{{0,120}}?)(?P<marker>(?:为|是)\s*(?P<blank>{DATE_SLOT_CLUSTER}))"
)


SKIP_FIELD_NAMES = {
    "盖章",
    "盖单位章",
    "盖单位公章",
    "签字",
    "签章",
    "附",
    "备注",
}

SKIP_HINT_NAMES = {
    "盖章",
    "盖单位章",
    "盖单位公章",
    "签字",
    "签章",
}

CONTAINER_LABELS = {
    "本授权声明",
    "本授权书",
    "声明",
}

TABLE_SKIP_LABELS = {
    "其中",
    "证书名称",
    "级别",
    "证号",
    "专业",
    "类别",
    "序号",
    "备注",
}

NOTE_KEYWORDS = (
    "盖章",
    "盖单位章",
    "公章",
    "签字",
    "签章",
    "全称并盖章",
)

FIELD_LABEL_BLACKLIST = (
    "如下",
    "说明",
    "要求如下",
    "承诺如下",
)

NOTE_PREFIXES = (
    "注",
    "说明",
    "备注",
    "注意",
)

FORM_SECTION_HEADING_KEYWORDS = (
    "技术、服务要求应答表",
    "技术服务要求应答表",
    "商务应答表",
    "商务要求响应",
    "商务要求偏离",
    "类似项目业绩",
    "项目管理、技术、服务人员",
    "本项目管理、技术、服务人员",
    "人员情况表",
    "报价表",
)

SCOPE_START_KEYWORDS = (
    "响应文件格式",
    "响应文件组成格式",
    "响应文件编制格式",
    "响应文件模板",
    "响应文件格式模板",
)

SCOPE_END_KEYWORDS = (
    "合同主要条款",
    "合同条款",
    "主要合同条款",
    "合同格式",
    "合同协议书",
    "政府采购合同",
    "采购合同",
    "合同书",
)

POST_RESPONSE_SCOPE_END_KEYWORDS = (
    "供应商的资格",
    "供应商应当提供的资格",
    "资格、资质性",
    "采购需求",
    "评审办法",
    "政府采购合同",
    "采购合同",
)

SIGNATURE_MANUAL_KEYWORDS = (
    "签字",
    "签名",
)

OPTIONAL_STATEMENT_SKIP_KEYWORDS = (
    "中小企业声明",
    "中小企业声明函",
    "中小企业（监狱企业）声明函",
    "中小企业(监狱企业)声明函",
    "监狱企业证明材料",
    "残疾人福利性单位声明函",
)


def _clean_name(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    return text.strip("()（）:：；;。.")


def _contains_note_keyword(text: str) -> bool:
    return any(keyword in (text or "") for keyword in NOTE_KEYWORDS)


def _strip_trailing_note(label: str) -> tuple[str, str]:
    text = (label or "").strip()
    patterns = [
        r"^(?P<base>.+?)[（(](?P<note>[^（）()]*)[）)]$",
        r"^(?P<base>.+?)[（(](?P<note>[^（）()]*)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        base = _clean_name(match.group("base"))
        note = _clean_name(match.group("note"))
        if base and note and _contains_note_keyword(note):
            return base, note
    return _clean_name(text), ""


def _skip_field(cleaned: str) -> bool:
    if not cleaned:
        return True
    if cleaned in SKIP_FIELD_NAMES:
        return True
    if _contains_note_keyword(cleaned) and len(cleaned) <= 12:
        return True
    return looks_like_placeholder(cleaned) or looks_like_date_placeholder(cleaned)


def _skip_hint_field(cleaned: str) -> bool:
    if not cleaned:
        return True
    return cleaned in SKIP_HINT_NAMES or (_contains_note_keyword(cleaned) and len(cleaned) <= 12)


def _looks_like_field_label(label: str) -> bool:
    cleaned, _ = _strip_trailing_note(label)
    if not cleaned or len(cleaned) > 40:
        return False
    if any(token in cleaned for token in FIELD_LABEL_BLACKLIST):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fa5/\-（）()、“”\"·\s]+", cleaned))


def _is_signature_manual_field(field_name: str, trailing_note: str = "") -> bool:
    text = f"{field_name}{trailing_note}"
    return any(keyword in text for keyword in SIGNATURE_MANUAL_KEYWORDS)


def _compact_scope_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _is_toc_entry(text: str) -> bool:
    return bool(re.match(r"^第[一二三四五六七八九十0-9]+章.+\d{1,4}$", text))


def _is_real_chapter_heading(block) -> bool:
    text = _compact_scope_text(block.text)
    if not text or _is_toc_entry(text):
        return False
    return bool(re.match(r"^第[一二三四五六七八九十0-9]+章", text))


def _looks_like_scope_heading(block) -> bool:
    text = _compact_scope_text(block.text)
    if not text:
        return False
    if _is_toc_entry(text):
        return False
    if len(text) <= 40:
        return True
    return bool(
        _is_real_chapter_heading(block)
        or re.match(r"^第[一二三四五六七八九十0-9]+节", text)
    )


def _is_scope_start(block) -> bool:
    text = _compact_scope_text(block.text)
    if not text or _is_toc_entry(text):
        return False

    if not _looks_like_scope_heading(block):
        return False

    if re.search(r"第[一二三四五六七八九十0-9]+章.*响应文件.*格式", text):
        return True

    if re.match(r"^文件[一二三四五六七八九十0-9]+[、.．].*响应文件.*格式$", text):
        return True

    return text in SCOPE_START_KEYWORDS


def _eligible_scope_end_block_ids(parsed_document: ParsedDocument, count: int = 2) -> set[str]:
    chapter_block_ids = [
        block.block_id for block in parsed_document.blocks if _is_real_chapter_heading(block)
    ]
    eligible = set(chapter_block_ids[-count:])

    tail_start = int(len(parsed_document.blocks) * 0.7)
    for block in parsed_document.blocks[tail_start:]:
        text = _compact_scope_text(block.text)
        if _looks_like_scope_heading(block) and any(keyword in text for keyword in SCOPE_END_KEYWORDS):
            eligible.add(block.block_id)

    return eligible


def _is_scope_end(block, eligible_scope_end_block_ids: set[str]) -> bool:
    text = _compact_scope_text(block.text)
    if _is_real_chapter_heading(block) and any(keyword in text for keyword in POST_RESPONSE_SCOPE_END_KEYWORDS):
        return True

    if block.block_id not in eligible_scope_end_block_ids:
        return False
    if not _looks_like_scope_heading(block):
        return False
    if re.match(r"^第[一二三四五六七八九十0-9]+章.*合同.*条款$", text):
        return True
    return any(keyword in text for keyword in SCOPE_END_KEYWORDS)


def _is_optional_statement_skip_heading(block) -> bool:
    text = _compact_scope_text(block.text)
    if not text or not _looks_like_scope_heading(block):
        return False
    if any(keyword in text for keyword in OPTIONAL_STATEMENT_SKIP_KEYWORDS):
        return True
    has_statement_word = "声明" in text or "证明材料" in text or "声明函" in text
    if not has_statement_word:
        return False
    return (
        "中小企业" in text
        or "监狱企业" in text
        or "监狱" in text
        or "残疾人福利" in text
        or "残疾人福利性单位" in text
    )


def _is_section_transition_heading(block) -> bool:
    text = _compact_scope_text(block.text)
    if not text:
        return False
    if _is_real_chapter_heading(block):
        return True
    if re.match(r"^格式\d+[-－—]\d+", text):
        return True
    if re.match(r"^文件[一二三四五六七八九十0-9]+[、.．]", text):
        return True
    if text.startswith(("附件", "附表")):
        return True
    if any(keyword in text for keyword in FORM_SECTION_HEADING_KEYWORDS):
        return True
    return bool(re.match(r"^[（(][一二三四五六七八九十0-9]+[）)]", text))


def _iter_fill_scope_blocks(parsed_document: ParsedDocument, scope_detection: ScopeDetection | None = None):
    in_optional_statement_skip = False
    detection = scope_detection or detect_fill_scope(parsed_document)
    active_section_name = detection.start_text

    for block in iter_detected_scope_blocks(parsed_document, detection):

        if in_optional_statement_skip:
            if _is_section_transition_heading(block) and not _is_optional_statement_skip_heading(block):
                in_optional_statement_skip = False
                active_section_name = block.text.strip() or block.section_name
            else:
                continue

        if _is_section_transition_heading(block):
            active_section_name = block.text.strip() or block.section_name

        if _is_optional_statement_skip_heading(block):
            in_optional_statement_skip = True
            continue

        if active_section_name:
            block.section_name = active_section_name

        yield block


def fill_scope_block_ids(
    parsed_document: ParsedDocument,
    scope_detection: ScopeDetection | None = None,
) -> set[str]:
    return {block.block_id for block in _iter_fill_scope_blocks(parsed_document, scope_detection)}


def _append_field(
    fields: list[ExtractedField],
    seen: set[tuple[str, str]],
    block_id: str,
    block_type: str,
    section_name: str,
    field_name: str,
    context_text: str,
    detected_by: str,
    metadata: dict | None = None,
) -> None:
    cleaned, trailing_note = _strip_trailing_note(field_name)
    if len(cleaned) < 1 or _skip_field(cleaned):
        return

    normalized = normalize_field_name(cleaned)
    dedupe_key = (block_id, normalized)
    if metadata and metadata.get("allow_duplicate"):
        dedupe_key = (block_id, normalized, str(metadata.get("occurrence_index", len(fields))))
    if dedupe_key in seen:
        return

    seen.add(dedupe_key)
    merged_metadata = dict(metadata or {})
    if trailing_note:
        merged_metadata["trailing_note"] = trailing_note
    if _is_signature_manual_field(cleaned, trailing_note):
        merged_metadata["no_auto_fill"] = True
        merged_metadata["review_reason"] = "SIGNATURE_REQUIRES_HANDWRITING"

    fields.append(
        ExtractedField(
            field_id=f"{block_id}-{len(fields) + 1}",
            field_name=cleaned,
            normalized_name=normalized,
            section_name=section_name,
            block_id=block_id,
            block_type=block_type,
            context_text=context_text,
            detected_by=detected_by,
            metadata=merged_metadata,
            field_type=infer_field_type(cleaned),
        )
    )


def _detect_label_rule(label: str, rest: str, block_type: str) -> str | None:
    cleaned_label, _ = _strip_trailing_note(label)
    if not cleaned_label or not _looks_like_field_label(cleaned_label):
        return None
    if re.search(SLOT_CLUSTER, label) and re.search(r"[（(].+[）)]", label):
        return None

    if rest.strip():
        if infer_field_type(cleaned_label) == "DATE" and looks_like_date_placeholder(rest):
            return "date_colon_blank"
        if BLANK_RE.search(rest):
            return "colon_blank"
        return None

    if infer_field_type(cleaned_label) == "DATE":
        return "date_label_only"
    if block_type == "table_paragraph":
        return "table_label_only"
    return "label_only"


def _is_note_or_instruction_paragraph(text: str) -> bool:
    compact = _compact_scope_text(text)
    if not compact:
        return False
    return any(compact.startswith(f"{prefix}:") or compact.startswith(f"{prefix}：") for prefix in NOTE_PREFIXES)


def _extract_anchor_hints(text: str) -> list[tuple[str, str]]:
    anchors: list[tuple[str, str]] = []
    for match in ANCHOR_HINT_RE.finditer(text):
        hint, trailing_note = _strip_trailing_note(match.group("hint"))
        if trailing_note:
            continue
        if _skip_hint_field(hint):
            continue

        before = match.group("before") or ""
        after_inner = match.group("after_inner") or ""
        after_outer = match.group("after_outer") or ""
        head = text[: match.start()]
        tail = text[match.end() :]

        has_before = bool(before.strip())
        has_after_inner = bool(after_inner.strip())
        has_after_outer_slot = bool(re.search(SLOT_CLUSTER, after_outer))
        has_after_colon = ":" in after_outer or "：" in after_outer
        prev_chunk = head.rstrip()
        prev_ends_with_colon = prev_chunk.endswith((":", "："))
        next_chunk = tail.lstrip()
        next_starts_with_hint = next_chunk.startswith(("(", "（"))

        fill_side = ""
        if prev_ends_with_colon and next_starts_with_hint:
            fill_side = "before_insert"
        elif (not has_before) and (not has_after_inner) and (not has_after_outer_slot) and (not prev_chunk or prev_ends_with_colon):
            fill_side = "before_insert"
        elif has_before and not has_after_inner and not has_after_outer_slot:
            fill_side = "before"
        elif has_before and has_after_outer_slot and next_starts_with_hint:
            fill_side = "before"
        elif has_after_outer_slot:
            fill_side = "after_outer"
        elif has_after_colon:
            fill_side = "after_colon"
        elif has_after_inner:
            fill_side = "after_inner"
        elif has_before:
            fill_side = "before"

        if not fill_side:
            continue
        anchors.append((hint, fill_side))

    return anchors


def _extract_before_hint_slots(text: str) -> list[tuple[str, str]]:
    anchors: list[tuple[str, str]] = []
    for match in BEFORE_HINT_SLOT_RE.finditer(text):
        hint, trailing_note = _strip_trailing_note(match.group("hint"))
        if trailing_note:
            continue
        if _skip_hint_field(hint):
            continue
        anchors.append((hint, "before"))
    return anchors


def _extract_x_before_hints(text: str) -> list[tuple[str, str]]:
    anchors: list[tuple[str, str]] = []
    for match in X_BEFORE_HINT_RE.finditer(text):
        hint, trailing_note = _strip_trailing_note(match.group("hint"))
        if trailing_note:
            continue
        if _skip_hint_field(hint):
            continue
        anchors.append((hint, "before"))

    return anchors


def _extract_inline_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in INLINE_HINT_AFTER_RE.finditer(text):
        hint, trailing_note = _strip_trailing_note(match.group("hint"))
        if trailing_note:
            continue
        if _skip_hint_field(hint):
            continue
        hints.append(hint)

    if QUOTED_PROJECT_NAME_RE.search(text):
        hints.append("项目名称")

    ordered: list[str] = []
    for hint in hints:
        if hint not in ordered:
            ordered.append(hint)
    return ordered


def _extract_bare_parentheses_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in BARE_PARENTHESES_HINT_RE.finditer(text):
        hint, trailing_note = _strip_trailing_note(match.group("hint"))
        if trailing_note:
            continue
        if _skip_hint_field(hint):
            continue
        hints.append(hint)

    ordered: list[str] = []
    for hint in hints:
        if hint not in ordered:
            ordered.append(hint)
    return ordered


def _extract_parenthesized_colon_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in PAREN_COLON_HINT_RE.finditer(text):
        hint, trailing_note = _strip_trailing_note(match.group("hint"))
        if trailing_note:
            continue
        if _skip_hint_field(hint):
            continue
        hints.append(hint)

    ordered: list[str] = []
    for hint in hints:
        if hint not in ordered:
            ordered.append(hint)
    return ordered


def _trim_clause_prefix(prefix: str) -> str:
    text = (prefix or "").strip()
    parts = [part.strip() for part in re.split(r"[，。、；;:\n]", text) if part.strip()]
    if parts:
        text = parts[-1]
    text = re.sub(r"^\d+[、.]\s*", "", text)
    return text.strip()


def _best_sentence_label(prefix: str) -> str:
    clause = _trim_clause_prefix(prefix)
    if not clause:
        return ""

    candidates: list[str] = []
    cleaned_clause, _ = _strip_trailing_note(clause)
    if _looks_like_field_label(cleaned_clause):
        candidates.append(cleaned_clause)

    max_len = min(len(clause), 20)
    for start in range(max(0, len(clause) - max_len), len(clause)):
        candidate, _ = _strip_trailing_note(clause[start:])
        if _looks_like_field_label(candidate):
            candidates.append(candidate)

    best = ""
    best_score = -1
    for candidate in candidates:
        field_type = infer_field_type(candidate)
        if field_type == "TEXT":
            continue
        score = len(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    return _clean_name(best)


def _extract_paragraph_fields(fields: list[ExtractedField], seen: set[tuple[str, str]], block) -> None:
    text = block.text.strip()
    if not text:
        return
    if _is_note_or_instruction_paragraph(text):
        return

    anchor_hints = _extract_before_hint_slots(text)
    explicit_anchor_names = {normalize_field_name(name) for name, _ in anchor_hints}
    for anchor in _extract_anchor_hints(text):
        if normalize_field_name(anchor[0]) not in explicit_anchor_names:
            anchor_hints.append(anchor)
    for anchor in _extract_x_before_hints(text):
        if normalize_field_name(anchor[0]) not in explicit_anchor_names and anchor not in anchor_hints:
            anchor_hints.append(anchor)
    inline_hints = _extract_inline_hints(text)
    bare_parentheses_hints = _extract_bare_parentheses_hints(text)
    parenthesized_colon_hints = _extract_parenthesized_colon_hints(text)

    if NAKED_PROJECT_TITLE_RE.match(text):
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            "项目名称",
            block.text,
            "naked_project_title",
        )
        return

    match = COLON_LABEL_RE.match(text)
    if match:
        label = match.group("label").strip()
        rest = match.group("rest")
        detected_by = _detect_label_rule(label, rest, block.block_type)
        cleaned_label, _ = _strip_trailing_note(label)
        anchor_names = {normalize_field_name(name) for name, _ in anchor_hints}
        should_append_label = bool(detected_by)
        if should_append_label and (
            normalize_field_name(cleaned_label) in anchor_names
            or (
                (anchor_hints or inline_hints)
                and (cleaned_label in CONTAINER_LABELS or len(anchor_hints) + len(inline_hints) >= 2)
            )
        ):
            should_append_label = False

        if should_append_label:
            _append_field(
                fields,
                seen,
                block.block_id,
                block.block_type,
                block.section_name,
                label,
                block.text,
                detected_by,
            )

    for occurrence_index, (hint, fill_side) in enumerate(anchor_hints):
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            hint,
            block.text,
            "anchor_hint",
            metadata={
                "fill_side": fill_side,
                "allow_duplicate": True,
                "occurrence_index": occurrence_index,
            },
        )

    for hint in inline_hints:
        detected_by = "quoted_project_name" if hint == "项目名称" and QUOTED_PROJECT_NAME_RE.search(text) else "inline_hint_after"
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            hint,
            block.text,
            detected_by,
        )

    existing_hint_names = {
        normalize_field_name(hint)
        for hint in [*(name for name, _ in anchor_hints), *inline_hints]
    }
    for hint in bare_parentheses_hints:
        if normalize_field_name(hint) in existing_hint_names:
            continue
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            hint,
            block.text,
            "bare_parentheses_hint",
            metadata={"replace_hint": True},
        )

    existing_hint_names.update(normalize_field_name(hint) for hint in bare_parentheses_hints)
    for hint in parenthesized_colon_hints:
        if normalize_field_name(hint) in existing_hint_names:
            continue
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            hint,
            block.text,
            "anchor_hint",
            metadata={"fill_side": "inside_colon"},
        )

    for sentence_match in SENTENCE_CLAUSE_RE.finditer(text):
        label = _best_sentence_label(sentence_match.group("prefix"))
        if not label:
            continue
        field_type = infer_field_type(label)
        if field_type == "TEXT":
            continue
        detected_by = "sentence_date_blank" if field_type == "DATE" else "sentence_blank"
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            label,
            block.text,
            detected_by,
        )

    if block.block_type == "table_paragraph" and text.endswith(("：", ":")):
        _append_field(
            fields,
            seen,
            block.block_id,
            block.block_type,
            block.section_name,
            text[:-1],
            block.text,
            "table_label_only",
        )


def _looks_like_empty_cell(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return looks_like_placeholder(stripped) or looks_like_date_placeholder(stripped)


def _extract_table_fields(fields: list[ExtractedField], seen: set[tuple[str, str]], block) -> None:
    cells = block.metadata.get("cells", [])
    if not cells:
        return

    for index in range(len(cells) - 1):
        label = cells[index].strip()
        value = cells[index + 1].strip()
        if not label:
            continue
        if label in TABLE_SKIP_LABELS:
            continue
        if _looks_like_empty_cell(value):
            _append_field(
                fields,
                seen,
                block.block_id,
                block.block_type,
                block.section_name,
                label,
                block.text,
                "table_label_value_pair",
                metadata={"target_cell_index": index + 1},
            )


def extract_fields(
    parsed_document: ParsedDocument,
    scope_detection: ScopeDetection | None = None,
) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    seen: set[tuple[str, str]] = set()

    for block in _iter_fill_scope_blocks(parsed_document, scope_detection):
        if block.block_type in {"paragraph", "table_paragraph"}:
            _extract_paragraph_fields(fields, seen, block)
        elif block.block_type == "table_row":
            _extract_table_fields(fields, seen, block)

    return fields
