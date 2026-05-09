from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from schemas import ExtractedField


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS}
SOURCE_STYLES = {
    "UPLOAD_DOC": "1F4E79",
    "KNOWLEDGE_BASE": "008000",
    "MANUAL": "C00000",
}

SLOT_CHARS = "._·•…—-＿Xx"
VISIBLE_SLOT_CHARS = "._·•…—-＿"
PLACEHOLDER_CHARS = SLOT_CHARS + "()（）[] "
SLOT_CLUSTER = rf"(?:[{re.escape(SLOT_CHARS)}]{{2,300}}|[ \u00A0]{{4,300}})"
DATE_SLOT_CLUSTER = rf"(?:[ _{re.escape(SLOT_CHARS)}]{{0,120}}年\s*[ _{re.escape(SLOT_CHARS)}]{{0,80}}月\s*[ _{re.escape(SLOT_CHARS)}]{{0,80}}日|XX年XX月XX日|{SLOT_CLUSTER})"
PLACEHOLDER_RE = re.compile(rf"[\s{re.escape(PLACEHOLDER_CHARS)}]{{2,}}")
VISIBLE_PLACEHOLDER_RE = re.compile(
    rf"(?:"
    rf"[ _{re.escape(VISIBLE_SLOT_CHARS)}Xx]{{0,120}}年\s*[ _{re.escape(VISIBLE_SLOT_CHARS)}Xx]{{0,80}}月\s*[ _{re.escape(VISIBLE_SLOT_CHARS)}Xx]{{0,80}}日"
    rf"|XX年XX月XX日"
    rf"|[{re.escape(VISIBLE_SLOT_CHARS)}]{{2,300}}"
    rf"|[ \u00A0]{{6,300}}"
    rf")"
)
NOTE_PREFIX_RE = re.compile(r"^\s*(?:注|说明|备注|注意)\s*[:：]")


def _w_tag(local_name: str) -> str:
    return f"{{{W_NS}}}{local_name}"


def _field_name_pattern(field_name: str) -> str:
    chars = [re.escape(char) for char in field_name if not char.isspace()]
    return r"[\s\u00A0]*".join(chars)


def _read_text(element: etree._Element) -> str:
    return "".join(element.xpath(".//w:t/text()", namespaces=NS))


def _iter_text_nodes(paragraph_or_cell: etree._Element) -> list[etree._Element]:
    return paragraph_or_cell.xpath(".//w:t", namespaces=NS)


def _set_text_node(node: etree._Element, text: str) -> None:
    node.text = text
    if text.startswith(" ") or text.endswith(" "):
        node.set(f"{{{XML_NS}}}space", "preserve")
    else:
        node.attrib.pop(f"{{{XML_NS}}}space", None)


def _text_node_run(node: etree._Element) -> etree._Element | None:
    parent = node.getparent()
    while parent is not None:
        if parent.tag == _w_tag("r"):
            return parent
        parent = parent.getparent()
    return None


def _ensure_run_properties(run: etree._Element) -> etree._Element:
    rpr = run.find("./w:rPr", namespaces=NS)
    if rpr is None:
        rpr = etree.Element(_w_tag("rPr"))
        run.insert(0, rpr)
    return rpr


def _set_child_val(parent: etree._Element, local_name: str, value: str) -> etree._Element:
    child = parent.find(f"./w:{local_name}", namespaces=NS)
    if child is None:
        child = etree.SubElement(parent, _w_tag(local_name))
    child.set(_w_tag("val"), value)
    return child


def _style_run_for_source(run: etree._Element, source: str) -> None:
    color = SOURCE_STYLES.get(source)
    if not color:
        return

    rpr = _ensure_run_properties(run)
    _set_child_val(rpr, "b", "1")
    _set_child_val(rpr, "color", color)

    underline = rpr.find("./w:u", namespaces=NS)
    if underline is not None:
        underline.set(_w_tag("color"), color)
    elif source == "MANUAL":
        underline = etree.SubElement(rpr, _w_tag("u"))
        underline.set(_w_tag("val"), "single")
        underline.set(_w_tag("color"), color)


def _source_style_for_field(field: ExtractedField) -> str:
    source = str(field.metadata.get("value_source") or field.source_type or "").strip()
    if source == "UPLOAD_DOC":
        return "UPLOAD_DOC"
    if source == "KNOWLEDGE_BASE":
        return "KNOWLEDGE_BASE"
    return "MANUAL"


