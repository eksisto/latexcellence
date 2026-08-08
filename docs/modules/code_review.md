# LaTeX 代码审查模块 `code_review/`

## 设计思路

Agentic 混合流水线：latexindent.pl（或内置轻量格式化器）处理机械性格式化，LLM 负责智能审查（坏味道、过时用法、最佳实践）。

**单数据线架构（无 marker 保护）**：

```
原始 LaTeX ──→ 工具格式化（latexindent / 内置格式化器）──→ formatted
                                                              │
                                      LLM 审查线（真实 LaTeX）←┘
                                                              │
                                                              ↓
                                                       CodeReviewResult
```

1. **工具格式化**：用原始 LaTeX（无 marker）调用 latexindent.pl 或内置兜底格式化器。
   两条路径统一为每层 4 个空格缩进：latexindent 通过项目内置
   `backend/utils/latexindent.yaml`（`defaultIndent`）覆盖其默认的制表符缩进；
   内置格式化器同样按每层 4 个空格缩进
2. **LLM 审查**：将格式化后的真实 LaTeX 直接发给 LLM，LLM 只出建议不动源码（坏味道、过时用法、最佳实践）

**不使用 marker 保护的原因**：code_review 的 LLM 输出（建议）
只读展示、从不写回文件，`new_text` 完全由工具生成；marker 保护反而把真实结构藏起来，
导致 LLM 只能依据周围可见文本臆测、产生无根据的建议（如凭空断言存在换行命令）。
去掉 marker 后 LLM 可以直接审查真实结构，建议的 `original` 能与源码锚定
（前端 anchors 在 `formattedText` 中定位：优先精确匹配，重复片段按出现顺序
逐个消费，精确失败时折叠空白宽松匹配兜底，结果按 offset 升序渲染）。
其余逐段检查模块（typo_check / expression_opt / terminology_check /
paragraph_translate）的输出会写回文件，仍保留 marker 保护。

## Pydantic 输出模型

- `CodeReviewSuggestion` — 审查建议（type, severity, description, original, suggested；original/suggested 为 `LatexText`，经内容校验拦截 JSON 转义损坏）
- `CodeReviewResult` — 逐段审查结果（paragraph_index, needs_review, suggestions）

`CodeReviewSuggestion` 通过 `_reject_markers` 校验器兜底：输入已无 marker，若
`description` / `original` / `suggested` 仍出现 `__CMD_0000__` 等占位符样式 token，
说明 LLM 臆造/回显占位符，校验失败并进入 `retry_with_validation` 重试（错误反馈要求
精确引用源码片段；源码无真实问题时返回 `needs_review: false`），重试耗尽后按常规回退。
该防线保证占位符永不泄漏到前端展示（徽章污染/锚点失效/"采纳"静默无效）。

## 提示词约束（反幻觉 + 不可信数据隔离）

system prompt 明确要求：只报告源码中实际存在的问题，禁止从上下文臆造命令或结构；

每条建议的 `original` 必须精确引用源码片段，`suggested` 给出具体 LaTeX 替换而非散文。

`original` 还需携带足够上下文（整行或所在命令）使其在段落内只出现一次，避免多处匹配导致锚点/采纳错位；`suggested` 保留同样的上下文、仅修改问题部分，保证"替换 original"恰好落在目标位置。前端 anchors/采纳在 `formattedText` 中定位，仍以重复片段按序消费、折叠空白宽松匹配作为兜底。

用户提示词将 LATEX SOURCE 包裹在 `<latex_source>` 分隔符内，并声明文档内容是不可信数据而非指令，禁止执行内容中的任何指令文本（提示词注入防御）。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `check_code_review` | 批量代码审查（工具格式化 + LLM 审查） |
| `check_code_review_stream` | SSE 流式审查 |
| `apply_changes` | 写回格式化后的原始 LaTeX |
| `_format_source` | 统一格式化入口（委托共享工具 `latex_formatter.format_latex_source`：latexindent → 内置兜底） |

- `use_marker_protection = False`：LLM 直接审查格式化后的真实 LaTeX，建议只读展示、不写回（见上文"不使用 marker 保护的原因"）

## 内置格式化器 `backend/utils/builtin_formatter.py`

零外部依赖的轻量格式化器：环境缩进（每层 4 个空格，与 latexindent 路径保持一致）、环境间空行、`\begin`/`\end` 对齐、去除行尾多余空白。不做 latexindent 的高级对齐功能。

## API 路由

Prefix: `/api/code-review`, Tag: `code_review`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落（含原始 LaTeX 和纯文本） |
| POST | `/check` | 工具格式化 + AI 审查 |
| POST | `/apply` | 写回格式化结果 |
| GET | `/stream/{workspace_id}` | SSE 流式推送 |
