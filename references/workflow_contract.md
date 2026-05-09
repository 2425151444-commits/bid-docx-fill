# Workflow Contract

This file is the implementation contract for turning `bid-doc-fill` into an auditable multi-step DeerFlow skill for `.docx` auto-fill.

## 1. Architecture in one sentence

Use deterministic document parsing and localized OOXML write-back as the backbone, and use the model only for semantic understanding, candidate matching, and review decisions.

## 2. Recommended skill directory structure

Keep the current folder and converge toward this layout:

```text
bid-doc-fill/
  SKILL.md
  requirements.txt
  agents/
    openai.yaml
  assets/
    data/
      knowledge-base.xlsx
  references/
    workflow_contract.md
    output_schema.md
    field_rules.md
    source_rules.md
  scripts/
    deerflow_entry.py
    main.py
    schemas.py
    parse_docx_v2.py
    extract_fields_v4.py
    field_semantics.py
    classify_source_v3.py
    resolve_values_v3.py
    fill_docx_inplace_v3.py
```

## 3. High-level workflow policy

The workflow should follow this bias:

1. detect more candidate fill targets first
2. normalize and rank them later
3. keep unresolved items visible in the unified mapping table
4. only let write-back stay conservative

This means:

- `high recall` at the field-detection stage
- `high precision` at the value-matching and write-back stage

Undetected blanks are worse than detected-but-unresolved blanks.

## 4. Stage contracts

### Stage A. Parsed document

Input:

- original `.docx` path

Output:

- `parsed_document.json` in memory or optional debug file

Suggested top-level shape:

```json
{
  "document_name": "sample.docx",
  "blocks": [],
  "stats": {
    "paragraph_count": 0,
    "table_row_count": 0
  }
}
```

Each block should preserve enough structure for later matching and write-back:

```json
{
  "block_id": "p-12",
  "block_type": "paragraph",
  "section_name": "Section 3",
  "order_index": 12,
  "text": "Bidder name: ______",
  "runs": [
    {
      "run_index": 0,
      "text": "Bidder name:",
      "char_start": 0,
      "char_end": 12
    },
    {
      "run_index": 1,
      "text": "______",
      "char_start": 12,
      "char_end": 18
    }
  ],
  "table": null
}
```

For a table row:

```json
{
  "block_id": "t-7",
  "block_type": "table_row",
  "section_name": "Appendix",
  "order_index": 52,
  "text": "Contact phone | ",
  "table": {
    "table_index": 2,
    "row_index": 7,
    "cells": [
      {
        "cell_index": 0,
        "text": "Contact phone"
      },
      {
        "cell_index": 1,
        "text": ""
      }
    ]
  }
}
```

### Stage B. Field extraction

Input:

- parsed document

Output:

- `fields_detected.json`

Each extracted target should look like:

```json
{
  "field_id": "fld-0001",
  "raw_placeholder": "______",
  "field_name": "联系电话",
  "inferred_field_name": "contact_phone",
  "field_type": "PHONE",
  "location": {
    "block_id": "p-12",
    "block_type": "paragraph",
    "table_index": null,
    "row_index": null,
    "cell_index": null,
    "char_start": 13,
    "char_end": 19
  },
  "anchor_direction": "anchor_after",
  "surrounding_text": "联系电话：_____",
  "detected_by": "colon_blank",
  "status": "detected"
}
```

Field extraction should optimize for recall first:

- when in doubt, keep a candidate field and let later stages down-rank it
- do not discard a plausible blank only because the final normalized name is not yet certain
- unresolved candidates are acceptable; undetected blanks are worse

For table extraction, treat structure as two-dimensional:

- row-wise anchors
- column-wise anchors
- cell-internal anchors
- row/column intersection anchors

Do not constrain the detector to only `left label -> right blank`.

Recommended table anchor types:

- `left_right`
- `top_down`
- `multi_pair_row`
- `multi_pair_column`
- `cell_inner`
- `row_col_intersection`

For anchor hints such as `（采购代理机构名称）`, store side information:

```json
{
  "field_id": "fld-0010",
  "field_name": "采购代理机构名称",
  "detected_by": "anchor_hint",
  "anchor_direction": "anchor_after",
  "location": {
    "block_id": "p-322",
    "block_type": "paragraph",
    "table_index": null,
    "row_index": null,
    "cell_index": null
  }
}
```

### Stage C. Field semantics

Input:

- extracted fields
- local context from back-half blocks

Output:

- `fields_semantic.json`

Add:

- `normalized_field_name`
- `semantic_reason`
- `semantic_confidence`
- `semantic_status`

This stage is a good fit for LLM plus light rule fallback.

### Stage D. Value resolution

Input:

- semantic fields
- front-half blocks
- knowledge base

Output:

- `field_mapping.json`

Each mapping record should include:

```json
{
  "field_id": "fld-0001",
  "normalized_field_name": "contact_phone",
  "candidate_value": "010-12345678",
  "value_source": "UPLOAD_DOC",
  "evidence_text": "联系人电话：010-12345678",
  "evidence_location": {
    "block_id": "p-3",
    "char_start": 6,
    "char_end": 18
  },
  "confidence": 0.92,
  "status": "matched",
  "alternatives": []
}
```

This stage is partly deterministic and partly LLM-assisted.

### Stage E. Write-back plan

Input:

- resolved mappings
- parsed document with run information

Output:

- `writeback_plan.json`

Example:

```json
{
  "field_id": "fld-0001",
  "target": {
    "block_id": "p-12",
    "block_type": "paragraph",
    "char_start": 13,
    "char_end": 19
  },
  "replacement_text": "010-12345678",
  "write_strategy": "replace_placeholder_span",
  "safe_to_apply": true
}
```

When the field comes from a table or an anchor hint, the plan should also carry structural guidance:

```json
{
  "field_id": "fld-0032",
  "target": {
    "block_id": "t-7",
    "block_type": "table_row",
    "table_index": 2,
    "row_index": 7,
    "cell_index": 1
  },
  "anchor_direction": "top_down",
  "replacement_text": "成都市经济发展研究院",
  "write_strategy": "replace_table_cell_or_anchor_slot",
  "safe_to_apply": true
}
```

This stage should be programmatic, not LLM-driven.

### Stage F. Validation report

Input:

- original fields
- mappings
- write-back results

Output:

- `validation_report.json`

Example:

```json
{
  "summary": {
    "total_fields": 28,
    "filled_fields": 20,
    "uncertain_fields": 5,
    "unfilled_fields": 3
  },
  "issues": [
    {
      "field_id": "fld-0021",
      "issue_type": "low_confidence",
      "message": "Candidate value found but confidence below threshold.",
      "confidence": 0.58
    }
  ]
}
```

## 5. What should be LLM-driven

Good fits for LLM:

- infer the semantic meaning of a placeholder from nearby text
- map a normalized field to the best source snippet from the front half
- rank competing candidates
- explain uncertainty in human-review language

Bad fits for LLM:

- parsing OOXML
- placeholder span indexing
- run-level replacement
- table cell write-back
- checking whether a placeholder still exists after replacement

## 6. MVP scope

MVP should support:

- `.docx` input only
- paragraph placeholders such as `Label: ____`
- date placeholders such as `年 月 日`
- empty table cells where the label is in the left cell
- empty table cells where the label is above the blank cell
- rows or columns with multiple field pairs
- cell-internal paragraph anchors such as `（采购代理机构名称）：`
- values sourced from earlier blocks of the same document
- knowledge-base fallback
- manual review output for low-confidence or unresolved fields

MVP should not yet support:

- headers and footers
- text boxes and shapes
- footnotes/endnotes
- checkboxes/content controls
- fully reliable merged-cell reasoning in arbitrary tables

## 7. Key implementation notes

### Placeholder location

Do not store only `block_id`. Also store char span or target cell index. Otherwise write-back becomes ambiguous when one paragraph has multiple blanks.

### Run-level replacement

Use a paragraph text view plus a run-to-char mapping:

1. concatenate all run texts
2. map each run to char offsets
3. replace the target span in the concatenated text
4. write back by slicing the updated text across the original run boundaries

This is the safest way to preserve styling most of the time.

### Table blanks

Treat tables in at least these classes:

1. row label plus blank value cell
2. one-cell tables that contain paragraph-like content
3. upper label plus lower blank value cell
4. multi-pair rows and columns
5. row/column header intersections that should at least be detected

If a table blank cannot yet be auto-filled safely, still emit it into the unified field mapping table so that later matching and manual review can see it.

### Confidence policy

Suggested thresholds:

- `>= 0.85`: auto-fill
- `0.60 ~ 0.84`: fill only if policy allows, otherwise send to review
- `< 0.60`: do not auto-fill

### Evidence design

Every non-manual value should keep:

- source type
- source block id
- evidence snippet
- confidence reason

Without evidence, the result is not auditable.

## 8. Direct coding order

Implement in this order:

1. stabilize `parse_docx_v2.py` so it emits block order, table location, and optional run metadata
2. stabilize `extract_fields_v4.py` so each field has precise `location`, `anchor_direction`, and table-anchor type
3. expand `schemas.py` to model field semantics, candidates, and validation issues
4. split `resolve_values_v3.py` into candidate generation and candidate scoring
5. keep `fill_docx_inplace_v3.py` fully programmatic and driven by `writeback_plan`
6. update `main.py` so it can optionally dump intermediate JSON files for debugging
7. update `deerflow_entry.py` to expose canonical DeerFlow outputs only
