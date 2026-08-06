# 文档格式转换模块 `format_converter/`

通过 `subprocess` 调用 Pandoc 实现 Markdown/DOCX/HTML 与 LaTeX 的互转，支持文本输入和文件上传两种模式。目标格式为 LaTeX 时，通过确定性宏包规则表自动分析所需宏包（LLM 仅兜底）。

| 维度 | 格式转换 | 其他模块 |
| --- | --- | --- |
| 外部依赖 | pandoc + 宏包规则表（LLM 兜底） | latexmk / LLM |
| 是否修改源文件 | 否（返回结果，手动复制/下载） | 是/否 |

## 支持的转换方向

| 源格式 | 可转目标格式 |
| --- | --- |
| Markdown | LaTeX, HTML, DOCX |
| HTML | LaTeX, Markdown, DOCX |
| DOCX | LaTeX, Markdown, HTML |
| LaTeX | Markdown, HTML, DOCX |

> 注意：LaTeX → 其他格式的逆向转换会丢失复杂 LaTeX 命令，由 Pandoc 尽力处理。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `convert_text(text, source_format, target_format)` | 文本输入格式转换 |
| `convert_file(file_bytes, filename, source_format, target_format)` | 文件上传格式转换 |
| `convert(markdown_text)` | 向后兼容接口（Markdown → LaTeX） |
| `_check_pandoc` | 检查 pandoc 是否可用 |
| `_validate_formats(source, target)` | 校验格式参数有效性 |
| `_validate_file(filename, source_format)` | 校验文件扩展名与格式匹配 |
| `_run_pandoc_text(text, source, target)` | 通过 stdin 调用 Pandoc |
| `_run_pandoc_file(file_bytes, source, target)` | 通过临时文件调用 Pandoc（DOCX 等二进制格式） |
| `_derive_output_filename(original, target)` | 推导输出文件名 |

**降级策略**：无 API Key 或 LLM 兜底分析失败时，直接返回 Pandoc 转换结果。

## API 路由

Prefix: `/api/format-converter`, Tag: `format_converter`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/convert` | Markdown 文本转 LaTeX（向后兼容） |
| POST | `/convert-text` | 文本格式转换（任意方向） |
| POST | `/convert-file` | 文件上传格式转换 |

### 请求/响应格式

**`POST /convert`**（向后兼容）

```json
{ "markdown": "# Hello" }
→ { "latex": "\\section{Hello}" }
```

**`POST /convert-text`**

```json
{ "text": "<b>bold</b>", "source_format": "html", "target_format": "latex" }
→ { "result": "\\textbf{bold}" }
```

**`POST /convert-file`**（multipart/form-data）

- Body: `file` 字段
- Query: `source_format`, `target_format`

```json
→ { "result": "...", "output_filename": "document.tex" }
```

## 配置项

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用 |
| `allowed_extensions` | `[".md", ".markdown", ".html", ".htm", ".docx"]` | 允许上传的文件扩展名 |
| `max_file_size_mb` | `20` | 上传文件最大尺寸（MB） |
