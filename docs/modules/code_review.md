# LaTeX 代码审查模块 `code_review/`

## 设计思路

Agentic 混合流水线：latexindent.pl（或内置轻量格式化器）处理机械性格式化，LLM 负责智能审查（坏味道、过时用法、最佳实践）。

**双数据线架构**：

```
原始 LaTeX
    │
    ├─→ [1] 工具格式化线（原始 LaTeX）──→ latexindent / 内置格式化器
    │                                          │
    │                                          ↓
    │                                       formatted
    │                                          │
    │                       [2] Marker 回贴 ←┘ strip_to_plain_text
    │                                          │
    │                                       formatted_with_markers
    │                                          │
    └─→ [3] LLM 审查线（带 marker）←───────────┘
                │
                ↓
         CodeReviewResult
```

1. **工具格式化**：用原始 LaTeX（无 marker）调用 latexindent.pl 或内置兜底格式化器
2. **Marker 回贴**：对格式化后的纯净 LaTeX 执行 `strip_to_plain_text`，保护结构不被 LLM 篡改
3. **LLM 审查**：将带 marker 的纯文本发给 LLM，LLM 只出建议不动源码（坏味道、过时用法、最佳实践）

## Pydantic 输出模型

- `CodeReviewSuggestion` — 审查建议（type, severity, description, original, suggested；original/suggested 为 `LatexText`，经内容校验拦截 JSON 转义损坏）
- `CodeReviewResult` — 逐段审查结果（paragraph_index, needs_review, suggestions）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `check_code_review` | 批量代码审查（工具格式化 + LLM 审查） |
| `check_code_review_stream` | SSE 流式审查 |
| `apply_changes` | 写回格式化后的原始 LaTeX |
| `_format_source` | 统一格式化入口（latexindent → 内置兜底） |

- `use_marker_protection = True`：LLM 只能看到含 marker 的纯文本，无法篡改 LaTeX 结构

## 内置格式化器 `backend/utils/builtin_formatter.py`

零外部依赖的轻量格式化器：环境缩进、环境间空行、`\begin`/`\end` 对齐、去除行尾多余空白。不做 latexindent 的高级对齐功能。

## API 路由

Prefix: `/api/code-review`, Tag: `code_review`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落（含原始 LaTeX 和纯文本） |
| POST | `/check` | 工具格式化 + AI 审查 |
| POST | `/apply` | 写回格式化结果 |
| GET | `/stream/{workspace_id}` | SSE 流式推送 |