def _new_run_like(source_run: etree._Element, text: str, source_style: str | None = None) -> etree._Element:
    new_run = etree.Element(source_run.tag, nsmap=source_run.nsmap)
    rpr = source_run.find("./w:rPr", namespaces=NS)
    if rpr is not None:
        new_run.append(deepcopy(rpr))
    text_node = etree.SubElement(new_run, _w_tag("t"))
    _set_text_node(text_node, text)
    if source_style:
        _style_run_for_source(new_run, source_style)
    return new_run


def _insert_run_after(reference_run: etree._Element, new_run: etree._Element) -> None:
    parent = reference_run.getparent()
    if parent is None:
        return
    parent.insert(parent.index(reference_run) + 1, new_run)


def _style_text_span(text_nodes: list[etree._Element], start: int, end: int, source_style: str) -> bool:
    if start == end:
        return False

    chunks = [node.text or "" for node in text_nodes]
    cursor = 0
    styled = False
    for node, chunk in zip(text_nodes, chunks):
        next_cursor = cursor + len(chunk)
        if cursor < end and next_cursor > start:
            run = _text_node_run(node)
            if run is not None:
                _style_run_for_source(run, source_style)
                styled = True
        cursor = next_cursor
    return styled


def _write_span_with_styled_replacement(
    text_nodes: list[etree._Element],
    start: int,
    end: int,
    replacement: str,
    source_style: str | None = None,
) -> bool:
    if not text_nodes:
        return False
    if not source_style:
        return _write_span_preserve_nodes(text_nodes, start, end, replacement)

    chunks = [node.text or "" for node in text_nodes]
    total_len = sum(len(chunk) for chunk in chunks)
    if start < 0 or end < start or end > total_len:
        return False

    ranges: list[tuple[etree._Element, int, int, str]] = []
    cursor = 0
    for node, chunk in zip(text_nodes, chunks):
        next_cursor = cursor + len(chunk)
        ranges.append((node, cursor, next_cursor, chunk))
        cursor = next_cursor

    affected = [item for item in ranges if item[1] < end and item[2] > start]
    if not affected:
        for node, node_start, node_end, chunk in ranges:
            if node_start <= start <= node_end:
                run = _text_node_run(node)
                if run is None:
                    return False
                offset = start - node_start
                before = chunk[:offset]
                after = chunk[offset:]
                _set_text_node(node, before)
                replacement_run = _new_run_like(run, replacement, source_style)
                _insert_run_after(run, replacement_run)
                if after:
                    _insert_run_after(replacement_run, _new_run_like(run, after))
                return True
        return False

    first_node, first_start, _, first_chunk = affected[0]
    last_node, last_start, _, last_chunk = affected[-1]
    first_offset = max(0, start - first_start)
    last_offset = max(0, end - last_start)
    before = first_chunk[:first_offset]
    after = last_chunk[last_offset:]
    first_run = _text_node_run(first_node)
    if first_run is None:
        return False

    if first_node is last_node:
        if not before and not after:
            _set_text_node(first_node, replacement)
            _style_run_for_source(first_run, source_style)
            return True

        _set_text_node(first_node, before)
        replacement_run = _new_run_like(first_run, replacement, source_style)
        _insert_run_after(first_run, replacement_run)
        if after:
            _insert_run_after(replacement_run, _new_run_like(first_run, after))
        return True

    _set_text_node(first_node, before)
    replacement_run = _new_run_like(first_run, replacement, source_style)
    _insert_run_after(first_run, replacement_run)

    for node, _, _, _ in affected[1:-1]:
        _set_text_node(node, "")
    _set_text_node(last_node, after)
    return True


def _ensure_cell_text_nodes(cell: etree._Element) -> list[etree._Element]:
    text_nodes = _iter_text_nodes(cell)
    if text_nodes:
        return text_nodes

    paragraph = cell.find("./w:p", namespaces=NS)
    if paragraph is None:
        paragraph = etree.SubElement(cell, _w_tag("p"))

    run = paragraph.find("./w:r", namespaces=NS)
    if run is None:
        run = etree.SubElement(paragraph, _w_tag("r"))

    text_node = run.find("./w:t", namespaces=NS)
    if text_node is None:
        text_node = etree.SubElement(run, _w_tag("t"))
        text_node.text = ""

    return [text_node]


def _write_back_preserve_nodes(
    text_nodes: list[etree._Element],
    original_chunks: list[str],
    updated_text: str,
) -> bool:
    if not text_nodes:
        return False

    cursor = 0
    last_index = len(text_nodes) - 1
    for index, (node, original_chunk) in enumerate(zip(text_nodes, original_chunks)):
        if index == last_index:
            segment = updated_text[cursor:]
        else:
            chunk_len = len(original_chunk)
            segment = updated_text[cursor : cursor + chunk_len]
            cursor += chunk_len
        _set_text_node(node, segment)
    return True


