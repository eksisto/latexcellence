# 表达优化模块 `expression_opt/`

与错字查找共用 Marker 保护策略，聚焦于学术英语表达优化（清晰度、简洁性、学术语气）。

## 自定义提示词管理 `prompt_manager.py`

| 方法 | 功能 |
| --- | --- |
| `list_prompts` / `load_prompt` / `save_prompt` / `delete_prompt` | 提示词 CRUD |
| `set_active` / `get_active_prompt_content` | 激活/获取当前提示词 |

## Pydantic 输出模型

- `ExpressionChangeItem` — 单条优化（type, original, optimized, reason）
- `ExpressionOptResult` — 逐段优化结果（paragraph_index, original, optimized, changes）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `preview_paragraphs` | 提取段落 + 剥离 LaTeX + 缓存 marker_map |
| `optimize` | 分批并发调用 LLM 优化表达 |
| `optimize_stream` | SSE 流式逐段推送优化进度 |
| `apply_changes` | 还原 marker + 原子写回源文件 |

### System Prompt 核心约束

LLM 在 system prompt 中强制约束：marker（`__CMD_0000__`）代表 LaTeX 结构，**绝对禁止修改、删除、拆分或重排**任何 marker。只允许优化纯文本部分的清晰度、简洁性和学术语气。每个 marker 在优化文本中必须与原文完全一致。

## API 路由

Prefix: `/api/expression-opt`, Tag: `expression_opt`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落 |
| POST | `/optimize` | LLM 逐段优化表达 |
| POST | `/apply` | 写回源文件 |
| GET | `/stream/{workspace_id}` | SSE 流式推送 |
| GET | `/prompts` | 列出所有自定义提示词 |
| GET | `/prompts/{name}` | 加载指定提示词 |
| POST | `/prompts` | 创建或更新提示词 |
| DELETE | `/prompts/{name}` | 删除提示词 |
| POST | `/prompts/active` | 设置当前激活的提示词 |

apply 端点使用 `ApplyChangeItem` 共享模型。
