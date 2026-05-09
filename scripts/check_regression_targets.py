from __future__ import annotations

import argparse
from pathlib import Path

from extract_fields_v4 import extract_fields
from parse_docx_v2 import parse_docx


EXPECTED = {
    "p-322": {"采购代理机构名称"},
    "p-325": {"项目完成时间"},
    "p-327": {"磋商有效期"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check key regression targets for bid-doc-fill")
    parser.add_argument("--input-doc", required=True, help="Path to the source .docx")
    args = parser.parse_args()

    doc_path = Path(args.input_doc)
    parsed = parse_docx(str(doc_path))
    fields = extract_fields(parsed)

    actual: dict[str, set[str]] = {block_id: set() for block_id in EXPECTED}
    for field in fields:
        if field.block_id in actual:
            actual[field.block_id].add(field.field_name)

    ok = True
    for block_id, expected_names in EXPECTED.items():
        missing = expected_names - actual.get(block_id, set())
        print(block_id, "expected=", sorted(expected_names), "actual=", sorted(actual.get(block_id, set())))
        if missing:
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