def _write_span_preserve_nodes(
    text_nodes: list[etree._Element],
    start: int,
    end: int,
    replacement: str,
) -> bool:
    if not text_nodes:
        return False

    chunks = [node.text or "" for node in text_nodes]
    total_len = sum(len(chunk) for chunk in chunks)
    if start < 0 or end < start or end > total_len:
        return False

    ranges: list[tuple[etree._Element, int, int, str]] = []
    cursor = 0
    for node, chunk in zip(text_nodes, chunks):
        next_cursor = cursor + len(chunk)
        ranges.append((node, cursor, next_cursor, chunk))
        cursor = next_cursor

    affected = [
        item for item in ranges if item[1] < end and item[2] > start
    ]
    if not affected:
        for item in ranges:
            node, node_start, node_end, chunk = item
            if node_start <= start <= node_end:
                offset = start - node_start
                _set_text_node(node, chunk[:offset] + replacement + chunk[offset:])
                return True
        return False

    first_node, first_start, _, first_chunk = affected[0]
    _, last_start, _, last_chunk = affected[-1]
    first_offset = max(0, start - first_start)
    last_offset = max(0, end - last_start)
    before = first_chunk[:first_offset]
    after = last_chunk[last_offset:]
    _set_text_node(first_node, before + replacement + after)

    for node, _, _, _ in affected[1:]:
        _set_text_node(node, "")

    return True


def _changed_span(before: str, after: str) -> tuple[int, int, str]:
    prefix_len = 0
    min_len = min(len(before), len(after))
    while prefix_len < min_len and before[prefix_len] == after[prefix_len]:
        prefix_len += 1

    suffix_len = 0
    before_remaining = len(before) - prefix_len
    after_remaining = len(after) - prefix_len
    while (
        suffix_len < before_remaining
        and suffix_len < after_remaining
        and before[len(before) - 1 - suffix_len] == after[len(after) - 1 - suffix_len]
    ):
        suffix_len += 1

    old_end = len(before) - suffix_len
    new_end = len(after) - suffix_len
    return prefix_len, old_end, after[prefix_len:new_end]


def _replace_once(text: str, pattern: re.Pattern[str], replacement_value: str) -> tuple[str, bool]:
    match = pattern.search(text)
    if not match:
        return text, False

    value = replacement_value.strip()
    if not value:
        return text, False

    start, end = match.span("blank")
    return text[:start] + value + text[end:], True


def _format_fill(value: str) -> str:
    stripped = value.strip()
    return f" {stripped} " if stripped else stripped


def _replace_colon_placeholder(text: str, field_name: str, replacement_value: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(?P<prefix>{_field_name_pattern(field_name)}\s*[:：])(?P<blank>\s*{SLOT_CLUSTER})"
    )
    return _replace_once(text, pattern, _format_fill(replacement_value))


def _replace_trailing_note_label(
    text: str,
    field_name: str,
    trailing_note: str,
    replacement_value: str,
) -> tuple[str, bool]:
    value = replacement_value.strip()
    if not value or not trailing_note:
        return text, False

    base_pattern = _field_name_pattern(field_name)
    note_pattern = re.escape(trailing_note)

    replace_after_colon = re.compile(
        rf"(?P<prefix>{base_pattern}\s*[（(]\s*{note_pattern}\s*[）)]\s*[:：])(?P<blank>\s*{SLOT_CLUSTER})"
    )
    updated_text, replaced = _replace_once(text, replace_after_colon, _format_fill(value))
    if replaced:
        return updated_text, True

    insert_after_colon = re.compile(
        rf"(?P<prefix>{base_pattern}\s*[（(]\s*{note_pattern}\s*[）)]\s*[:：]\s*)"
    )
    match = insert_after_colon.search(text)
    if match:
        insert_at = match.end("prefix")
        return text[:insert_at] + _format_fill(value) + text[insert_at:], True

    insert_after_suffix = re.compile(
        rf"(?P<prefix>{base_pattern}\s*[（(]\s*{note_pattern}\s*{SLOT_CLUSTER}\s*[）)]\s*[:：]\s*)"
    )
    match = insert_after_suffix.search(text)
    if match:
        insert_at = match.end("prefix")
        return text[:insert_at] + _format_fill(value) + text[insert_at:], True

    return text, False


def _replace_inline_hint_after(text: str, hint: str, replacement_value: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(?P<blank>{SLOT_CLUSTER}\s*)[（(]\s*{re.escape(hint)}\s*[）)]"
    )
    return _replace_once(text, pattern, _format_fill(replacement_value))


