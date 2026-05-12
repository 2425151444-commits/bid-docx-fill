# Bid Docx Fill

一句话总结：这是一个面向 DeerFlow/Codex 的 Word 标书自动回填 skill，用程序解析 `.doc/.docx` 文档，识别后半部分待填写位置，从前文或知识库中匹配值，并在原 Word 文件中原位写回。

## What This Project Is

`bid-docx-fill` solves a very common engineering problem:A user uploads a bid document, response document, contract template, or similar Word document. The first half of the document usually contains factual information such as the project name, supplier name, contact details, address, and amount. The latter half usually contains many underlines, empty tables, blank parentheses, or signature/seal areas, and these need to be filled automatically with information from earlier in the document.
This project does not ask a large language model to rewrite the entire Word document. Instead, it follows a more stable engineering workflow:
1. Parse the original Word structure.
2. Determine the actual scope of the response-file template that needs to be filled.
3. Use rules to identify fields that need to be filled.
4. Match candidate values from the preceding content in the uploaded document and from the local knowledge base.
5. Perform local XML write-back inside the original `.docx`, preserving the format as much as possible.
6. Output auditable JSON, Markdown, and the final Word file.

## Why It Is Needed

If a model is asked to generate a new Word document directly, three problems can easily occur:

-The original formatting, tables, fonts, and layout may be damaged.
-Field sources may be untraceable, making later manual review difficult.
-The model may invent values, creating a high-risk outcome.

The core idea of this project is that the model or Agent is responsible for understanding and orchestration, while scripts handle deterministic parsing, matching, and write-back. In an Agent system, it belongs to the “tool execution layer / Skill layer”; it is not a pure chat response and not merely RAG retrieval.

## Features

- Supports `.docx` input. For `.doc`, it will attempt conversion through Wordconv or LibreOffice.
- Automatically identifies fillable regions such as response-file formats, qualification response documents, and commercial response documents.
- Identifies common fillable items such as underlines, empty parentheses, empty table cells, and row/column anchors.
- Prioritizes values from the preceding content in the uploaded document, then uses `assets/data/knowledge-base.xlsx` as a fallback knowledge base.
- Applies different styles to written values from different sources, making manual inspection easier.
- Preserves `manual_review.json`; low-confidence fields, signatures, handwritten signatures, and uncertain fields will not be silently filled with arbitrary values.
- Generates `final_response.md` and a field mapping table so that the Agent can display the results directly in the conversation.

## Directory Structure

```text
bid-docx-fill/
  SKILL.md
  requirements.txt
  agents/
    openai.yaml
  assets/
    data/
      knowledge-base.xlsx
      classification-table.xlsx
  references/
    workflow_contract.md
    output_schema.md
    field_rules.md
    source_rules.md
    data_packaging.md
    known_edge_cases.md
  scripts/
    deerflow_entry.py
    main.py
    parse_docx_v2.py
    scope_detector.py
    extract_fields_v4.py
    field_semantics.py
    classify_source_v3.py
    resolve_values_v3.py
    fill_docx_inplace_v3.py
    kb_loader.py
    schemas.py
    check_regression_targets.py
```

Key file descriptions：

- `SKILL.md`: Usage instructions for Codex/DeerFlow to read this skill.
- `scripts/deerflow_entry.py`: A stable entry point for the DeerFlow environment.
- `scripts/main.py`: Local command-line end-to-end entry point.
- `scripts/parse_docx_v2.py`: Parses Word paragraphs, tables, and cell structures.
- `scripts/scope_detector.py`: Locates the actual response-file template section.Note that sparse query is currently used; you need to modify the corresponding contract headings to constrain the range of blanks.
- `scripts/extract_fields_v4.py`: Identifies fields that need to be filled.
- `scripts/resolve_values_v3.py`: Matches field values from the document prefix and the knowledge base.
- `scripts/fill_docx_inplace_v3.py`: Performs local write-back inside the original `.docx`.
- `assets/data/knowledge-base.xlsx`: Fixed enterprise or supplier information knowledge base.
- `assets/data/classification-table.xlsx`: Benchmark / field definition data.

## Environment Requirements

Python 3.10 or later is recommended.

Install dependencies:：

```bash
pip install -r requirements.txt
```

The current dependency set is lightweight:：

- `lxml`： Processes Word internal OOXML.
- `openpyxl`：Reads the knowledge-base Excel file.

If you need to process legacy `.doc` files, one of the following additional requirements must be met:

- Install Microsoft Word Converter / Wordconv in a Windows environment.
- Install LibreOffice in a Linux or macOS environment and ensure that `soffice` is available.

If you only process `.docx` files, no additional conversion tool is required.

## Quick Start

Run locally:

```bash
python scripts/main.py --input-doc path/to/input.docx --output-dir outputs --no-save-to-desktop
```

Windows example:

```powershell
python scripts\main.py --input-doc .\demo\input.docx --output-dir .\outputs --no-save-to-desktop
```

View parameters:

```bash
python scripts/main.py --help
```

## DeerFlow / Codex Invocation Method

In a DeerFlow skill environment, it is recommended to call the stable wrapper:

```bash
python3 /mnt/skills/custom/bid-doc-fill/scripts/deerflow_entry.py --output-dir /mnt/user-data/outputs
```

If the user has explicitly uploaded a specific file:

```bash
python3 /mnt/skills/custom/bid-doc-fill/scripts/deerflow_entry.py \
  --input-doc /mnt/user-data/uploads/example.docx \
  --output-dir /mnt/user-data/outputs
```

`deerflow_entry.py` performs several steps:

1. Selects the latest Word file from `/mnt/user-data/uploads`.
2. Calls `scripts/main.py` to execute the complete processing workflow.
3. Verifies that required artifacts exist in the output directory.
4. Returns structured JSON in stdout, making it easier for the upper-layer Agent to display and mount files.

## Output Files

After a successful run, the output directory usually contains:

```text
outputs/
  final_output.docx
  result.json
  manual_review.json
  final_response.md
  field_mapping_table.json
  field_mapping_table.csv
  field_mapping_table.md
  scope_detection_report.json
```

Core artifacts:

- `final_output.docx`: The final Word file after backfilling.
- `result.json`: Overall run status, number of fields, and grouping information.
- `manual_review.json`: Fields requiring manual review.
- `final_response.md`: A summary suitable for direct display to the user.
- `field_mapping_table.*`: Fields, candidate values, sources, confidence, and write-back status.
- `scope_detection_report.json`: Response-template scope detection report.

## Knowledge Base Format

Default knowledge-base path:

```text
assets/data/knowledge-base.xlsx
```

The script reads the first worksheet. The header row must contain a field column and a value column. 

The field column supports one of the following headers:

- `Specific fill item` (`具体填写项`)
- `Field name` (`字段名`)
- `Item` (`项目`)

The value column supports one of the following headers:

- `Remarks` (`备注`)
- `Field value` (`字段值`)
- `Value` (`值`)
- `Content` (`内容`)

Minimal example:

| Specific fill item | Remarks |
| --- | --- |
| 供应商名称 | 某某科技有限公司 |
| 法定代表人 | 张三 |
| 联系电话 | 010-12345678 |
| 地址 | 北京市海淀区示例路 1 号 |

## Workflow

```text
Word input
  -> parse_docx_v2.py parses the document structure
  -> scope_detector.py locates the template range to be filled
  -> extract_fields_v4.py identifies fields that need to be filled
  -> classify_source_v3.py determines each field's preferred source
  -> resolve_values_v3.py matches values from the uploaded document and knowledge base
  -> fill_docx_inplace_v3.py writes back into the Word file in place
  -> output final_output.docx and audit reports
```

In an Agent project, it can be understood as a “deterministic toolchain”:

-The Agent is responsible for task orchestration and explaining results to the user.
-The scripts are responsible for Word parsing, field positioning, evidence recording, and write-back.
-The knowledge base is responsible for providing fixed enterprise information.

## FAQ

### 1.  Runtime error: `Knowledge base file does not exist`

Cause: The default knowledge-base file does not exist, or the path is incorrect.

Troubleshooting:

1. Confirm whether `assets/data/knowledge-base.xlsx` exists.
2. If using a custom knowledge base, confirm that `--kb-file` points to a real path.
3. Confirm that the Excel file is not locked by WPS or Office.

Solution:

```bash
python scripts/main.py \
  --input-doc path/to/input.docx \
  --kb-file assets/data/knowledge-base.xlsx \
  --output-dir outputs
```

### 2. `.doc` file cannot be converted

Cause: `.doc` is an old Word binary format and cannot be parsed directly as OOXML like `.docx`.

Troubleshooting:

1. On Windows, confirm whether `Wordconv.exe` exists.
2. On Linux/macOS, confirm whether `soffice --version` is executable.
3. Prefer asking the user to upload a `.docx` file.

Solution: Save the `.doc` file as `.docx` and then rerun the workflow.

### 3. Very few fields are detected or no automatic filling occurs

Cause: The script did not find a reliable “response-file template section,” or field confidence is insufficient.

Troubleshooting:

1. Check `scope_detection_report.json` to confirm whether the range start is correct.
2. Check `field_mapping_table.md` to confirm whether fields were detected.
3. Check `manual_review.json` to confirm whether fields were marked for manual review.
   
Solution: Optimize document section headings, supplement the knowledge base, or extend heading signals in `scope_detector.py`.

### 4. Abnormal formatting in the final Word file

Cause: The Word document structure may be complex, such as nested tables, text boxes, comments, headers/footers, or special controls.

Troubleshooting:

1. Confirm that the input is a standard `.docx` file.
2. Check whether complex tables or text boxes are present.
3. Use `--writeback-mode safe` to prioritize format preservation.

Solution:

```bash
python scripts/main.py --input-doc path/to/input.docx --output-dir outputs --writeback-mode safe
```

## Current Limitations

- Mainly supports `.docx`; `.doc` depends on external conversion tools.
- Does not currently focus on PDF, scanned files, or OCR.
- Does not yet cover all complex Word elements, such as text boxes, headers/footers, comments, and tracked changes.
- Provides only limited support for highly complex nested tables.
- Signature and handwritten-signature fields default to manual review and should not be automatically generated.

## Notes Before Uploading to GitHub

This project contains `assets/data/*.xlsx`, which may include business data such as fixed enterprise information, contacts, addresses, and account details. Before public upload, confirm the following:

1. Whether `knowledge-base.xlsx` contains sensitive information.
2. Whether `classification-table.xlsx` contains real customer or project data.
3. Whether `outputs/`, `sample-run-*`, `__pycache__/`, and temporary Word files have been cleaned up.
   
The recommended `.gitignore` should include at least:

```gitignore
__pycache__/
*.pyc
outputs/
*.filled.docx
~$*
```

## Suitable Directions for Further Optimization

- Add unit tests and regression test examples.
- Make field rules configurable to reduce hardcoding.
- Add benchmarks for more Word templates.
- Provide anonymized sample files for the knowledge base.
- Add CI checks to ensure that entry scripts and dependency installation work correctly.


