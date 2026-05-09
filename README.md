# Bid Docx Fill

一句话总结：这是一个面向 DeerFlow/Codex 的 Word 标书自动回填 skill，用程序解析 `.doc/.docx` 文档，识别后半部分待填写位置，从前文或知识库中匹配值，并在原 Word 文件中原位写回。

## 这个项目是什么

`bid-docx-fill` 解决的是一类很常见的工程问题：

用户上传一份标书、响应文件、合同模板或类似 Word 文档，文档前半部分通常包含项目名称、供应商名称、联系方式、地址、金额等事实信息；文档后半部分通常包含很多下划线、空表格、括号空白或签章区域，需要把前面的信息自动填进去。

这个项目不是让大模型重写整份 Word，而是走一个更稳定的工程流程：

1. 解析原始 Word 结构。
2. 判断真正需要填写的响应文件模板范围。
3. 用规则识别待填写字段。
4. 从上传文档前文和本地知识库中匹配候选值。
5. 在原 `.docx` 内做局部 XML 写回，尽量保持格式不变。
6. 输出可审计的 JSON、Markdown 和最终 Word 文件。

## 为什么需要它

如果直接让模型生成一份新的 Word，容易出现三个问题：

- 原文格式、表格、字体和排版被破坏。
- 字段来源不可追踪，后续不好人工复核。
- 模型可能凭空补值，风险很高。

本项目的核心思路是：模型或 Agent 负责理解和编排，脚本负责确定性解析、匹配和写回。放在 Agent 系统里，它属于“工具执行层 / Skill 层”，不是纯聊天回答，也不是单纯 RAG 检索。

## 功能特性

- 支持 `.docx` 输入，`.doc` 会尝试通过 Wordconv 或 LibreOffice 转换。
- 自动识别响应文件格式、资格性响应文件、商务响应文件等可填写区域。
- 识别下划线、空括号、表格空单元格、行列锚点等常见待填项。
- 从上传文档前文优先取值，再使用 `assets/data/knowledge-base.xlsx` 作为兜底知识库。
- 对不同来源的写入值使用不同样式，方便人工检查。
- 保留 `manual_review.json`，低置信度、签字签名、无法确定的字段不会静默乱填。
- 生成 `final_response.md` 和字段映射表，方便 Agent 在对话中直接展示结果。

## 目录结构

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

关键文件说明：

- `SKILL.md`：给 Codex/DeerFlow 读取的 skill 使用说明。
- `scripts/deerflow_entry.py`：DeerFlow 环境下的稳定入口。
- `scripts/main.py`：本地命令行端到端入口。
- `scripts/parse_docx_v2.py`：解析 Word 段落、表格、单元格结构。
- `scripts/scope_detector.py`：定位真正的响应文件模板区。
- `scripts/extract_fields_v4.py`：识别待填写字段。
- `scripts/resolve_values_v3.py`：从文档前文和知识库中匹配字段值。
- `scripts/fill_docx_inplace_v3.py`：在原 `.docx` 中局部写回。
- `assets/data/knowledge-base.xlsx`：企业或供应商固定信息知识库。
- `assets/data/classification-table.xlsx`： benchmark / 字段定义数据。

## 环境要求

建议使用 Python 3.10 或更高版本。

安装依赖：

```bash
pip install -r requirements.txt
```

当前依赖很轻：

- `lxml`：处理 Word 内部 OOXML。
- `openpyxl`：读取知识库 Excel。

如果要处理老式 `.doc` 文件，还需要额外满足其一：

- Windows 环境安装 Microsoft Word Converter / Wordconv。
- Linux 或 macOS 环境安装 LibreOffice，并保证 `soffice` 可用。

如果只处理 `.docx`，不需要额外转换工具。

## 快速开始

本地运行：

```bash
python scripts/main.py --input-doc path/to/input.docx --output-dir outputs --no-save-to-desktop
```

Windows 示例：

```powershell
python scripts\main.py --input-doc .\demo\input.docx --output-dir .\outputs --no-save-to-desktop
```

查看参数：

```bash
python scripts/main.py --help
```

## DeerFlow / Codex 调用方式

在 DeerFlow skill 环境中，推荐调用稳定 wrapper：

```bash
python3 /mnt/skills/custom/bid-doc-fill/scripts/deerflow_entry.py --output-dir /mnt/user-data/outputs
```

如果用户明确上传了某个文件：

```bash
python3 /mnt/skills/custom/bid-doc-fill/scripts/deerflow_entry.py \
  --input-doc /mnt/user-data/uploads/example.docx \
  --output-dir /mnt/user-data/outputs
```

`deerflow_entry.py` 会做几件事：

1. 从 `/mnt/user-data/uploads` 选择最新 Word 文件。
2. 调用 `scripts/main.py` 执行完整处理流程。
3. 确认输出目录中存在必需产物。
4. 在 stdout 中返回结构化 JSON，方便上层 Agent 展示和挂载文件。