def _replace_bare_parentheses_hint(text: str, hint: str, replacement_value: str) -> tuple[str, bool]:
    value = replacement_value.strip()
    if not value:
        return text, False

    before_hint_pattern = re.compile(
        rf"(?P<blank>{SLOT_CLUSTER}\s*)(?=[（(]\s*{re.escape(hint)}\s*[）)])"
    )
    updated_text, replaced = _replace_once(text, before_hint_pattern, value)
    if replaced:
        return updated_text, True

    pattern = re.compile(
        rf"(?P<prefix>[（(]\s*){re.escape(hint)}\s*(?P<suffix>[）)])"
    )
    match = pattern.search(text)
    if not match:
        return text, False

    return (
        text[: match.start()]
        + match.group("prefix")
        + value
        + match.group("suffix")
        + text[match.end() :],
        True,
    )


def _replace_quoted_project_name(text: str, replacement_value: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"[“\"](?P<blank>{SLOT_CLUSTER})[”\"]\s*项目"
    )
    value = replacement_value.strip()
    if not value:
        return text, False
    match = pattern.search(text)
    if not match:
        return text, False
    start, end = match.span("blank")
    return text[:start] + value + text[end:], True


def _replace_naked_project_title(text: str, replacement_value: str) -> tuple[str, bool]:
    value = replacement_value.strip()
    if not value:
        return text, False
    pattern = re.compile(r"^\s*[Xx＿_]{2,}\s*项目\s*$")
    match = pattern.match(text)
    if not match:
        return text, False
    suffix = "" if value.endswith("项目") else "项目"
    return f"{value}{suffix}", True


def _normalize_date_value(value: str) -> str:
    stripped = value.strip()
    if re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", stripped):
        return re.sub(r"\s+", "", stripped)

    digits = re.findall(r"\d+", stripped)
    if len(digits) >= 3:
        year, month, day = digits[0], digits[1], digits[2]
        return f"{year}年{int(month)}月{int(day)}日"

    try:
        parsed = datetime.fromisoformat(stripped)
        return f"{parsed.year}年{parsed.month}月{parsed.day}日"
    except ValueError:
        return stripped


def _cleanup_date_placeholder_residue(text: str) -> str:
    return re.sub(
        r"(?P<date>\d{4}年\d{1,2}月\d{1,2}日)\s*(?:[Xx＿_·•…—\-\s]{0,40}年\s*[Xx＿_·•…—\-\s]{1,40}月\s*[Xx＿_·•…—\-\s]{1,40}日|XX年XX月XX日)",
        r"\g<date>",
        text,
    )


def _replace_date_placeholder(text: str, field_name: str, replacement_value: str) -> tuple[str, bool]:
    normalized_value = _normalize_date_value(replacement_value)
    if not re.search(r"\d{4}年\d{1,2}月\d{1,2}日", normalized_value):
        return text, False

    pattern = re.compile(
        rf"(?P<prefix>{_field_name_pattern(field_name)}\s*[:：])(?P<blank>\s*(?:[ _{re.escape(SLOT_CHARS)}]{{0,20}}年\s*[ _{re.escape(SLOT_CHARS)}]{{0,10}}月\s*[ _{re.escape(SLOT_CHARS)}]{{0,10}}日|XX年XX月XX日))"
    )
    updated_text, replaced = _replace_once(text, pattern, _format_fill(normalized_value))
    if replaced:
        return _cleanup_date_placeholder_residue(updated_text), True
    return text, False


def _replace_label_only(text: str, field_name: str, replacement_value: str) -> tuple[str, bool]:
    pattern = re.compile(rf"^(?P<prefix>{_field_name_pattern(field_name)}\s*[:：]\s*)$")
    match = pattern.search(text.strip())
    if not match:
        return text, False
    value = replacement_value.strip()
    if not value:
        return text, False
    return f"{match.group('prefix')}{_format_fill(value)}", True


def _replace_sentence_blank(text: str, field_name: str, replacement_value: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(?P<prefix>{_field_name_pattern(field_name)}\s*(?:为|是))(?P<blank>\s*{DATE_SLOT_CLUSTER})"
    )
    return _replace_once(text, pattern, _format_fill(replacement_value))


