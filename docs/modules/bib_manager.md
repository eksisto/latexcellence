# 参考文献管理模块 `bib_manager/`

## 设计概述

帮助用户自动识别参考文献格式、提取结构化元数据并生成规范的 BibTeX 代码，支持写入项目中的 `.bib` 文件。格式配置可保存为 JSON 文件复用。

### 核心流程

1. **格式识别**：用户提供参考文献示例 → LLM 分析格式类型、条目类型、字段列表 + 推荐 LaTeX 样式
2. **元数据提取**：用户输入文本或上传截图 → LLM 根据格式字段列表动态提取
3. **BibTeX 生成**：将元数据转换为标准 BibTeX 代码
4. **应用到文件**：选择 `.bib` 文件，追加或更新条目
5. **生成 LaTeX 代码**：生成包含正确引用的 LaTeX 代码片段

## Pydantic 模型

- `ReferenceFormatResult`（format_name, confidence, entry_type, required_fields, optional_fields, field_descriptions, latex_backend, latex_package 等）
- `ExtractedMetadata`（fields: dict — 动态字段，非写死）
- `BibEntry`（cite_key, entry_type, fields, raw_bibtex）
- `LatexCodeResult`（preamble, bibliographystyle, bibliography, cite_command, full_code）
- 各类请求体：`FormatAnalysisRequest`、`ExtractRequest`、`GenerateBibtexRequest`、`SaveBibRequest`

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `analyze_format(example)` | LLM 识别参考文献格式 → ReferenceFormatResult |
| `extract_from_text(text, format_info)` | LLM 从文本提取元数据 |
| `extract_from_image(image_data, content_type, format_info)` | LLM Vision 从截图提取元数据 |
| `generate_bibtex(metadata, format_info, existing_keys)` | LLM 生成 BibTeX → BibEntry |
| `generate_latex_code(metadata, format_info, bib_file_path)` | 生成 LaTeX 代码片段 |
| `read_bib_file(file_path)` | 读取 .bib 文件 |
| `save_bib_entry(file_path, bibtex_code, cite_key)` | 写入 .bib（已存在则替换） |
| `list_bib_files` | 列出项目中所有 .bib 文件 |
| `list_formats` / `load_format` / `save_format` / `delete_format` | 格式配置 CRUD |

## 样式注册表 `style_registry.py`

内置常见学术格式到 LaTeX 样式的映射（如 GB/T 7714-2015 → gbt7714、APA 7th → biblatex-apa、IEEE → IEEEtran 等）。`analyze_format()` 在 LLM 返回后自动补全缺失的 `latex_bibliographystyle`。

## API 路由

Prefix: `/api/bib-manager`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/analyze-format` | 分析参考文献格式 |
| POST | `/extract` | 提取元数据（FormData） |
| POST | `/generate` | 生成 BibTeX |
| POST | `/generate-latex` | 生成 LaTeX 代码 |
| GET | `/latex-styles` | 列出内置 LaTeX 样式映射 |
| POST | `/read-bib` | 读取 .bib 文件 |
| POST | `/save` | 写入 .bib 文件 |
| GET | `/list-bib-files` | 列出 .bib 文件 |
| GET/POST/DELETE | `/formats[/{name}]` | 格式配置 CRUD |
