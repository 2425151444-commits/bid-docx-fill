from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


WORD_SUFFIXES = {".doc", ".docx"}


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def _default_skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_upload_dir() -> Path:
    return Path("/mnt/user-data/uploads")


def _default_output_dir() -> Path:
    return Path("/mnt/user-data/outputs")


def _default_kb_path() -> Path:
    return _default_skill_root() / "assets" / "data" / "knowledge-base.xlsx"


def _find_input_doc(upload_dir: Path) -> Path:
    candidates = [
        path
        for path in upload_dir.iterdir()
        if path.is_file() and path.suffix.lower() in WORD_SUFFIXES
    ]
    if not candidates:
        raise FileNotFoundError(f"No .doc or .docx file found in upload dir: {upload_dir}")

    # Prefer the most recently modified upload so the wrapper behaves well in
    # Deer-Flow threads where users may upload multiple versions over time.
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _normalize_upload_name(value: str) -> str:
    text = value or ""
    text = text.replace("〔", "[").replace("〕", "]")
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("－", "-").replace("—", "-").replace("–", "-")
    return "".join(text.split()).lower()


def _resolve_input_doc(input_doc: Path, upload_dir: Path) -> tuple[Path, str]:
    if input_doc.exists():
        return input_doc, ""

    if not upload_dir.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_doc}")

    candidates = [
        path
        for path in upload_dir.iterdir()
        if path.is_file() and path.suffix.lower() in WORD_SUFFIXES
    ]
    if not candidates:
        raise FileNotFoundError(f"Input file does not exist and upload dir has no Word files: {input_doc}")

    requested_name = _normalize_upload_name(input_doc.name)
    for candidate in candidates:
        if _normalize_upload_name(candidate.name) == requested_name:
            return candidate, f"requested input path was not found; matched uploaded filename: {candidate.name}"

    fallback = max(candidates, key=lambda path: path.stat().st_mtime)
    return fallback, f"requested input path was not found; fell back to latest uploaded Word file: {fallback.name}"


def _looks_like_virtual_user_data_path(path: Path) -> bool:
    return path.as_posix().rstrip("/") in {
        "/mnt/user-data",
        "/mnt/user-data/uploads",
        "/mnt/user-data/outputs",
        "/mnt/user-data/workspace",
    }


def _user_data_root_from_path(path: Path) -> Path | None:
    resolved = path.expanduser().resolve()
    current = resolved if resolved.is_dir() else resolved.parent
    for candidate in [current, *current.parents]:
        if candidate.name == "user-data":
            return candidate
    return None


def _runtime_user_data_root(input_doc: Path | None = None) -> Path | None:
    if input_doc is not None:
        root = _user_data_root_from_path(input_doc)
        if root is not None:
            return root

    cwd_root = _user_data_root_from_path(Path.cwd())
    if cwd_root is not None:
        return cwd_root

    return None


def _resolve_runtime_dir(kind: str, requested: str | None, input_doc: Path | None = None) -> Path:
    default_virtual = _default_upload_dir() if kind == "uploads" else _default_output_dir()
    requested_path = Path(requested) if requested else default_virtual

    if requested and not _looks_like_virtual_user_data_path(requested_path):
        return requested_path

    root = _runtime_user_data_root(input_doc)
    if root is not None:
        return root / kind

    return requested_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stable Deer-Flow entrypoint for bid-doc-fill")
    parser.add_argument("--input-doc", default=None, help="Optional explicit input .doc/.docx path")
    parser.add_argument("--upload-dir", default=None, help="Directory to scan for uploaded files")
    parser.add_argument("--output-dir", default=None, help="Directory for final artifacts")
    parser.add_argument("--kb-file", default=str(_default_kb_path()), help="Knowledge base xlsx path")
    parser.add_argument(
        "--writeback-mode",
        choices=("safe", "aggressive"),
        default="aggressive",
        help="Writeback mode used by Deer-Flow entrypoint. Defaults to aggressive for higher fill rate.",
    )
    return parser.parse_args()


def main() -> int:
    _configure_stdout()
    args = parse_args()
    requested_input_doc = Path(args.input_doc) if args.input_doc else None
    upload_dir = _resolve_runtime_dir("uploads", args.upload_dir, requested_input_doc)
    output_dir = _resolve_runtime_dir("outputs", args.output_dir, requested_input_doc)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_doc_warning = ""
    if requested_input_doc:
        input_doc, input_doc_warning = _resolve_input_doc(requested_input_doc, upload_dir)
    else:
        input_doc = _find_input_doc(upload_dir)
    main_script = Path(__file__).resolve().parent / "main.py"

    command = [
        sys.executable,
        str(main_script),
        "--input-doc",
        str(input_doc),
        "--kb-file",
        str(args.kb_file),
        "--output-dir",
        str(output_dir),
        "--no-save-to-desktop",
        "--writeback-mode",
        args.writeback_mode,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "bid-doc-fill main.py failed in Deer-Flow entrypoint. "
            f"exit_code={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

    stdout = result.stdout.strip()
    payload = json.loads(stdout) if stdout else {}

    required = {
        "result_json": output_dir / "result.json",
        "manual_review_json": output_dir / "manual_review.json",
        "final_output_docx": output_dir / "final_output.docx",
        "final_response_md": output_dir / "final_response.md",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise RuntimeError(
            "Deer-Flow entrypoint did not produce the required canonical outputs: "
            + ", ".join(missing)
        )

    payload.setdefault("result_json", str(required["result_json"]))
    payload.setdefault("manual_review_json", str(required["manual_review_json"]))
    payload.setdefault("final_output_docx", str(required["final_output_docx"]))
    payload.setdefault("final_response_md", str(required["final_response_md"]))
    if "final_response" not in payload and required["final_response_md"].exists():
        payload["final_response"] = required["final_response_md"].read_text(encoding="utf-8")
    if "filled_content_markdown" not in payload and "final_response" in payload:
        payload["filled_content_markdown"] = payload["final_response"]
    payload.setdefault(
        "present_filepaths",
        [
            "/mnt/user-data/outputs/final_output.docx",
            "/mnt/user-data/outputs/final_response.md",
        ],
    )
    payload.setdefault("download_filepaths", payload["present_filepaths"])
    payload.setdefault("input_doc", str(input_doc))
    if requested_input_doc is not None:
        payload.setdefault("requested_input_doc", str(requested_input_doc))
    if input_doc_warning:
        payload.setdefault("input_doc_warning", input_doc_warning)
    payload.setdefault("writeback_mode", args.writeback_mode)
    payload.setdefault("status", "success")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