def _replace_anchor_hint(text: str, hint: str, replacement_value: str, fill_side: str) -> tuple[str, bool]:
    value = replacement_value.strip()
    if not value:
        return text, False

    before_hint_slot_pattern = re.compile(
        rf"(?P<blank>(?:[Xx＿_]{{2,}}|{SLOT_CLUSTER})\s*)(?=[（(]\s*{re.escape(hint)}\s*[）)])"
    )
    updated_text, replaced = _replace_once(text, before_hint_slot_pattern, _format_fill(value))
    if replaced:
        return updated_text, True

    if fill_side == "before_insert":
        leading_slot_pattern = re.compile(
            rf"(?P<blank>^\s+)(?=[（(]\s*{re.escape(hint)}\s*[）)])"
        )
        updated_text, replaced = _replace_once(text, leading_slot_pattern, value)
        if replaced:
            return updated_text, True

        colon_slot_pattern = re.compile(
            rf"(?P<prefix>[:：])(?P<blank>\s+)(?=[（(]\s*{re.escape(hint)}\s*[）)])"
        )
        updated_text, replaced = _replace_once(text, colon_slot_pattern, value)
        if replaced:
            return updated_text, True

        pattern = re.compile(
            rf"(?P<prefix>(?:^\s*|\s*[:：]\s*))(?=[（(]\s*{re.escape(hint)}\s*[）)])"
        )
        match = pattern.search(text)
        if not match:
            return text, False
        insert_at = match.end("prefix")
        return text[:insert_at] + value + text[insert_at:], True

    if fill_side == "inside_colon":
        pattern = re.compile(
            rf"(?P<prefix>[（(]\s*{re.escape(hint)}\s*[:：])(?P<blank>\s*(?:[Xx＿_]{{2,}}|{SLOT_CLUSTER}))(?P<suffix>\s*[）)])"
        )
        updated_text, replaced = _replace_once(text, pattern, value)
        if replaced:
            return updated_text, True

    if fill_side == "after_outer":
        pattern = re.compile(
            rf"(?P<prefix>[（(]\s*{re.escape(hint)}\s*[）)]\s*)(?P<blank>{SLOT_CLUSTER})"
        )
        return _replace_once(text, pattern, _format_fill(value))

    if fill_side == "after_inner":
        pattern = re.compile(
            rf"(?P<prefix>[（(]\s*{re.escape(hint)}\s*)(?P<blank>{SLOT_CLUSTER})(?P<suffix>\s*[）)])"
        )
        return _replace_once(text, pattern, _format_fill(value))

    if fill_side == "before":
        pattern = re.compile(
            rf"(?P<blank>{SLOT_CLUSTER}\s*)(?=[（(]\s*{re.escape(hint)}\s*[）)])"
        )
        return _replace_once(text, pattern, _format_fill(value))

    if fill_side == "after_colon":
        pattern = re.compile(
            rf"(?P<prefix>[（(]\s*{re.escape(hint)}\s*[）)]\s*[:：])(?P<blank>\s*{SLOT_CLUSTER})"
        )
        updated_text, replaced = _replace_once(text, pattern, _format_fill(value))
        if replaced:
            return updated_text, True

        prefix_pattern = re.compile(
            rf"(?P<prefix>[（(]\s*{re.escape(hint)}\s*[）)]\s*[:：]\s*)"
        )
        match = prefix_pattern.search(text)
        if not match:
            return text, False
        insert_at = match.end("prefix")
        return text[:insert_at] + _format_fill(value) + text[insert_at:], True

    return text, False


def _replace_generic_placeholder(text: str, replacement_value: str) -> tuple[str, bool]:
    match = PLACEHOLDER_RE.search(text)
    if not match:
        return text, False
    value = replacement_value.strip()
    if not value:
        return text, False
    start, end = match.span(0)
    return text[:start] + _format_fill(value) + text[end:], True


def _replace_after_label_aggressive(text: str, field_name: str, replacement_value: str) -> tuple[str, bool]:
    value = replacement_value.strip()
    if not value:
        return text, False

    pattern = re.compile(rf"(?P<prefix>{_field_name_pattern(field_name)}\s*[:：]?\s*)(?P<suffix>$)")
    match = pattern.search(text.strip())
    if match:
        prefix = match.group("prefix")
        return f"{prefix}{_format_fill(value)}", True

    label_pattern = re.compile(rf"(?P<prefix>{_field_name_pattern(field_name)}\s*[:：]?\s*)")
    match = label_pattern.search(text)
    if match:
        insert_at = match.end("prefix")
        return text[:insert_at] + _format_fill(value) + text[insert_at:], True

    return text, False


def _normalize_paragraph_spacing(text: str) -> str:
    text = re.sub(r"([:：)])[\s\u00A0]+(?=[A-Za-z0-9\u4e00-\u9fa5“\"(（])", r"\1 ", text)
    text = re.sub(r"([A-Za-z0-9\u4e00-\u9fa5”\"])[\s\u00A0]+(?=[(（])", r"\1 ", text)
    text = re.sub(r"[ \u00A0]{3,}", "  ", text)
    return text


