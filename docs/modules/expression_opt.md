# 表达优化模块 `expression_opt/`

与错字查找共用相同的 Marker 保护策略（LaTeX 结构替换为 marker，LLM 仅修改纯文本），
但聚焦于学术英语表达优化（清晰度、简洁性、学术语气）。

## `prompt_manager.py` — 自定义提示词管理

| 方法 | 功能 |
|---|---|
| `list_prompts()` | 列出所有自定义提示词（`.txt` 文件） |
| `load_prompt(name)` | 加载指定提示词内容 |
| `save_prompt(name, content)` | 保存/覆盖提示词 |
| `delete_prompt(name)` | 删除提示词 |
| `set_active(name)` | 设置当前激活的提示词（写入 `_active.txt`） |
| `get_active_prompt_content()` | 获取激活提示词的内容 |

## Pydantic 输出模型

```python
class ExpressionChangeItem(BaseModel):
    type: str = "rewrite"
    original: str
    optimized: str
    reason: str = ""

class ExpressionOptResult(BaseModel):
    paragraph_index: int
    original: str
    optimized: str
    changes: list[ExpressionChangeItem] = []
```

## 核心方法

| 方法 | 功能 |
|---|---|
| `preview_paragraphs(file_path, resolve_input, workspace_id)` | 提取段落 + 剥离 LaTeX + 缓存 marker_map |
| `optimize(file_path, paragraph_indices, resolve_input, workspace_id)` | 异步：分批并发调用 LLM 优化表达，返回所有结果 |
| `optimize_stream(file_path, resolve_input, workspace_id)` | 异步生成器：逐段 yield SSE 事件 |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 还原 marker + 原子写回源文件 |
| `_build_system_prompt()` | 合并基础 prompt + 激活的自定义 prompt |

## System Prompt 核心约束

```
CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:
1. Markers like __CMD_0001__ represent LaTeX commands. They are NOT text.
2. ABSOLUTELY DO NOT modify, delete, split, merge, or reorder any marker.
3. Every marker must appear in corrected text EXACTLY as in original.
4. Only optimize plain text for clarity, conciseness, and academic tone.
```

## API 路由

Prefix: `/api/expression-opt`, Tag: `expression_opt`

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/preview` | 预览段落（剥离 LaTeX 后的纯文本） |
| `POST` | `/optimize` | LLM 逐段优化表达（返回 JSON） |
| `POST` | `/apply` | 将审核通过的修改还原 marker 并写回源文件 |
| `GET` | `/stream/{workspace_id}` | SSE 流式推送优化进度 |
| `GET` | `/prompts` | 列出所有自定义提示词 |
| `GET` | `/prompts/{name}` | 加载指定提示词内容 |
| `POST` | `/prompts` | 创建或更新提示词 |
| `DELETE` | `/prompts/{name}` | 删除提示词 |
| `POST` | `/prompts/active` | 设置当前激活的提示词 |

**请求模型**：apply 端点使用 `ApplyChangeItem`（定义于 `base_paragraph_check.py`）。apply 请求包含 `operation_id`（可选）字段，用于查找检查时的 marker 映射。
