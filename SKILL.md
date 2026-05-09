---
name: bid-doc-fill
description: Understand a user-uploaded .docx bid document, contract, or similar template, detect fillable placeholders mainly in the later sections, infer each field meaning from local context, match candidate values from the earlier sections or a bundled knowledge base, and write values back into the original .docx without regenerating the whole document. Use when DeerFlow needs format-preserving docx auto-fill, placeholder extraction, structured field mapping, or auditable manual-review output.
---

# Bid Doc Fill

Use this skill for `.docx` templates where the front half contains source facts and the back half contains blanks, placeholders, or empty table cells that should be filled without rewriting the whole document.

## Core Principle

Treat this as a multi-step workflow, not a single prompt:

1. Parse the original `.docx` structure.
2. Detect candidate fill targets deterministically.
3. Use model reasoning only for field meaning and value matching when rules are not enough.
4. Write back into the original `.docx` by localized replacement.
5. Produce auditable structured outputs and a review report.

Never ask the model to regenerate the full Word document body.

## Default DeerFlow Run Path

When DeerFlow invokes this skill, prefer the stable wrapper:

```bash
python3 /mnt/skills/custom/bid-doc-fill/scripts/deerflow_entry.py --output-dir /mnt/user-data/outputs
```

This wrapper should:

- pick the latest uploaded `.docx` from `/mnt/user-data/uploads`
- write final artifacts into `/mnt/user-data/outputs`
- fail if canonical outputs are missing

When an explicit input file is provided, still pass `--output-dir /mnt/user-data/outputs`:

```bash
python3 /mnt/skills/custom/bid-doc-fill/scripts/deerflow_entry.py --input-doc /mnt/user-data/uploads/example.docx --output-dir /mnt/user-data/outputs
```

If the exact uploaded filename is uncertain, omit `--input-doc` and let `deerflow_entry.py` pick the latest uploaded Word file. Do not repeatedly guess filenames with different spaces, hyphens, or bracket styles. The wrapper will also try to match normalized upload names and fall back to the latest uploaded Word file when an explicit `--input-doc` path is not found.

Canonical outputs:

- `final_output.docx`
- `result.json`
- `manual_review.json`
- `final_response.md`

The wrapper stdout must also include `final_response` / `filled_content_markdown` so the agent can show the result directly in chat.
Do not make the user browse the output directory just to understand what was filled.

For DeerFlow chat delivery, generating files is not enough. After the wrapper succeeds, the assistant must call `present_files` with the paths from `present_filepaths`, usually:

- `/mnt/user-data/outputs/final_output.docx`
- `/mnt/user-data/outputs/final_response.md`

This is required for the frontend/channel layer to expose download attachments.

## Workflow

### 1. Parse and segment the document

Use the parser to build block-level structure from the original `.docx`:

- paragraphs
- table rows
- table-cell paragraphs
- run-aware text views when replacement is needed

Prefer a front/back segmentation heuristic based on document order plus heading density:

- front half: source facts such as company name, project name, legal representative, address, phone, amount, date
- back half: repeated blanks, form sections, signature/date blocks, table forms

### 1.5. Detect the fillable response-template scope

Before extracting fields, run the deterministic scope detector. The goal is not to match one exact heading such as `响应文件格式`; the goal is to find the section where the supplier is expected to fill response templates.

Use `scripts/scope_detector.py` to combine multiple signals:

- positive title signals: `响应文件格式`, `资格性响应文件`, `其他响应文件`, `商务响应文件`, `技术响应文件`, `报价文件`, `投标文件格式`, `申请文件格式`, `响应函`, `磋商函`, `报价一览表`, `供应商基本情况表`, `法定代表人授权书`, `授权委托书`, `承诺函`
- fill-density signals in the following blocks: `项目名称`, `项目编号`, `供应商名称`, `法定代表人`, `授权代表`, `联系电话`, `报价`, `盖章`, `签字`, `年 月 日`, underline blanks, blank parentheses, and empty table cells
- negative boundary signals: `供应商须知`, `响应文件的编制`, `响应文件的递交`, `资格审查`, `评审办法`, `评分标准`, `采购需求`, `合同条款`, `政府采购合同`

Start selection priority:

1. Prefer a real top-level chapter heading such as `第六章 响应文件格式`.
2. If no reliable top-level chapter exists, use the earliest high-confidence response part such as `文件一、资格性响应文件封面格式` or `第一部分资格性响应文件(格式)`.
3. Treat `1.1资格性响应文件`, `响应文件的组成`, and other supplier-instruction subsections as negative or low-priority unless the following blocks contain strong fillable-template evidence.

The detector must produce `scope_detection_report.json` with:

- selected start block/text
- selected end block/text
- score and confidence
- reasons for the decision
- top candidate headings for debugging

If scope detection returns `no_scope_found` or `low_confidence_scope`, do not repeatedly call the same search/extraction tools. Return the status, preserve the original document as `final_output.docx`, and tell the user to inspect the report or manually specify the start location.

### 2. Detect fill targets

Only detect and fill targets inside the response-file template range:

- start after the document reaches the real chapter/template heading, such as `第六章 响应文件格式`, `响应文件格式`, `响应文件组成格式`, `响应文件编制格式`, or similar
- do not treat table-of-contents entries, numbered subsections such as `17.响应文件格式`, or ordinary body-text mentions of `响应文件` as the start boundary
- stop before a real contract chapter/template heading only when it belongs to the final one or two chapters near the end of the document, such as `第八章 合同主要条款`, `合同主要条款`, `合同条款`, `主要合同条款`, `合同格式`, `合同协议书`, or similar
- do not stop just because earlier body text, review rules, or middle chapters mention `合同条款`
- do not detect or write blanks outside this range, even if they look fillable
- do not auto-fill blanks whose field name or hint contains `签字` or `签名`; keep them for handwritten completion