def _apply_standard_replacement(text: str, field: ExtractedField) -> tuple[str, bool]:
    trailing_note = str(field.metadata.get("trailing_note", "")).strip()
    if trailing_note:
        updated_text, replaced = _replace_trailing_note_label(
            text,
            field.field_name,
            trailing_note,
            field.resolved_value,
        )
        if replaced:
            return updated_text, True

    if field.detected_by == "date_colon_blank":
        updated_text, replaced = _replace_date_placeholder(text, field.field_name, field.resolved_value)
        if replaced:
            return updated_text, True
        return _replace_colon_placeholder(text, field.field_name, _normalize_date_value(field.resolved_value))
    if field.detected_by == "date_label_only":
        updated_text, replaced = _replace_date_placeholder(text, field.field_name, field.resolved_value)
        if replaced:
            return updated_text, True
        updated_text, replaced = _replace_colon_placeholder(text, field.field_name, _normalize_date_value(field.resolved_value))
        if replaced:
            return updated_text, True
        return _replace_label_only(text, field.field_name, _normalize_date_value(field.resolved_value))
    if field.detected_by == "sentence_date_blank":
        return _replace_sentence_blank(text, field.field_name, _normalize_date_value(field.resolved_value))
    if field.detected_by == "sentence_blank":
        return _replace_sentence_blank(text, field.field_name, field.resolved_value)
    if field.detected_by in {"label_only", "table_label_only"}:
        updated_text, replaced = _replace_colon_placeholder(text, field.field_name, field.resolved_value)
        if replaced:
            return updated_text, True
        return _replace_label_only(text, field.field_name, field.resolved_value)
    if field.detected_by == "quoted_project_name":
        return _replace_quoted_project_name(text, field.resolved_value)
    if field.detected_by == "naked_project_title":
        return _replace_naked_project_title(text, field.resolved_value)
    if field.detected_by == "inline_hint_after":
        return _replace_inline_hint_after(text, field.field_name, field.resolved_value)
    if field.detected_by == "bare_parentheses_hint":
        return _replace_bare_parentheses_hint(text, field.field_name, field.resolved_value)
    if field.detected_by == "anchor_hint":
        fill_side = str(field.metadata.get("fill_side", ""))
        return _replace_anchor_hint(text, field.field_name, field.resolved_value, fill_side)
    return _replace_colon_placeholder(text, field.field_name, field.resolved_value)


def _apply_aggressive_replacement(text: str, field: ExtractedField) -> tuple[str, bool]:
    replacement_value = field.resolved_value
    if field.detected_by in {"date_colon_blank", "date_label_only", "sentence_date_blank"}:
        replacement_value = _normalize_date_value(replacement_value)
    if field.detected_by == "naked_project_title":
        return _replace_naked_project_title(text, replacement_value)

    trailing_note = str(field.metadata.get("trailing_note", "")).strip()
    if trailing_note:
        updated_text, replaced = _replace_trailing_note_label(
            text,
            field.field_name,
            trailing_note,
            replacement_value,
        )
        if replaced:
            return updated_text, True

    if field.detected_by == "anchor_hint":
        updated_text, replaced = _replace_anchor_hint(
            text,
            field.field_name,
            replacement_value,
            str(field.metadata.get("fill_side", "")),
        )
        if replaced:
            return updated_text, True

    if field.detected_by == "bare_parentheses_hint":
        updated_text, replaced = _replace_bare_parentheses_hint(text, field.field_name, replacement_value)
        if replaced:
            return updated_text, True

    updated_text, replaced = _replace_after_label_aggressive(text, field.field_name, replacement_value)
    if replaced:
        return updated_text, True

    return _replace_generic_placeholder(text, replacement_value)


def _manual_placeholder_span(text: str, field: ExtractedField) -> tuple[int, int] | None:
    field_pattern = _field_name_pattern(field.field_name)
    candidates = [
        re.compile(rf"(?P<prefix>{field_pattern}\s*[:：])(?P<blank>\s*{SLOT_CLUSTER})"),
        re.compile(rf"(?P<blank>{SLOT_CLUSTER}\s*)(?=[（(]\s*{field_pattern}\s*[）)])"),
        re.compile(rf"(?P<prefix>[（(]\s*{field_pattern}\s*[）)]\s*[:：])(?P<blank>\s*{SLOT_CLUSTER})"),
    ]

    for pattern in candidates:
        match = pattern.search(text)
        if match:
            return match.span("blank")

    if field.detected_by == "bare_parentheses_hint":
        hint_pattern = re.compile(rf"(?P<blank>[（(]\s*{field_pattern}\s*[）)])")
        match = hint_pattern.search(text)
        if match:
            return match.span("blank")

    if field.field_name and field.field_name in text:
        match = PLACEHOLDER_RE.search(text)
        if match:
            return match.span(0)

    return None


