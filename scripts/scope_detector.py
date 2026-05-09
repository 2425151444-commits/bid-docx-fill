from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re

from parse_docx_v2 import ParsedDocument
from schemas import DocumentBlock


LOOKAHEAD_BLOCKS = 60
MIN_SUCCESS_SCORE = 55
LOW_CONFIDENCE_SCORE = 45
MIN_BODY_BLOCKS_BEFORE_END = 6


STRONG_SCOPE_TITLES = (
    "响应文件格式",
    "响应文件组成格式",
    "响应文件编制格式",
    "响应文件模板",
    "响应文件格式模板",
    "投标文件格式",
    "申请文件格式",
)

RESPONSE_PART_TITLES = (
    "资格性响应文件",
    "其他响应文件",
    "商务响应文件",
    "技术响应文件",
    "报价文件",
    "响应文件封面",
    "投标响应文件",
    "磋商响应文件",
)

FORM_ANCHOR_TITLES = (
    "响应函",
    "磋商函",
    "报价一览表",
    "报价表",
    "供应商基本情况表",
    "法定代表人授权书",
    "授权委托书",
    "承诺函",
    "商务应答表",
    "技术应答表",
    "偏离表",
)

FILL_ANCHORS = (
    "项目名称",
    "项目编号",
    "采购项目编号",
    "供应商名称",
    "供应商全称",
    "法定代表人",
    "授权代表",
    "联系人",
    "联系电话",
    "通讯地址",
    "报价",
    "总价",
    "盖章",
    "签字",
    "签章",
    "年月日",
)

NEGATIVE_SCOPE_TITLES = (
    "供应商须知",
    "响应文件的编制",
    "响应文件编制要求",
    "响应文件的递交",
    "响应文件的密封",
    "响应文件开启",
    "采购邀请",
    "磋商邀请",
    "谈判邀请",
    "资格审查",
    "评审办法",
    "评分标准",
    "综合评分",
    "采购需求",
    "服务要求",
    "合同主要条款",
    "合同条款",
    "政府采购合同",
    "采购合同",
    "合同格式",
    "合同范本",
)

END_SCOPE_TITLES = (
    "供应商的资格",
    "供应商应当提供的资格",
    "资格、资质性",
    "采购需求",
    "评审办法",
    "评分标准",
    "合同主要条款",
    "合同条款",
    "主要合同条款",
    "合同格式",
    "合同协议书",
    "政府采购合同",
    "采购合同",
    "合同书",
    "合同范本",
)

PLACEHOLDER_RE = re.compile(
    r"_{2,}|＿{2,}|[Xx]{2,}|[ \u00A0]{4,}|"
    r"年\s*月\s*日|XX年XX月XX日|（\s*）|\(\s*\)"
)


