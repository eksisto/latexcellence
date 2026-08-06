# 逐段翻译模块 `paragraph_translate/`

按段落逐一将 LaTeX 文档翻译为目标语言。LLM 在剥离 LaTeX 结构后的纯文本上执行翻译，Marker 保护确保 LaTeX 命令和公式不被修改。支持多文件展开（`\input`/`\include`）和 SSE 流式推送。

## 设计思路

与其他逐段检查模块（typo_check / expression_opt 等）共享 `BaseParagraphCheckService` 基类，仅实现 hooks 定制翻译行为。无需额外的后处理逻辑（如术语替换、聚类），流程最简洁。

| 维度 | paragraph_translate | typo_check / expression_opt |
| --- | --- | --- |
| LLM 调用次数 | N（逐段） | N（逐段） |
| 跨段落分析 | 否 | 否 |
| 输出字段 | `translated` | `corrected` / `optimized` |
| 用户操作 | 接受/忽略翻译 | 接受/忽略修改 |

## Pydantic 输出模型

- `TranslateChangeItem` — 单条翻译修改（type, original, translated, explanation）
- `ParagraphTranslateResult` — 翻译输出（paragraph_index, original, translated, changes）

## Prompt 设计

System prompt 包含：

- 学术翻译要求（准确性 > 流畅性 > 简洁性、术语一致、正式学术语气）
- Marker 保护规则（禁止修改/删除/拆分任何 marker）
- 严格 JSON 输出格式（paragraph_index, original, translated, changes）
- 已在目标语言时原样返回

User prompt 将待翻译纯文本和目标语言注入模板。

## Service 流程

```
1. preview_paragraphs() → 段落提取 + LaTeX 剥离 + marker_map 缓存
2. translate() / translate_stream() → 逐段调用 LLM 翻译
   - 每段：marker 保护检查 → LLM 调用 → Pydantic 校验 → marker 完整性验证
   - 空白段落直接返回原文
3. apply_changes() → marker 还原 + 原子写回源文件
```

| 方法 | 功能 |
| --- | --- |
| `preview_paragraphs` | 提取段落 + 剥离 LaTeX + 缓存 marker_map |
| `translate` | 非流式批量翻译 |
| `translate_stream` | SSE 流式推送翻译进度和结果 |
| `apply_changes` | 还原 marker + 原子写回 |

### 三层防护（与其他逐段检查模块一致）

| 层级 | 防护内容 | 失败处理 |
| --- | --- | --- |
| 第 1 层 | `retry_with_validation()`（最多 3 次） | LLM 输出不符 schema 时反馈重试 |
| 第 2 层 | Marker 匹配验证 | 不一致时回退到原文 |
| 第 3 层 | 异常兜底 | LLM 调用异常时返回原文 + 空 changes |

## API 路由

Prefix: `/api/paragraph-translate`, Tag: `paragraph_translate`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落（含 LaTeX 剥离后的纯文本） |
| POST | `/translate` | 批量翻译（非流式） |
| POST | `/apply` | 将翻译写回源文件 |
| GET | `/stream/{workspace_id}` | SSE 流式推送翻译进度和结果 |

请求模型：`ParagraphTranslateRequest`、`ParagraphTranslateApplyRequest`

### SSE 事件格式

```
{ "type": "progress", "current": 3, "total": 12, "file_path": "..." }
{ "type": "result", "paragraph_index": 0, "data": {...} }
{ "type": "error", "message": "...", "retrying": true }
{ "type": "done", "operation_id": "..." }
```

## 前端

页面 `ParagraphTranslatePage.jsx` 复用 `useParagraphCheck` hook，与其他逐段检查页面共享相同的交互模式：文件选择 → 段落预览 → 翻译 → 审核结果 → 逐项/批量应用。

额外功能：

- 目标语言选择（支持多种语言）
- 翻译结果中原文与译文对照展示
- 多文件模式下按文件 Tab 分组显示

配置同步：`config.json` 的 `features.paragraph_translate`（`enabled` + `batch_size`）。
