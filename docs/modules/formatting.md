# 源码格式化模块 `formatting/`

## 设计差异（与 typo_check / expression_opt 的关键区别）

源码格式化针对 LaTeX **源码结构本身**进行格式优化（间距、环境对齐、缩进），
因此**不使用 Marker 保护策略**——LLM 看到的是原始 LaTeX 源码，可以调整
命令周围的空白、行布局、环境格式，但不得改动文档内容、文本含义或数学公式。

| 维度 | typo_check / expression_opt | formatting |
|---|---|---|
| LLM 输入 | 剥离后的纯文本（含 marker） | 原始 LaTeX 源码 |
| Marker 保护 | 是（prompt 约束 + 还原校验） | 否（LLM 直接修改 LaTeX 结构） |
| apply 还原 | `restore_from_markers()` 还原 | 直接写回 corrected 字段 |
| 修改对象 | 纯文本字词 | 间距 / 缩进 / 环境对齐 |

## `service.py` — 排版业务逻辑

### Pydantic 输出模型

```python
class FormattingChangeItem(BaseModel):
    type: str = "spacing"            # spacing / environment_alignment / code_formatting / consistency
    description: str = ""            # 修改说明
    original: str                    # 原始 LaTeX 片段
    corrected: str                   # 排版后 LaTeX 片段

class FormattingResult(BaseModel):
    paragraph_index: int
    original: str                    # 原始 LaTeX 源码
    corrected: str                   # 排版后 LaTeX 源码
    changes: list[FormattingChangeItem] = []
```

### 核心方法

| 方法 | 功能 |
|---|---|
| `preview_paragraphs(file_path, resolve_input, workspace_id)` | 提取段落，返回原始 LaTeX（`text`）+ 剥离纯文本（`plain_text`，供上下文展示） |
| `check_formatting(file_path, paragraph_indices, resolve_input, workspace_id)` | 异步：分批并发调用 LLM 对原始 LaTeX 进行格式化优化 |
| `check_formatting_stream(file_path, resolve_input, workspace_id)` | 异步生成器：逐段 yield `progress` / `result` / `error` / `done`（SSE） |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 同步：直接写回 corrected 字段（**无需还原 marker**） |
| `_check_one_paragraph(llm, slot, idx, text)` | 异步：调用 `retry_with_validation()` 进行 LLM 调用 + Pydantic 校验 |

### 两层防护

| 层级 | 防护内容 | 失败处理 |
|---|---|---|
| 第1层 | `retry_with_validation()`（最多3次） | LLM输出不符JSON规范或schema时反馈重试 |
| 第2层 | 空输出兜底 | `corrected`为空时回退到原始文本 |

### 系统提示词

```
CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:
1. DO NOT change the document content, meaning, or mathematics.
2. DO NOT add, remove, or rewrite any natural language text.
3. Only adjust formatting: spacing, indentation, line breaks, environment consistency.
4. If no formatting improvement is needed, corrected must be byte-identical to original.
```

## `router.py` — API 路由

Prefix: `/api/formatting`, Tag: `formatting`

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/preview` | 预览段落（原始 LaTeX + 剥离纯文本上下文） |
| `POST` | `/check` | LLM 逐段排版优化（返回 JSON） |
| `POST` | `/apply` | 将审核通过的排版修改直接写回源文件 |
| `GET` | `/stream/{workspace_id}` | SSE 流式推送排版进度 |

**请求模型**：apply 端点使用 `ApplyChangeItem`（定义于 `base_paragraph_check.py`）。apply 请求包含 `operation_id`（可选）字段，用于查找检查时的 marker 映射。
