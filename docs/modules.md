# LateXcellence — 功能模块文档

本目录包含各个功能模块的详细文档。

## 模块列表

### LLM 文本处理模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 错字查找 | [typo_check.md](modules/typo_check.md) | LaTeX 源码的错字检查与修正 |
| 表达优化 | [expression_opt.md](modules/expression_opt.md) | 学术语言表达润色 |
| 术语一致性检查 | [terminology_check.md](modules/terminology_check.md) | 术语一致性检查 |
| LaTeX 代码审查 | [code_review.md](modules/code_review.md) | 工具格式化 + LLM 智能审查 |
| LaTeX 样式修改 | [style_modify.md](modules/style_modify.md) | 自然语言描述修改样式 |
| 写作助手 | [writing_chat.md](modules/writing_chat.md) | 多轮对话式写作辅助 |

### OCR 识别模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 公式识别 | [formula_ocr.md](modules/formula_ocr.md) | 图片转 LaTeX 公式 |
| 表格识别 | [table_ocr.md](modules/table_ocr.md) | 图片表格转 LaTeX |

### 文档生成模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 模板生成 | [template_gen.md](modules/template_gen.md) | 描述生成 LaTeX 模板 |
| Markdown 转 LaTeX | [markdown_converter.md](modules/markdown_converter.md) | Markdown 转 LaTeX |

### 项目管理模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 自动编译 | [compile.md](modules/compile.md) | LaTeX 编译与错误翻译 |
| Git 差异高亮 | [git_diff.md](modules/git_diff.md) | 版本差异可视化高亮 PDF |
| 参考文献管理 | [bib_manager.md](modules/bib_manager.md) | BibTeX 格式识别与管理 |

### 工具模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 联网搜索 | [web_search.md](modules/web_search.md) | 多引擎搜索 + Function Calling |

---

## 架构概览

### BaseParagraphCheckService 基类

四个逐段检查模块（typo_check / expression_opt / code_review / terminology_check）共享 `BaseParagraphCheckService` 基类和 `ApplyChangeItem` 共享模型。子类通过 6 个 hooks 定制行为。

### CheckOperationContext

每次 preview/check 生成唯一 `operation_id`，关联的上下文（marker_maps、file_md5s、TTL 1 小时）保存在服务实例中。apply 时通过 operation_id 取回上下文，避免并发或重启后 marker 映射丢失。

### Marker 保护策略

- **使用 Marker 保护**：typo_check, expression_opt, terminology_check, code_review
- **不使用**：formula_ocr, table_ocr, compile, markdown_converter, style_modify, git_diff, template_gen

### LLM 调用模式

| 模式 | 模块 |
| --- | --- |
| 逐段检查 + SSE 流式 | typo_check, expression_opt, terminology_check, code_review |
| 多轮对话 | writing_chat |
| 单次请求-响应 | formula_ocr, table_ocr, template_gen, style_modify, compile, git_diff, markdown_converter |
| 无 LLM | compile（编译本身） |

### 外部依赖

| 依赖 | 模块 |
| --- | --- |
| latexmk + xelatex | compile, git_diff |
| pandoc | markdown_converter |
| git | git_diff |
| OpenAI 兼容 API | 大部分 LLM 模块 |
