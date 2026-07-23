# LateXcellence — 功能模块文档

本目录包含 LateXcellence 各功能模块的详细文档。

## 模块列表

### LLM 文本处理模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 错字查找 | [typo_check.md](modules/typo_check.md) | LaTeX 源码错字检查与修正 |
| 表达优化 | [expression_opt.md](modules/expression_opt.md) | 学术英语表达优化（清晰度、简洁性、学术语气） |
| 术语一致性检查 | [terminology_check.md](modules/terminology_check.md) | 术语一致性检查与统一建议 |
| 源码格式化 | [formatting.md](modules/formatting.md) | LaTeX 源码结构格式优化（间距、对齐、缩进） |
| LaTeX 样式修改 | [style_modify.md](modules/style_modify.md) | 基于自然语言描述修改 LaTeX 样式 |
| 写作助手聊天 | [writing_chat.md](modules/writing_chat.md) | 多轮对话式写作辅助与段落编辑 |

### OCR 识别模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 公式识别 | [formula_ocr.md](modules/formula_ocr.md) | 图片 LaTeX 公式识别 |
| 表格识别 | [table_ocr.md](modules/table_ocr.md) | 图片表格识别与 LaTeX 代码生成 |

### 文档生成模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 模板生成 | [template_gen.md](modules/template_gen.md) | 根据描述生成 LaTeX 文档模板 |
| Markdown 转 LaTeX | [markdown_converter.md](modules/markdown_converter.md) | Markdown 文本转 LaTeX 代码 |

### 项目管理模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 自动编译 | [compile.md](modules/compile.md) | LaTeX 项目编译与错误翻译 |
| Git 差异高亮 | [git_diff.md](modules/git_diff.md) | Git 版本差异可视化高亮 PDF（含编译错误 LLM 翻译） |
| 参考文献管理 | [bib_manager.md](modules/bib_manager.md) | BibTeX 参考文献格式识别与管理 |

### 工具模块

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| 联网搜索 | [web_search.md](modules/web_search.md) | 多引擎联网搜索 + Function Calling 工具集成 |

---

## 架构概览

### BaseParagraphCheckService 基类

四个逐段检查模块共享 `backend/core/base_paragraph_check.py` 中的 `BaseParagraphCheckService` 基类和 `ApplyChangeItem` 共享请求模型，封装：
- 文件解析与段落提取（含 `\input` 展开）
- LaTeX marker 替换与还原
- LLM 逐段调用编排（批量 + 流式）
- 结果写回源文件

`ApplyChangeItem`（`paragraph_index`, `new_text`, `file_path`）是四个模块 router 层 apply 接口的统一修改项模型，避免各模块重复定义。

子类通过 6 个 hooks 定制行为：`_get_system_prompt`、`_build_user_content`、`_get_model_cls`、`_get_llm_params`、`_get_batch_size`、`_build_success_result`。

类变量控制差异：
- `_output_field`：结果字段名（`"corrected"` 或 `"optimized"`）
- `use_marker_protection`：是否启用 marker 保护（formatting 为 `False`）

### CheckOperationContext 操作上下文

每次 preview/check 操作生成唯一 `operation_id`，关联的上下文（`CheckOperationContext`）保存在服务实例的 `_operation_contexts` 字典中：

| 字段 | 含义 |
|---|---|
| `operation_id` | 12 位 hex 唯一标识 |
| `marker_maps` | 深拷贝的 marker 映射（`file_path → para_index → {marker: original}`） |
| `file_path` | 主文件路径 |
| `file_md5s` | 检查时各文件的 MD5 快照 |
| `created_at` | 创建时间戳（TTL 清理依据，默认 1 小时） |

**流程**：
1. Preview/Check 时：生成 `operation_id` → 深拷贝 `_marker_maps` → 存入 `_operation_contexts`
2. Apply 时：前端回传 `operation_id` → 查找对应上下文 → 使用上下文中的 `marker_maps` 还原 marker → 写回文件
3. 上下文过期后自动清理（TTL 1 小时）

此机制将请求相关状态从服务单例字段（`_marker_maps`）中解耦，避免并发请求或重启后 marker 映射丢失导致字面 marker 写入 `.tex` 文件。即使 context 丢失，apply_changes 也会逐段检测 `marker_map`，缺失时跳过写入并返回 `errors`，前端据此保留未应用项并提示重新检查。

### Marker 保护策略

与 LLM 交互时，LaTeX 结构必须替换为 marker（`__CMD_0000__` / `__MATH_0000__` 等）。
- **使用 Marker 保护的模块**：typo_check, expression_opt, terminology_check, writing_chat (编辑操作)
- **不使用 Marker 保护的模块**：formatting (直接操作 LaTeX 源码), formula_ocr, table_ocr, compile, markdown_converter, style_modify, git_diff, template_gen

### LLM 调用模式

| 模式 | 模块 |
| --- | --- |
| 逐段检查 + SSE 流式 | typo_check, expression_opt, terminology_check, formatting |
| 多轮对话 | writing_chat |
| 单次请求-响应 | formula_ocr, table_ocr, template_gen, style_modify, compile (错误翻译), git_diff (错误翻译), markdown_converter (宏包分析) |
| 无 LLM | compile (编译本身) |

### 外部依赖

| 依赖 | 模块 | 用途 |
| --- | --- | --- |
| `latexmk` + `xelatex` | compile, git_diff | LaTeX 项目编译 |
| `pandoc` | markdown_converter | Markdown 转 LaTeX |
| `git` | git_diff | 版本控制操作 |
| OpenAI 兼容 API | 大部分 LLM 模块 | 文本处理与理解 |
