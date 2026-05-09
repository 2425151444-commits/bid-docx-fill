# Known Edge Cases

1. Legacy `.doc` is not parsed directly.
2. OCR images inside Word are ignored.
3. Inline replacement currently focuses on paragraph placeholders, not every table cell variant.
4. The filled draft always appends an `自动回填摘要` section as a safe fallback.
