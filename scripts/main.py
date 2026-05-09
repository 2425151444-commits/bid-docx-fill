from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from lxml import etree

from classify_source_v3 import classify_fields
from extract_fields_v4 import extract_fields, fill_scope_block_ids
from fill_docx_inplace_v3 import create_filled_docx
from kb_loader import load_knowledge_base
from parse_docx_v2 import parse_docx
from resolve_values_v3 import resolve_fields
from scope_detector import ScopeDetection, detect_fill_scope


FINAL_OUTPUT_DOCX_NAME = "final_output.docx"
RESULT_JSON_NAME = "result.json"
MANUAL_REVIEW_JSON_NAME = "manual_review.json"
FIELD_MAPPING_TABLE_JSON_NAME = "field_mapping_table.json"
FIELD_MAPPING_TABLE_CSV_NAME = "field_mapping_table.csv"
FIELD_MAPPING_TABLE_MD_NAME = "field_mapping_table.md"
FINAL_RESPONSE_MD_NAME = "final_response.md"
SCOPE_DETECTION_REPORT_JSON_NAME = "scope_detection_report.json"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def _default_kb_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "data" / "knowledge-base.xlsx"


def _resolve_desktop_dir() -> Path:
    candidates: list[Path] = []
    desktop_from_env = os.getenv("BID_DOC_FILL_DESKTOP")
    if desktop_from_env:
        candidates.append(Path(desktop_from_env).expanduser())

    home = Path.home()
    candidates.extend(
        [
            Path("/mnt/user-data/Desktop"),
            Path("/mnt/user-data/desktop"),
            Path("/mnt/user-data/workspace"),
            home / "Desktop",
            home / "桌面",
        ]
    )

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    fallback = candidates[0] if candidates else home / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _resolve_wordconv_path() -> str | None:
    candidates = [
        Path(r"C:\Program Files\Microsoft Office\root\Office16\Wordconv.exe"),
        Path(r"C:\Program Files (x86)\Microsoft Office\root\Office16\Wordconv.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("wordconv.exe")


def _resolve_soffice_path() -> str | None:
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _detect_word_file_format(input_path: Path) -> str:
    try:
        with input_path.open("rb") as fh:
            header = fh.read(8)
    except OSError as exc:
        raise RuntimeError(f"Unable to read input file header: {input_path}") from exc

    if header.startswith(OLE_MAGIC):
        return "doc"
    if zipfile.is_zipfile(input_path):
        return "docx"
    return "unknown"


def _convert_legacy_doc_to_docx(input_path: Path, output_dir: Path) -> Path:
    converted_docx = output_dir / "legacy_input.converted.docx"
    if os.name == "nt":
        wordconv = _resolve_wordconv_path()
        if not wordconv:
            raise RuntimeError("Unable to find Wordconv.exe. Please provide a valid .docx input.")
        result = subprocess.run(
            [wordconv, "-oice", "-nme", str(input_path.resolve()), str(converted_docx.resolve())],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not converted_docx.exists():
            raise RuntimeError(
                "Failed to convert legacy Word document via Wordconv.exe. "
                f"exit_code={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        return converted_docx

    soffice = _resolve_soffice_path()
    if not soffice:
        raise RuntimeError(
            "Input is a legacy Word document, but no converter is available. "
            "Install LibreOffice (soffice) or provide a valid .docx file."
        )

    result = subprocess.run(
        [soffice, "--headless", "--convert-to", "docx", "--outdir", str(output_dir.resolve()), str(input_path.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not converted_docx.exists():
        raise RuntimeError(
            "Failed to convert legacy Word document via LibreOffice. "
            f"exit_code={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    return converted_docx


def _ensure_docx_input(input_path: Path, output_dir: Path) -> Path:
    detected_format = _detect_word_file_format(input_path)
    suffix = input_path.suffix.lower()

    if detected_format == "docx":
        return input_path

    if detected_format == "doc":
        source_doc_path = input_path
        if suffix != ".doc":
            source_doc_path = output_dir / "legacy_input.source.doc"
            shutil.copy2(input_path, source_doc_path)
        return _convert_legacy_doc_to_docx(source_doc_path, output_dir)

    raise RuntimeError("Input is neither a valid OOXML .docx nor a detectable legacy .doc file.")


def _group_fields_by_section(fields: list) -> list[dict]:
    sections: dict[str, list[dict]] = {}
    for field in fields:
        sections.setdefault(field.section_name, []).append(
            {
                "field_id": field.field_id,
                "field_name": field.field_name,
                "source_type": field.source_type,
                "value": field.resolved_value,
                "confidence": field.confidence,
                "needs_manual_review": field.needs_manual_review,
                "detected_by": field.detected_by,
                "block_id": field.block_id,
                "notes": field.notes,
            }
        )
    return [{"section_name": section_name, "fields": items} for section_name, items in sections.items()]


def _sync_artifacts_to_desktop(artifacts: dict[str, Path], desktop_dir: Path) -> dict[str, str]:
    desktop_dir.mkdir(parents=True, exist_ok=True)
    synced: dict[str, str] = {}
    for key, source_path in artifacts.items():
        target_path = desktop_dir / source_path.name
        if source_path.resolve() != target_path.resolve():
            shutil.copy2(source_path, target_path)
        synced[key] = str(target_path)
    return synced


def _cleanup_known_outputs(output_dir: Path) -> None:
    removable_names = {
        FINAL_OUTPUT_DOCX_NAME,
        RESULT_JSON_NAME,
        MANUAL_REVIEW_JSON_NAME,
        FIELD_MAPPING_TABLE_JSON_NAME,
        FIELD_MAPPING_TABLE_CSV_NAME,
        FIELD_MAPPING_TABLE_MD_NAME,
        FINAL_RESPONSE_MD_NAME,
        SCOPE_DETECTION_REPORT_JSON_NAME,
    }
    for child in output_dir.iterdir():
        if not child.is_file():
            continue
        if child.name in removable_names:
            try:
                child.unlink(missing_ok=True)
            except PermissionError:
                pass
            continue
        if child.suffix.lower() == ".docx" and (
            child.name.endswith(".filled.docx")
            or child.name.endswith(".converted.docx")
            or child.name == "tender_filled_final.docx"
        ):
            try:
                child.unlink(missing_ok=True)
            except PermissionError:
                pass


def _docx_structure_signature(docx_path: Path) -> dict:
    with zipfile.ZipFile(docx_path) as archive:
        entries = set(archive.namelist())
        document_xml = archive.read("word/document.xml")

    root = etree.fromstring(document_xml)
    return {
        "entries": entries,
        "paragraphs": len(root.xpath("//w:p", namespaces=NS)),
        "tables": len(root.xpath("//w:tbl", namespaces=NS)),
        "rows": len(root.xpath("//w:tr", namespaces=NS)),
        "cells": len(root.xpath("//w:tc", namespaces=NS)),
        "headers": len([entry for entry in entries if entry.startswith("word/header")]),
        "footers": len([entry for entry in entries if entry.startswith("word/footer")]),
    }


def _validate_format_preserved(input_docx: Path, output_docx: Path) -> None:
    source = _docx_structure_signature(input_docx)
    output = _docx_structure_signature(output_docx)
    errors: list[str] = []

    for key in ("paragraphs", "tables", "rows", "cells", "headers", "footers"):
        if source[key] != output[key]:
            errors.append(f"{key}: source={source[key]}, output={output[key]}")

    missing_entries = sorted(source["entries"] - output["entries"])
    if missing_entries:
        errors.append("missing package entries: " + ", ".join(missing_entries[:10]))

    if errors:
        raise RuntimeError(
            "Format-preserving write-back validation failed. "
            "The output appears to have been regenerated instead of edited in place. "
            + "; ".join(errors)
        )


def _result_status(fields: list, scope_detection: ScopeDetection) -> str:
    if scope_detection.status != "success":
        return scope_detection.status
    if not fields:
        return "no_fields_found"
    return "success"


def build_result_payload(input_doc: str, fields: list, scope_detection: ScopeDetection) -> dict:
    return {
        "document_name": Path(input_doc).name,
        "status": _result_status(fields, scope_detection),
        "scope_detection": scope_detection.to_dict(),
        "field_count": len(fields),
        "manual_review_count": sum(1 for field in fields if field.needs_manual_review),
        "sections": _group_fields_by_section(fields),
    }


def _block_sort_key(block_id: str) -> tuple[int, int]:
    if block_id.startswith("p-"):
        return (0, int(block_id.split("-", 1)[1]))
    if block_id.startswith("t-"):
        return (1, int(block_id.split("-", 1)[1]))
    if block_id.startswith("tp-"):
        return (2, int(block_id.split("-", 1)[1]))
    return (9, 0)


def _field_status(field) -> str:
    if field.resolved_value.strip() and not field.needs_manual_review:
        return "filled"
    if field.resolved_value.strip() and field.needs_manual_review:
        return "need_review"
    return "not_found"


def _join_notes(notes: list[str]) -> str:
    return "；".join(note.strip() for note in notes if note.strip())


def _join_items(items: object) -> str:
    if not isinstance(items, list):
        return ""
    return " > ".join(str(item).strip() for item in items if str(item).strip())


def build_field_mapping_rows(fields: list, skipped_field_ids: set[str]) -> list[dict]:
    ordered = sorted(fields, key=lambda field: _block_sort_key(field.block_id))
    rows: list[dict] = []
    for field in ordered:
        writeback_status = "not_applicable"
        if field.resolved_value.strip():
            writeback_status = "skipped" if field.field_id in skipped_field_ids else "written"

        rows.append(
            {
                "field_id": field.field_id,
                "section_name": field.section_name,
                "block_id": field.block_id,
                "block_type": field.block_type,
                "field_name": field.field_name,
                "normalized_field_name": field.normalized_name,
                "detected_by": field.detected_by,
                "field_type": field.field_type,
                "planned_source_type": field.source_type or "",
                "source_priority": _join_items(field.metadata.get("source_priority")),
                "tried_sources": _join_items(field.metadata.get("tried_sources")),
                "value_source": field.metadata.get("value_source", field.source_type or ""),
                "candidate_value": field.resolved_value,
                "evidence_field_name": field.metadata.get("evidence_field_name", ""),
                "evidence_text": field.metadata.get("evidence_text", ""),
                "evidence_block_id": field.metadata.get("evidence_block_id", ""),
                "confidence": f"{field.confidence:.2f}",
                "status": _field_status(field),
                "writeback_status": writeback_status,
                "needs_manual_review": "yes" if field.needs_manual_review else "no",
                "review_reason": field.metadata.get("review_reason", ""),
                "notes": _join_notes(field.notes),
            }
        )
    return rows


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _escape_md_cell(value: object) -> str:
    text = str(value or "")
    text = text.replace("\n", " ").replace("|", "\\|")
    return text


def _write_mapping_markdown(path: Path, rows: list[dict]) -> None:
    lines = ["# Field Mapping Table", ""]
    if not rows:
        lines.extend(["无数据。", ""])
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    headers = list(rows[0].keys())
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_escape_md_cell(row.get(header, "")) for header in headers) + " |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _display_value(value: object, limit: int = 80) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _build_filled_content_rows(mapping_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for row in mapping_rows:
        if row.get("writeback_status") != "written":
            continue
        value = str(row.get("candidate_value", "")).strip()
        if not value:
            continue
        rows.append(
            {
                "section_name": row.get("section_name", ""),
                "field_name": row.get("field_name", ""),
                "value": value,
                "confidence": row.get("confidence", ""),
                "source": row.get("value_source", ""),
                "block_id": row.get("block_id", ""),
            }
        )
    return rows


def _build_final_response_markdown(
    filled_docx_path: Path,
    output_dir: Path,
    fields: list,
    mapping_rows: list[dict],
    skipped_field_ids: set[str],
    scope_detection: ScopeDetection,
) -> str:
    filled_rows = _build_filled_content_rows(mapping_rows)
    manual_review_count = sum(1 for field in fields if field.needs_manual_review)
    skipped_writeback_count = sum(1 for row in mapping_rows if row.get("writeback_status") == "skipped")
    result_status = _result_status(fields, scope_detection)

    lines = [
        "# bid-doc-fill 输出结果",
        "",
        "已完成文档自动填补。" if result_status == "success" else "已完成文档分析，但未进入自动填补。",
        "",
        f"- 最终文件：{filled_docx_path}",
        f"- 输出目录：{output_dir}",
        f"- 运行状态：{result_status}",
        f"- 响应模板区：{scope_detection.message}",
        f"- 范围起点：{scope_detection.start_text or '未识别'}",
        f"- 范围置信度：{scope_detection.confidence}",
        f"- 识别字段数：{len(fields)}",
        f"- 已写回字段数：{len(filled_rows)}",
        f"- 需人工复核字段数：{manual_review_count}",
        f"- 因格式保护跳过写回字段数：{skipped_writeback_count}",
        "",
        "## 已写回内容",
        "",
    ]

    if filled_rows:
        lines.append("| 字段 | 填入内容 | 来源 | 置信度 | 位置 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in filled_rows[:80]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_md_cell(row.get("field_name", "")),
                        _escape_md_cell(_display_value(row.get("value", ""))),
                        _escape_md_cell(row.get("source", "")),
                        _escape_md_cell(row.get("confidence", "")),
                        _escape_md_cell(row.get("block_id", "")),
                    ]
                )
                + " |"
            )
        if len(filled_rows) > 80:
            lines.append(f"| ... | 还有 {len(filled_rows) - 80} 条已写回内容，详见 field_mapping_table.md |  |  |  |")
    else:
        lines.append("未检测到可自动写回的字段。")
        if result_status in {"no_scope_found", "low_confidence_scope"}:
            lines.append("")
            lines.append("原因：未找到足够可信的响应文件/资格性响应文件/其他响应文件等可填写模板范围。")
            lines.append("建议：检查章节标题是否异常，或在上层调用中人工指定模板起始位置后重跑。")

    review_fields = [field for field in fields if field.needs_manual_review]
    lines.extend(["", "## 需人工复核", ""])
    if review_fields:
        lines.append("| 字段 | 原因 | 位置 |")
        lines.append("| --- | --- | --- |")
        for field in review_fields[:40]:
            reason = field.metadata.get("review_reason", "") or "; ".join(field.notes)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_md_cell(field.field_name),
                        _escape_md_cell(_display_value(reason, limit=100)),
                        _escape_md_cell(field.block_id),
                    ]
                )
                + " |"
            )
        if len(review_fields) > 40:
            lines.append(f"| ... | 还有 {len(review_fields) - 40} 条需复核内容，详见 manual_review.json |  |")
    else:
        lines.append("无。")

    lines.append("")
    return "\n".join(lines)


def _build_present_filepaths(output_dir: Path, artifact_paths: list[Path]) -> list[str]:
    """Return paths the DeerFlow agent should pass to present_files.

    In DeerFlow, only files under /mnt/user-data/outputs are presentable to the
    client. Local Windows runs keep absolute paths for debugging, while sandbox
    runs expose the virtual /mnt/user-data/outputs contract.
    """
    output_dir_text = output_dir.as_posix().rstrip("/")
    if output_dir_text == "/mnt/user-data/outputs":
        return [f"/mnt/user-data/outputs/{path.name}" for path in artifact_paths]
    return [str(path) for path in artifact_paths]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bid document filling MVP")
    parser.add_argument("--input-doc", required=True, help="Path to the input .docx/.doc file")
    parser.add_argument(
        "--kb-file",
        default=str(_default_kb_path()),
        help="Path to the enterprise knowledge-base .xlsx. Defaults to the bundled skill asset.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for generated outputs. If omitted, outputs are written to Desktop by default.",
    )
    parser.add_argument(
        "--save-to-desktop",
        dest="save_to_desktop",
        action="store_true",
        default=True,
        help="Copy final artifacts to Desktop (enabled by default).",
    )
    parser.add_argument(
        "--no-save-to-desktop",
        dest="save_to_desktop",
        action="store_false",
        help="Disable copying final artifacts to Desktop.",
    )
    parser.add_argument(
        "--append-summary",
        action="store_true",
        help="Append an auto-fill summary page. Off by default to avoid changing unrelated document layout.",
    )
    parser.add_argument(
        "--writeback-mode",
        choices=("safe", "aggressive"),
        default="safe",
        help="Writeback strategy. 'safe' preserves formatting when possible; 'aggressive' prioritizes fill rate.",
    )
    return parser.parse_args()


def main() -> int:
    _configure_stdout()
    args = parse_args()
    input_path = Path(args.input_doc)
    desktop_dir = _resolve_desktop_dir()
    output_dir = Path(args.output_dir) if args.output_dir else desktop_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_known_outputs(output_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    temp_dir_path = Path(tempfile.mkdtemp(prefix="bid-doc-fill-", dir=str(output_dir.resolve())))
    docx_input_path = _ensure_docx_input(input_path, temp_dir_path)

    knowledge_base = load_knowledge_base(args.kb_file)
    parsed_document = parse_docx(str(docx_input_path))
    scope_detection = detect_fill_scope(parsed_document)
    redline_scope_block_ids = fill_scope_block_ids(parsed_document, scope_detection)
    fields = extract_fields(parsed_document, scope_detection)
    fields = classify_fields(fields, knowledge_base)
    fields = resolve_fields(fields, parsed_document, knowledge_base)

    filled_docx_path = output_dir / FINAL_OUTPUT_DOCX_NAME
    skipped_field_ids = set(
        create_filled_docx(
            str(docx_input_path),
            str(filled_docx_path),
            fields,
            append_summary=args.append_summary,
            writeback_mode=args.writeback_mode,
            redline_scope_block_ids=redline_scope_block_ids,
        )
    )
    _validate_format_preserved(docx_input_path, filled_docx_path)

    for field in fields:
        if field.field_id in skipped_field_ids and field.resolved_value.strip():
            field.needs_manual_review = True
            field.metadata["review_reason"] = "WRITEBACK_UNSAFE"
            field.notes.append("为避免破坏原格式，该字段未自动写回正文，请人工复核。")

    result_payload = build_result_payload(str(input_path), fields, scope_detection)
    mapping_rows = build_field_mapping_rows(fields, skipped_field_ids)

    result_path = output_dir / RESULT_JSON_NAME
    manual_review_path = output_dir / MANUAL_REVIEW_JSON_NAME
    mapping_json_path = output_dir / FIELD_MAPPING_TABLE_JSON_NAME
    mapping_csv_path = output_dir / FIELD_MAPPING_TABLE_CSV_NAME
    mapping_md_path = output_dir / FIELD_MAPPING_TABLE_MD_NAME
    final_response_md_path = output_dir / FINAL_RESPONSE_MD_NAME
    scope_detection_report_path = output_dir / SCOPE_DETECTION_REPORT_JSON_NAME

    _write_json(result_path, result_payload)
    _write_json(manual_review_path, [field.to_dict() for field in fields if field.needs_manual_review])
    _write_json(mapping_json_path, mapping_rows)
    _write_csv(mapping_csv_path, mapping_rows)
    _write_mapping_markdown(mapping_md_path, mapping_rows)
    _write_json(scope_detection_report_path, scope_detection.to_dict())
    final_response_markdown = _build_final_response_markdown(
        filled_docx_path,
        output_dir,
        fields,
        mapping_rows,
        skipped_field_ids,
        scope_detection,
    )
    final_response_md_path.write_text(final_response_markdown, encoding="utf-8")
    filled_content_rows = _build_filled_content_rows(mapping_rows)
    present_filepaths = _build_present_filepaths(
        output_dir,
        [filled_docx_path, final_response_md_path],
    )

    artifacts = {
        "result_json": result_path,
        "manual_review_json": manual_review_path,
        "filled_docx": filled_docx_path,
        "field_mapping_table_json": mapping_json_path,
        "field_mapping_table_csv": mapping_csv_path,
        "field_mapping_table_md": mapping_md_path,
        "final_response_md": final_response_md_path,
        "scope_detection_report_json": scope_detection_report_path,
    }
    desktop_files: dict[str, str] = {}
    if args.save_to_desktop:
        desktop_files = _sync_artifacts_to_desktop(artifacts, desktop_dir)

    shutil.rmtree(temp_dir_path, ignore_errors=True)

    print(
        json.dumps(
            {
                "result_json": str(result_path),
                "manual_review_json": str(manual_review_path),
                "filled_docx": str(filled_docx_path),
                "final_output_docx": str(filled_docx_path),
                "field_mapping_table_json": str(mapping_json_path),
                "field_mapping_table_csv": str(mapping_csv_path),
                "field_mapping_table_md": str(mapping_md_path),
                "final_response_md": str(final_response_md_path),
                "scope_detection_report_json": str(scope_detection_report_path),
                "final_response": final_response_markdown,
                "filled_content": filled_content_rows,
                "filled_content_markdown": final_response_markdown,
                "present_filepaths": present_filepaths,
                "download_filepaths": present_filepaths,
                "output_dir": str(output_dir),
                "status": result_payload["status"],
                "scope_detection": scope_detection.to_dict(),
                "writeback_mode": args.writeback_mode,
                "desktop_dir": str(desktop_dir),
                "desktop_files": desktop_files,
                "field_count": len(fields),
                "filled_count": len(filled_content_rows),
                "manual_review_count": sum(1 for field in fields if field.needs_manual_review),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