Detect placeholders with deterministic rules first:

- underline-like blanks such as `______`
- blank parentheses or brackets
- `Label: ____`
- empty or placeholder table cells
- one-cell table paragraphs like `Date:` with missing value

Treat detection as a high-recall stage:

- prefer capturing more candidate fill targets first
- resolve and filter later using evidence, confidence, and review policy
- do not keep the detector artificially narrow just to avoid false positives

For tables, do not limit detection to `left label -> right blank` only. Also inspect:

- `upper label -> lower blank`
- one row with multiple field pairs such as `联系人 | [空] | 电话 | [空]`
- one column with repeated `label -> blank`
- paragraph-like anchors inside cells such as `（采购代理机构名称）：`
- row/column header intersections where the blank cell is defined by both axes

For anchor-like hints such as `（采购代理机构名称）`, do not assume the fill slot is always after the hint. The detector should inspect both sides:

1. check whether the nearest blank slot exists after the hint
2. if not, check whether the nearest blank slot exists before the hint
3. if both exist, prefer the after-side by default
4. store the chosen anchor direction for write-back

Capture location and context for every target. The minimum target record should include:

- `field_id`
- `raw_placeholder`
- `inferred_field_name`
- `location`
- `surrounding_text`

### 3. Infer field meaning

Use model reasoning only when deterministic naming is weak.

Normalize each target into a business-friendly field name such as:

- `bidder_name`
- `legal_representative`
- `contact_phone`
- `registered_address`
- `project_name`
- `bid_price`
- `sign_date`

If the meaning is unclear, mark it as `need_review` instead of guessing.

### 4. Resolve candidate values

Try value resolution in this order:

1. earlier sections of the same uploaded document
2. bundled knowledge base
3. manual review

For every resolved value, keep:

- candidate value
- evidence text
- evidence location
- confidence
- status

Write resolved values with visible source styling in the final Word file:

- values from the uploaded document/front section: bold blue
- values from the enterprise knowledge base: bold green
- values that require business/manual completion, including dynamic fallback values and unresolved visible blanks: bold red

For manual or handwritten fields, do not insert artificial text such as `please fill`; keep the original blank/underline and only style the visible placeholder when possible.

Do not silently fill low-confidence guesses.

### 5. Write back in place

Perform targeted replacement inside the original `.docx`.

The final `.docx` must be the canonical `final_output.docx` produced by `scripts/main.py` / `scripts/deerflow_entry.py`.
Never create the final user-facing Word file from extracted text, Markdown, `antiword`, `catdoc`, or a fresh `python-docx.Document()`.
Those tools may be used only for diagnostics, not for deliverable generation.

Prefer:

- paragraph text view plus run mapping
- table-cell localized replacement
- minimal XML edits

When a field is defined by an anchor hint instead of a plain label, write-back should follow the detected anchor direction instead of assuming `field -> value` is always left-to-right.

Avoid:

- replacing the whole paragraph XML
- rebuilding the whole document from plain text
- rewriting layout, table geometry, or styles

### 6. Post-fill validation

After write-back, inspect the result for:

- unresolved placeholders
- low-confidence fills
- conflicting values for the same normalized field
- suspicious formatting changes

Return those items in `manual_review.json` and the summary section of `result.json`.

### 7. User-facing final response

After the scripts finish, the assistant must directly return:

- the canonical final Word file path from `final_output_docx`
- a concise filled-content table from `filled_content_markdown` or `final_response`
- manual-review items that still require human action
- and call `present_files` for `present_filepaths` so the final Word file appears as a downloadable artifact

Do not only say that files were written to `/mnt/user-data/outputs` or another folder.
The user should be able to see the fill result from the chat response without opening intermediate JSON or browsing folders.

## What The Model Should And Should Not Do

Use the model for:

- field semantic normalization
- matching values to fields using local evidence
- conflict judgment when multiple candidates exist

Do not use the model for:

- raw OOXML editing
- run-level replacement
- table cell write-back
- file format conversion
- deciding that a risky guess is safe without evidence

## Current MVP Boundary

Support first:

- `.docx` only
- common bid/contract templates
- obvious placeholder patterns
- common table forms where blanks can be understood from row, column, or cell-internal anchors
- values that can be found from earlier document text or the bundled knowledge base
- auditable JSON outputs

Do not target in MVP:

- PDF
- OCR or scanned files
- headers, footers, text boxes, comments, tracked changes
- extremely complex nested tables
- one-shot support for every template style

## Recommended Scripts

Current scripts already cover most of the MVP path. Use them as the execution backbone and evolve them incrementally instead of starting over:

- `scripts/deerflow_entry.py`: DeerFlow wrapper
- `scripts/main.py`: end-to-end CLI orchestrator
- `scripts/parse_docx_v2.py`: block extraction
- `scripts/scope_detector.py`: multi-signal response-template scope detection with `scope_detection_report.json`
- `scripts/extract_fields_v4.py`: deterministic placeholder detection
- `scripts/field_semantics.py`: field typing and normalization helpers
- `scripts/resolve_values_v3.py`: value matching
- `scripts/fill_docx_inplace_v3.py`: in-place write-back
- `scripts/kb_loader.py`: bundled KB loading
- `scripts/schemas.py`: shared data structures

Read the following references only when needed:

- `references/workflow_contract.md`: stage-by-stage design, script boundaries, JSON contracts, MVP implementation order
- `references/output_schema.md`: expected artifact schema
- `references/field_rules.md`: supported placeholder patterns and extraction limits
- `references/source_rules.md`: resolution priority and source policy
