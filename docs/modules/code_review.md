# LaTeX 代码审查模块 `code_review/`

## 设计思路

传统 LLM 直接操作 LaTeX 源码做格式化已升级为 Agentic 混合流水线：
- **latexindent.pl**（用户已安装 TeXLive 时）或**内置轻量格式化器**（兜底）处理机械性格式化
- **LLM** 负责智能审查（坏味道、过时用法、最佳实践）

## 架构流程

原始 LaTeX → [latexindent.pl / 内置格式化器]（格式化）→ Marker 回贴 → LLM（审查）→ 审查报告

### 双数据线架构

```
原始 LaTeX（original_text）
    │
    ├─→ [1] 工具格式化线（原始 LaTeX）──→ latexindent / 内置格式化器
    │                                              │
    │                                              ▼
    │                                          formatted（原始 LaTeX）
    │                                              │
    │                                              ▼
    │                              [2] Marker 回贴 ← strip_to_plain_text
    │                                              │
    │                                              ▼
    │                                        formatted_with_markers
    │                                              │
    └─→ [3] LLM 审查线（含 marker） ←───────────────┘
                                                       │
                                                       ▼
                                                CodeReviewResult
```

1. **工具格式化**：用原始 LaTeX（无 marker）调用格式化工具，得到 `formatted`
2. **Marker 回贴**：对 `formatted` 执行 `strip_to_plain_text`，得 `formatted_with_markers`
3. **LLM 审查**：将 `formatted_with_markers` 发给 LLM，LLM 只出建议不动源码

## Pydantic 模型

### CodeReviewSuggestion

```python
class CodeReviewSuggestion(BaseModel):
    type: Literal["code_smell", "modernization", "best_practice", "portability", "consistency"]
    severity: Literal["info", "warning", "error"] = "info"
    description: str
    original: str
    suggested: str
```

### CodeReviewResult

```python
class CodeReviewResult(BaseModel):
    paragraph_index: int
    needs_review: bool = True
    suggestions: list[CodeReviewSuggestion] = []

    @model_validator(mode="after")
    def _check_suggestions_consistency(self):
        # needs_review=true ⟹ suggestions 非空
        # needs_review=false ⟹ suggestions 为空
```

## Service API

| 方法 | 说明 |
| --- | --- |
| `check_code_review(file_path, paragraph_indices, resolve_input, workspace_id)` | 批量代码审查 |
| `check_code_review_stream(file_path, resolve_input, workspace_id)` | SSE 流式代码审查 |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 写回格式化后的原始 LaTeX |
| `_format_source(source)` | 统一格式化入口（latexindent → 内置兜底） |

- `use_marker_protection = True`：LLM 只能看到含 marker 的纯文本，无法篡改 LaTeX 结构
- `_latexindent_available`：类级别惰性缓存，运行时环境检测

## Router API

Prefix: `/api/code-review`, Tag: `code_review`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/preview` | 预览段落（含原始 LaTeX 和纯文本） |
| POST | `/check` | 代码审查（工具格式化 + LLM 审查） |
| POST | `/apply` | 写回格式化结果 |
| GET | `/stream/{workspace_id}` | SSE 流式审查 |

## 内置格式化器

`backend/utils/builtin_formatter.py` — 零外部依赖的轻量格式化器：

- 环境缩进（`\begin{env}` … `\end{env}` 内统一缩进）
- 环境间空行（环境前后统一空行分隔）
- `\begin`/`\end` 对齐（同一缩进层级）
- 去除行尾多余空白

不做 latexindent.pl 的高级功能（如 `&` 列宽对齐、`\` 断行对齐），高级对齐场景交给 LLM 审查指出。

---

## API 路由

前缀 `/api/code-review`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落 |
| POST | `/check` | 工具格式化 + AI 审查 |
| POST | `/apply` | 写回格式化结果 |
| GET | `/stream/{workspace_id}` | SSE 流式推送 |