@dataclass
class ScopeDetection:
    status: str
    message: str
    start_index: int | None = None
    end_index: int | None = None
    start_block_id: str = ""
    end_block_id: str = ""
    start_text: str = ""
    end_text: str = ""
    score: int = 0
    confidence: float = 0.0
    scope_block_count: int = 0
    reasons: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)

    @property
    def is_fillable(self) -> bool:
        return (
            self.status == "success"
            and self.start_index is not None
            and self.end_index is not None
            and self.end_index > self.start_index
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["is_fillable"] = self.is_fillable
        return payload


def compact_scope_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def is_toc_entry(text: str) -> bool:
    compact = compact_scope_text(text)
    return bool(re.match(r"^第[一二三四五六七八九十0-9]+章.+\d{1,4}$", compact))


def is_real_chapter_heading(block: DocumentBlock) -> bool:
    text = compact_scope_text(block.text)
    if not text or is_toc_entry(text):
        return False
    return bool(re.match(r"^第[一二三四五六七八九十0-9]+章", text))


def looks_like_heading(block: DocumentBlock) -> bool:
    text = compact_scope_text(block.text)
    if not text or is_toc_entry(text):
        return False
    if len(text) <= 48:
        return True
    return bool(
        is_real_chapter_heading(block)
        or re.match(r"^第[一二三四五六七八九十0-9]+节", text)
        or re.match(r"^文件[一二三四五六七八九十0-9]+[、.．]", text)
    )


def looks_like_candidate_heading(block: DocumentBlock) -> bool:
    text = compact_scope_text(block.text)
    if not text or is_toc_entry(text):
        return False
    if is_real_chapter_heading(block):
        return True
    if re.match(r"^文件[一二三四五六七八九十0-9]+[、.．]", text):
        return True
    if re.match(r"^格式\d+[-－—]?\d*", text):
        return True
    if len(text) <= 48 and not any(mark in text for mark in "。；;"):
        return True
    return False


def looks_like_boundary_heading(block: DocumentBlock) -> bool:
    text = compact_scope_text(block.text)
    if not text or is_toc_entry(text):
        return False
    if is_real_chapter_heading(block):
        return True
    if re.match(r"^文件[一二三四五六七八九十0-9]+[、.．]", text):
        return True
    if re.match(r"^格式\d+[-－—]?\d*", text):
        return True
    if text.startswith(("附件", "附表")) and len(text) <= 40:
        return True
    return len(text) <= 30 and not any(mark in text for mark in "。；;，,")


def is_response_template_heading(text: str) -> bool:
    compact = compact_scope_text(text)
    if not compact:
        return False
    if any(title in compact for title in STRONG_SCOPE_TITLES):
        return True
    if any(title in compact for title in RESPONSE_PART_TITLES):
        return True
    if re.match(r"^文件[一二三四五六七八九十0-9]+[、.．].*响应文件", compact):
        return True
    return "响应文件" in compact and any(token in compact for token in ("格式", "模板", "封面", "组成"))


def _is_form_anchor_heading(text: str) -> bool:
    compact = compact_scope_text(text)
    return any(title in compact for title in FORM_ANCHOR_TITLES)


def _count_fill_anchors(text: str) -> int:
    compact = compact_scope_text(text)
    return sum(1 for anchor in FILL_ANCHORS if anchor in compact)


def _count_blank_table_cells(blocks: list[DocumentBlock]) -> int:
    total = 0
    for block in blocks:
        if block.block_type != "table_row":
            continue
        cells = block.metadata.get("cells") or []
        total += sum(1 for cell in cells if not str(cell).strip())
    return total


def _score_scope_candidate(parsed_document: ParsedDocument, index: int) -> dict:
    block = parsed_document.blocks[index]
    text = compact_scope_text(block.text)
    window_blocks = parsed_document.blocks[index : index + LOOKAHEAD_BLOCKS]
    window_text = "\n".join(block.text for block in window_blocks)

    score = 0
    reasons: list[str] = []
    negative_reasons: list[str] = []
    title_signal = False

    if not text or is_toc_entry(text):
        return _candidate_payload(block, index, score, reasons, negative_reasons, window_blocks)

    heading_like = looks_like_candidate_heading(block)
    if heading_like and any(title in text for title in STRONG_SCOPE_TITLES):
        score += 55
        reasons.append("命中强响应模板标题")
        title_signal = True
    elif heading_like and "响应文件" in text and any(token in text for token in ("格式", "模板", "封面", "组成")):
        score += 48
        reasons.append("命中响应文件模板类标题")
        title_signal = True

    if heading_like and any(title in text for title in RESPONSE_PART_TITLES):
        score += 42
        reasons.append("命中资格性/其他/商务/技术响应文件标题")
        title_signal = True

    if heading_like and _is_form_anchor_heading(text):
        score += 30
        reasons.append("命中常见响应表单标题")
        title_signal = True

    fill_anchor_count = _count_fill_anchors(window_text)
    if fill_anchor_count:
        score += min(fill_anchor_count * 5, 35)
        reasons.append(f"后续窗口出现 {fill_anchor_count} 个填写语义锚点")

    placeholder_count = len(PLACEHOLDER_RE.findall(window_text))
    if placeholder_count:
        score += min(placeholder_count * 3, 30)
        reasons.append(f"后续窗口出现 {placeholder_count} 个占位符/空白")

    blank_cell_count = _count_blank_table_cells(window_blocks)
    if blank_cell_count:
        score += min(blank_cell_count * 2, 18)
        reasons.append(f"后续窗口出现 {blank_cell_count} 个空表格单元格")

    form_title_count = sum(1 for candidate in FORM_ANCHOR_TITLES if candidate in compact_scope_text(window_text))
    if form_title_count:
        score += min(form_title_count * 4, 20)
        reasons.append(f"后续窗口出现 {form_title_count} 个表单标题")

    document_length = max(len(parsed_document.blocks), 1)
    if index >= int(document_length * 0.25):
        score += 8
        reasons.append("候选位置位于文档中后段")
    elif index >= int(document_length * 0.15):
        score += 4
        reasons.append("候选位置已离开文档开头说明区")

    if any(title in text for title in NEGATIVE_SCOPE_TITLES) and not is_response_template_heading(text):
        score -= 65
        negative_reasons.append("命中供应商须知/评审/需求/合同等负向章节")

    if "目录" in text:
        score -= 30
        negative_reasons.append("疑似目录文本")

    if not heading_like and score < 75:
        score -= 20
        negative_reasons.append("当前块不像章节或表单标题")

    return _candidate_payload(block, index, score, reasons, negative_reasons, window_blocks, title_signal)


def _candidate_payload(
    block: DocumentBlock,
    index: int,
    score: int,
    reasons: list[str],
    negative_reasons: list[str],
    window_blocks: list[DocumentBlock],
    title_signal: bool = False,
) -> dict:
    return {
        "index": index,
        "block_id": block.block_id,
        "block_type": block.block_type,
        "text": block.text.strip(),
        "score": score,
        "confidence": round(max(0.0, min(score / 100, 0.99)), 2),
        "reasons": reasons,
        "negative_reasons": negative_reasons,
        "features": {
            "title_signal": title_signal,
        },
        "window_preview": [candidate.text.strip() for candidate in window_blocks[:5] if candidate.text.strip()],
    }


def _scope_end_index(parsed_document: ParsedDocument, start_index: int) -> int:
    blocks = parsed_document.blocks
    for index in range(start_index + 1, len(blocks)):
        if index - start_index <= MIN_BODY_BLOCKS_BEFORE_END:
            continue

        block = blocks[index]
        text = compact_scope_text(block.text)
        if not text or is_toc_entry(text) or not looks_like_boundary_heading(block):
            continue
        if is_response_template_heading(text) or _is_form_anchor_heading(text):
            continue

        if any(title in text for title in END_SCOPE_TITLES):
            if (
                any(title in text for title in ("供应商的资格", "供应商应当提供的资格", "资格、资质性"))
                and not is_real_chapter_heading(block)
            ):
                continue
            return index

        if is_real_chapter_heading(block) and any(
            token in text for token in ("资格", "需求", "评审", "评分", "合同")
        ):
            return index

    return len(blocks)


def _select_scope_start_candidate(candidates: list[dict]) -> dict:
    best = candidates[0]
    chapter_candidates = [
        candidate
        for candidate in candidates
        if int(candidate["score"]) >= MIN_SUCCESS_SCORE
        and re.match(r"^第[一二三四五六七八九十0-9]+章.*响应文件.*格式", compact_scope_text(candidate["text"]))
    ]
    if chapter_candidates:
        return min(chapter_candidates, key=lambda item: int(item["index"]))

    part_candidates = [
        candidate
        for candidate in candidates
        if int(candidate["score"]) >= MIN_SUCCESS_SCORE
        and _looks_like_response_part_start(candidate["text"])
    ]
    if part_candidates:
        return min(part_candidates, key=lambda item: int(item["index"]))

    strong_floor = max(MIN_SUCCESS_SCORE, int(best["score"]) - 25)
    strong_title_candidates = [
        candidate
        for candidate in candidates
        if int(candidate["score"]) >= strong_floor
        and candidate.get("features", {}).get("title_signal")
    ]
    if strong_title_candidates:
        return min(strong_title_candidates, key=lambda item: int(item["index"]))

    success_candidates = [
        candidate
        for candidate in candidates
        if int(candidate["score"]) >= MIN_SUCCESS_SCORE
    ]
    if success_candidates:
        return min(success_candidates, key=lambda item: int(item["index"]))

    return best


def _looks_like_response_part_start(text: str) -> bool:
    compact = compact_scope_text(text)
    if re.match(r"^\d+(?:\.\d+)+", compact):
        return False
    if re.match(r"^文件[一二三四五六七八九十0-9]+[、.．].*响应文件", compact):
        return True
    if re.match(r"^第[一二三四五六七八九十0-9]+部分.*响应文件", compact):
        return True
    if not any(title in compact for title in RESPONSE_PART_TITLES):
        return False
    return any(token in compact for token in ("格式", "封面", "模板"))


def detect_fill_scope(parsed_document: ParsedDocument) -> ScopeDetection:
    if not parsed_document.blocks:
        return ScopeDetection(
            status="no_scope_found",
            message="文档没有可分析的正文 block，无法识别响应模板区。",
        )

    candidates = [
        _score_scope_candidate(parsed_document, index)
        for index in range(len(parsed_document.blocks))
    ]
    candidates = [
        candidate
        for candidate in candidates
        if candidate["score"] >= 20
        or candidate["reasons"]
        or candidate["negative_reasons"]
    ]
    candidates.sort(key=lambda item: item["score"], reverse=True)

    if not candidates:
        return ScopeDetection(
            status="no_scope_found",
            message="未找到响应文件、资格性响应文件、其他响应文件或常见响应表单等模板入口。",
        )

    best = _select_scope_start_candidate(candidates)
    if best["score"] < LOW_CONFIDENCE_SCORE:
        return ScopeDetection(
            status="no_scope_found",
            message="未找到可信的响应模板区；最高候选分数过低，建议人工指定起始位置。",
            score=best["score"],
            confidence=best["confidence"],
            reasons=best["reasons"] + best["negative_reasons"],
            candidates=candidates[:10],
        )

    start_index = int(best["index"])
    end_index = _scope_end_index(parsed_document, start_index)
    scope_block_count = max(0, end_index - start_index - 1)
    end_block = parsed_document.blocks[end_index] if end_index < len(parsed_document.blocks) else None

    if best["score"] < MIN_SUCCESS_SCORE:
        return ScopeDetection(
            status="low_confidence_scope",
            message="疑似找到响应模板区，但置信度较低；为避免误填，未进入自动填充范围。",
            start_index=start_index,
            end_index=end_index,
            start_block_id=best["block_id"],
            end_block_id=end_block.block_id if end_block else "",
            start_text=best["text"],
            end_text=end_block.text.strip() if end_block else "",
            score=best["score"],
            confidence=best["confidence"],
            scope_block_count=scope_block_count,
            reasons=best["reasons"] + best["negative_reasons"],
            candidates=candidates[:10],
        )

    return ScopeDetection(
        status="success",
        message="已通过标题信号、填写锚点、占位符密度和负向边界识别出响应模板区。",
        start_index=start_index,
        end_index=end_index,
        start_block_id=best["block_id"],
        end_block_id=end_block.block_id if end_block else "",
        start_text=best["text"],
        end_text=end_block.text.strip() if end_block else "",
        score=best["score"],
        confidence=best["confidence"],
        scope_block_count=scope_block_count,
        reasons=best["reasons"] + best["negative_reasons"],
        candidates=candidates[:10],
    )


def iter_fill_scope_blocks(
    parsed_document: ParsedDocument,
    scope_detection: ScopeDetection | None = None,
):
    detection = scope_detection or detect_fill_scope(parsed_document)
    if not detection.is_fillable:
        return

    start_index = detection.start_index or 0
    end_index = detection.end_index if detection.end_index is not None else len(parsed_document.blocks)
    for block in parsed_document.blocks[start_index + 1 : end_index]:
        yield block


def fill_scope_block_ids(
    parsed_document: ParsedDocument,
    scope_detection: ScopeDetection | None = None,
) -> set[str]:
    return {block.block_id for block in iter_fill_scope_blocks(parsed_document, scope_detection)}