def _style_manual_placeholder_in_place(text_nodes: list[etree._Element], field: ExtractedField) -> bool:
    current_text = "".join(node.text or "" for node in text_nodes)
    if not current_text:
        return False

    span = _manual_placeholder_span(current_text, field)
    if span is None:
        return False

    start, end = span
    if start == end:
        return False
    return _write_span_with_styled_replacement(text_nodes, start, end, current_text[start:end], "MANUAL")


def _is_internal_label_spacing(text: str, start: int, end: int) -> bool:
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    if not before or not after:
        return False
    prev_char = before[-1]
    next_char = after[0]
    if not ("\u4e00" <= prev_char <= "\u9fff" and "\u4e00" <= next_char <= "\u9fff"):
        return False
    left_label = before[-6:]
    right_label = after[:6]
    compact = re.sub(r"\s+", "", left_label + right_label)
    known_labels = (
        "日期",
        "供应商名称",
        "法定代表人",
        "单位负责人",
        "授权代表",
        "采购项目编号",
        "通讯地址",
        "联系电话",
    )
    return any(label in compact for label in known_labels)


def _style_remaining_visible_placeholders_in_place(text_nodes: list[etree._Element]) -> bool:
    current_text = "".join(node.text or "" for node in text_nodes)
    if not current_text:
        return False
    if NOTE_PREFIX_RE.match(current_text):
        return False

    spans = [match.span(0) for match in VISIBLE_PLACEHOLDER_RE.finditer(current_text)]
    if not spans:
        return False

    styled = False
    for start, end in reversed(spans):
        if start == end:
            continue
        if current_text[start:end].strip() == "" and _is_internal_label_spacing(current_text, start, end):
            continue
        if _write_span_with_styled_replacement(text_nodes, start, end, current_text[start:end], "MANUAL"):
            styled = True
    return styled


def _fill_paragraph_in_place(paragraph: etree._Element, fields: list[ExtractedField], aggressive: bool = False) -> set[str]:
    text_nodes = _iter_text_nodes(paragraph)
    if not text_nodes:
        return set()

    filled_ids: set[str] = set()

    for field in fields:
        if not field.resolved_value:
            _style_manual_placeholder_in_place(text_nodes, field)
            continue

        current_text = "".join(node.text or "" for node in text_nodes)
        if not current_text:
            continue

        updated_candidate, replaced = _apply_standard_replacement(current_text, field)
        if not replaced and aggressive:
            updated_candidate, replaced = _apply_aggressive_replacement(current_text, field)

        if replaced and updated_candidate != current_text:
            start, end, replacement = _changed_span(current_text, updated_candidate)
            if not _write_span_with_styled_replacement(
                text_nodes,
                start,
                end,
                replacement,
                _source_style_for_field(field),
            ):
                _style_manual_placeholder_in_place(text_nodes, field)
                continue
            filled_ids.add(field.field_id)
            text_nodes = _iter_text_nodes(paragraph)
        else:
            _style_manual_placeholder_in_place(text_nodes, field)

    return filled_ids


def _fill_blank_table_cell(row: etree._Element, field: ExtractedField, aggressive: bool = False) -> bool:
    cells = row.xpath("./w:tc", namespaces=NS)
    if len(cells) < 2:
        return False

    target_cell: etree._Element | None = None
    target_index = field.metadata.get("target_cell_index")
    if isinstance(target_index, int) and 0 <= target_index < len(cells):
        target_cell = cells[target_index]
    else:
        for cell in cells[1:]:
            cell_text = _read_text(cell)
            if PLACEHOLDER_RE.search(cell_text):
                target_cell = cell
                break
            if target_cell is None and not cell_text.strip():
                target_cell = cell

    if target_cell is None:
        return False

    text_nodes = _ensure_cell_text_nodes(target_cell)
    if not field.resolved_value:
        _style_manual_placeholder_in_place(text_nodes, field)
        return False

    original_chunks = [node.text or "" for node in text_nodes]
    original_text = "".join(original_chunks)
    value = field.resolved_value.strip()
    if not value:
        return False

    match = PLACEHOLDER_RE.search(original_text)
    if not match:
        if not original_text.strip():
            _set_text_node(text_nodes[0], value)
            run = _text_node_run(text_nodes[0])
            if run is not None:
                _style_run_for_source(run, _source_style_for_field(field))
            for node in text_nodes[1:]:
                _set_text_node(node, "")
            return True
        if aggressive:
            return _write_span_with_styled_replacement(
                text_nodes,
                len(original_text),
                len(original_text),
                _format_fill(value),
                _source_style_for_field(field),
            )
        _style_manual_placeholder_in_place(text_nodes, field)
        return False

    start, end = match.span(0)
    if _write_span_with_styled_replacement(text_nodes, start, end, value, _source_style_for_field(field)):
        return True
    _style_manual_placeholder_in_place(text_nodes, field)
    return False


