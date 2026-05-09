# Field Rules

The skill should treat field detection as a high-recall stage:

- detect as many plausible fill targets as possible first
- normalize and score them later
- prefer `detected but unresolved` over `never detected`

This is especially important for table forms, where the blank often does not look like a simple `Label: ____` paragraph.

## Detection scope

Only detect fill targets in the response-file template range:

1. start after the real chapter/template heading, such as `第六章 响应文件格式`, `响应文件格式`, `响应文件组成格式`, `响应文件编制格式`, or similar
2. do not treat table-of-contents entries, numbered subsections such as `17.响应文件格式`, or ordinary body-text mentions of `响应文件` as the start boundary
3. stop before a real contract chapter/template heading only when it belongs to the final one or two chapters near the end of the document, such as `第八章 合同主要条款`, `合同主要条款`, `合同条款`, `主要合同条款`, `合同格式`, `合同协议书`, or similar
4. do not stop just because earlier body text, review rules, or middle chapters mention `合同条款`
5. ignore blanks outside this range, even if they match placeholder patterns
6. never auto-fill blanks whose field name or hint contains `签字` or `签名`; these are handwritten fields

## Supported paragraph patterns

1. `Label: ____`
2. `Label:` with no value after the colon
3. `______（字段提示）`
4. `（字段提示）______`
5. `（字段提示）：`
6. `“______”项目`
7. `字段名为 ____`
8. `字段名是 ____`

## Supported table patterns

Treat tables as two-dimensional anchor structures, not only row text.

### A. Left label, right blank

Examples:

- `供应商名称 | [空]`
- `联系人 | ______`

Detection rule:

- if the current cell looks like a label
- and a later cell in the same row is empty or placeholder-like
- create a fill target for that blank cell

### B. Upper label, lower blank

Examples:

- first row: `项目负责人`
- second row: `[空]`

Detection rule:

- if the current cell looks like a label
- and the cell directly below is empty or placeholder-like
- create a fill target for the lower cell

### C. One row with multiple field pairs

Examples:

- `联系人 | [空] | 电话 | [空]`
- `地址 | [空] | 邮编 | [空]`

Detection rule:

- scan the row pairwise instead of assuming only one field per row
- every `label -> blank` pair should become its own field

### D. One column with multiple field pairs

Examples:

- row 1: `供应商名称`
- row 2: `[空]`
- row 3: `法定代表人`
- row 4: `[空]`

Detection rule:

- detect repeated vertical `label -> blank` structures in the same column

### E. Cell-internal paragraph anchors

Examples:

- `（采购代理机构名称）：`
- `______（项目名称）`
- `项目完成时间为 ______`

Detection rule:

- if the cell text itself contains paragraph-like placeholder syntax
- run the same anchor rules used for body paragraphs inside the cell

### F. Row/column header intersection

Examples:

- row header is `联系人`
- column header is `电话`
- the intersection cell is blank

Detection rule:

- if both row-header and column-header semantics exist
- keep the candidate as a detected field even if auto-fill is not yet enabled
- this should enter the field table for later review or future matching

## Anchor-side policy

For hint-like fields such as `（采购代理机构名称）`, do not assume the blank is always after the hint.

The detector should:

1. locate the hint anchor
2. inspect the nearest candidate slot after the hint
3. inspect the nearest candidate slot before the hint
4. prefer the after-slot if both exist
5. otherwise use the available side

Supported slot forms:

- underline clusters
- dot clusters
- long spaces
- quoted blanks
- blank-like bracket interiors

## Detection output requirements

Every detected field should preserve enough structure for later write-back:

- `field_id`
- `field_name`
- `detected_by`
- `block_id`
- `block_type`
- `table_index` when applicable
- `row_index` when applicable
- `cell_index` when applicable
- `anchor_direction`
- `surrounding_text`

Recommended `anchor_direction` values:

- `left_right`
- `top_down`
- `cell_inner`
- `row_col_intersection`
- `anchor_before`
- `anchor_after`

## Current limits

The current skill still has practical limits even after the rule expansion:

1. `.docx` only
2. no OCR or scanned documents
3. no headers, footers, or text boxes
4. merged-cell reasoning is weak and should be treated as review-first
5. complex financial tables may be detected but still require manual confirmation