## 输出文件

运行成功后，输出目录通常包含：

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

核心产物：

- `final_output.docx`：最终回填后的 Word 文件。
- `result.json`：整体运行状态、字段数量、分组信息。
- `manual_review.json`：需要人工复核的字段。
- `final_response.md`：适合直接展示给用户的总结。
- `field_mapping_table.*`：字段、候选值、来源、置信度、写回状态。
- `scope_detection_report.json`：响应模板范围识别报告。

## 知识库格式

默认知识库路径：

```text
assets/data/knowledge-base.xlsx
```

脚本会读取第一个 worksheet。表头需要包含字段列和值列。

字段列支持这些表头之一：

- `具体填写项`
- `字段名`
- `项目`

值列支持这些表头之一：

- `备注`
- `字段值`
- `值`
- `内容`

最小示例：

| 具体填写项 | 备注 |
| --- | --- |
| 供应商名称 | 某某科技有限公司 |
| 法定代表人 | 张三 |
| 联系电话 | 010-12345678 |
| 地址 | 北京市海淀区示例路 1 号 |

## 工作流程

```text
Word 输入
  -> parse_docx_v2.py 解析文档结构
  -> scope_detector.py 定位待填写模板范围
  -> extract_fields_v4.py 识别待填写字段
  -> classify_source_v3.py 判断字段优先来源
  -> resolve_values_v3.py 匹配上传文档和知识库值
  -> fill_docx_inplace_v3.py 原位写回 Word
  -> 输出 final_output.docx 和审计报告
```

在 Agent 项目中，可以把它理解为一个“确定性工具链”：

- Agent 负责任务编排和向用户解释结果。
- 脚本负责 Word 解析、字段定位、证据记录和写回。
- 知识库负责提供企业固定信息。

## 常见问题

### 1. 运行时报 `Knowledge base file does not exist`

本质：默认知识库文件不存在，或者路径传错了。

排查：

1. 确认 `assets/data/knowledge-base.xlsx` 是否存在。
2. 如果使用自定义知识库，确认 `--kb-file` 指向真实路径。
3. 确认 Excel 文件没有被 WPS 或 Office 独占锁定。

解决：

```bash
python scripts/main.py \
  --input-doc path/to/input.docx \
  --kb-file assets/data/knowledge-base.xlsx \
  --output-dir outputs
```

### 2. `.doc` 文件无法转换

本质：`.doc` 是旧版 Word 二进制格式，不能像 `.docx` 一样直接解析 OOXML。

排查：

1. Windows 上确认是否存在 `Wordconv.exe`。
2. Linux/macOS 上确认 `soffice --version` 是否可执行。
3. 优先让用户上传 `.docx`。

解决：把 `.doc` 另存为 `.docx` 后再运行。

### 3. 识别字段很少或没有自动填写

本质：脚本没有找到可信的“响应文件模板区”，或者字段置信度不足。

排查：

1. 查看 `scope_detection_report.json`，确认范围起点是否正确。
2. 查看 `field_mapping_table.md`，确认字段是否被识别。
3. 查看 `manual_review.json`，确认是否被标记为人工复核。

解决：优化文档章节标题、补充知识库，或在代码中扩展 `scope_detector.py` 的标题信号。

### 4. 最终 Word 格式异常

本质：Word 文档结构可能比较复杂，比如嵌套表格、文本框、批注、页眉页脚或特殊控件。

排查：

1. 确认输入是否是标准 `.docx`。
2. 查看是否存在复杂表格或文本框。
3. 使用 `--writeback-mode safe` 优先保护格式。

解决：

```bash
python scripts/main.py --input-doc path/to/input.docx --output-dir outputs --writeback-mode safe
```

## 当前限制

- 主要支持 `.docx`，`.doc` 依赖外部转换工具。
- 暂不重点支持 PDF、扫描件、OCR。
- 暂不覆盖所有复杂 Word 元素，例如文本框、页眉页脚、批注、修订痕迹。
- 对极复杂嵌套表格只做有限支持。
- 签字、签名类字段默认更偏人工复核，不建议自动生成。

## 上传 GitHub 前的注意事项

这个项目包含 `assets/data/*.xlsx`，其中可能有企业固定信息、联系人、地址、账号等业务数据。公开上传前请确认：

1. `knowledge-base.xlsx` 是否含敏感信息。
2. `classification-table.xlsx` 是否包含真实客户或项目数据。
3. `outputs/`、`sample-run-*`、`__pycache__/`、临时 Word 文件是否已经清理。

建议 `.gitignore` 至少包含：

```gitignore
__pycache__/
*.pyc
outputs/
*.filled.docx
~$*
```

## 适合继续优化的方向

- 增加单元测试和回归测试样例。
- 把字段规则配置化，减少硬编码。
- 增加更多 Word 模板的 benchmark。
- 为知识库增加脱敏示例文件。
- 增加 CI 检查，确保入口脚本和依赖安装正常。