def create_filled_docx(
    input_docx: str,
    output_docx: str,
    fields: list[ExtractedField],
    append_summary: bool = False,
    writeback_mode: str = "safe",
    redline_scope_block_ids: set[str] | None = None,
) -> set[str]:
    del append_summary

    source_path = Path(input_docx)
    if source_path.suffix.lower() != ".docx":
        raise ValueError("Current MVP only supports .docx output.")

    aggressive = writeback_mode == "aggressive"
    redline_scope_block_ids = redline_scope_block_ids or set()

    paragraph_fields: dict[str, list[ExtractedField]] = defaultdict(list)
    table_row_fields: dict[str, list[ExtractedField]] = defaultdict(list)
    table_paragraph_fields: dict[str, list[ExtractedField]] = defaultdict(list)
    for field in fields:
        if field.block_type == "paragraph":
            paragraph_fields[field.block_id].append(field)
        elif field.block_type == "table_row":
            table_row_fields[field.block_id].append(field)
        elif field.block_type == "table_paragraph":
            table_paragraph_fields[field.block_id].append(field)

    skipped_field_ids: set[str] = set()

    with ZipFile(source_path) as reader:
        document_xml = reader.read("word/document.xml")
        root = etree.fromstring(document_xml)
        body = root.find("w:body", namespaces=NS)
        if body is None:
            raise ValueError("Could not find the Word document body.")

        paragraph_index = 0
        table_row_index = 0
        table_paragraph_index = 0
        for child in body:
            local_name = etree.QName(child.tag).localname
            if local_name == "p":
                if not _read_text(child).strip():
                    continue
                block_id = f"p-{paragraph_index}"
                paragraph_index += 1
                field_group = paragraph_fields.get(block_id, [])
                if field_group:
                    filled_ids = _fill_paragraph_in_place(child, field_group, aggressive=aggressive)
                    for field in field_group:
                        if field.field_id not in filled_ids:
                            skipped_field_ids.add(field.field_id)
                if block_id in redline_scope_block_ids:
                    _style_remaining_visible_placeholders_in_place(_iter_text_nodes(child))

            elif local_name == "tbl":
                for row in child.xpath("./w:tr", namespaces=NS):
                    cells = row.xpath("./w:tc", namespaces=NS)
                    if not any(_read_text(cell).strip() for cell in cells):
                        continue
                    block_id = f"t-{table_row_index}"
                    table_row_index += 1
                    row_fields = table_row_fields.get(block_id, [])
                    for field in row_fields:
                        if not _fill_blank_table_cell(row, field, aggressive=aggressive):
                            skipped_field_ids.add(field.field_id)
                    if block_id in redline_scope_block_ids:
                        for cell in cells:
                            _style_remaining_visible_placeholders_in_place(_iter_text_nodes(cell))

                    if len(cells) == 1:
                        only_cell = cells[0]
                        for paragraph_in_cell in only_cell.xpath("./w:p", namespaces=NS):
                            if not _read_text(paragraph_in_cell).strip():
                                continue
                            inner_block_id = f"tp-{table_paragraph_index}"
                            table_paragraph_index += 1
                            field_group = table_paragraph_fields.get(inner_block_id, [])
                            if field_group:
                                filled_ids = _fill_paragraph_in_place(paragraph_in_cell, field_group, aggressive=aggressive)
                                for field in field_group:
                                    if field.field_id not in filled_ids:
                                        skipped_field_ids.add(field.field_id)
                            if inner_block_id in redline_scope_block_ids:
                                _style_remaining_visible_placeholders_in_place(_iter_text_nodes(paragraph_in_cell))

        updated_document_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
        with ZipFile(output_docx, "w", ZIP_DEFLATED) as writer:
            for item in reader.infolist():
                content = updated_document_xml if item.filename == "word/document.xml" else reader.read(item.filename)
                writer.writestr(item, content)

    return skipped_field_ids
