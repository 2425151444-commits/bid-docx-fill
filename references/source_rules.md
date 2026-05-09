# Source Rules

Use the following priority in the MVP:

1. `UPLOAD_DOC`
   Trigger when the field name matches upload-document keywords such as `项目编号`, `项目名称`, `日期`, `采购代理机构名称`.
2. `KNOWLEDGE_BASE`
   Trigger when the normalized field can be matched in the enterprise knowledge base.
3. `DYNAMIC`
   Use when neither of the above is reliable.

The first version intentionally keeps the rule set small and deterministic.
