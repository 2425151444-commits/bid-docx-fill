# Output Schema

The skill should produce three canonical user-facing artifacts:

- `final_output.docx`
- `result.json`
- `manual_review.json`

Optional debug artifacts can be added later behind a flag, for example:

- `fields_detected.json`
- `field_mapping.json`
- `validation_report.json`

## result.json

`result.json` is the main machine-readable summary of the workflow.

```json
{
  "document_name": "example.docx",
  "status": "success",
  "summary": {
    "total_fields": 12,
    "filled_fields": 9,
    "uncertain_fields": 2,
    "unfilled_fields": 1
  },
  "artifacts": {
    "final_output_docx": "E:/path/final_output.docx",
    "manual_review_json": "E:/path/manual_review.json"
  },
  "fields": [
    {
      "field_id": "fld-0001",
      "raw_field_name": "联系电话",
      "normalized_field_name": "contact_phone",
      "raw_placeholder": "______",
      "location": {
        "block_id": "p-12",
        "block_type": "paragraph",
        "table_index": null,
        "row_index": null,
        "cell_index": null,
        "char_start": 13,
        "char_end": 19
      },
      "surrounding_text": "联系电话：______",
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
      "writeback_status": "filled",
      "notes": []
    }
  ],
  "issues": []
}
```

## manual_review.json

`manual_review.json` should contain only the fields that still need human attention.

```json
[
  {
    "field_id": "fld-0007",
    "raw_field_name": "授权代表",
    "normalized_field_name": "authorized_representative",
    "surrounding_text": "授权代表：______",
    "candidate_value": "张三",
    "confidence": 0.58,
    "status": "uncertain",
    "review_reason": "LOW_CONFIDENCE",
    "evidence_text": "法定代表人：张三",
    "notes": [
      "The field meaning is close to legal representative but not proven strongly enough."
    ]
  }
]
```

## Suggested CSV export

If you later add CSV output, each row should map to one field:

```text
field_id,raw_field_name,normalized_field_name,candidate_value,value_source,confidence,status,review_reason
```

## Status vocabulary

Use a small, explicit status set:

- `detected`
- `matched`
- `uncertain`
- `not_found`
- `filled`
- `skipped`
- `need_review`

## Review reason vocabulary

Suggested reasons:

- `SEMANTIC_UNCLEAR`
- `NO_CANDIDATE_FOUND`
- `MULTIPLE_CANDIDATES`
- `LOW_CONFIDENCE`
- `WRITEBACK_UNSAFE`
- `WRITEBACK_FAILED`
- `FORMAT_RISK`
