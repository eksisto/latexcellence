# 错字查找模块 `typo_check/`

## 设计思路

将 LaTeX 源码转为纯文本，LLM 在纯文本上做错字检查，修改后的文本精确映射回源文件位置。核心策略：替换-还原（Replace-then-Restore）。

```
原始 LaTeX:      \textbf{这是一个错字} $x^2$ 更多内容
        ↓ strip_to_plain_text()
纯文本:         __CMD_0000__这是一个错字__MATH_0000__ 更多内容
        ↓ LLM 检查修改（不碰任何 marker）
修正文本:       __CMD_0000__这是一各错字__MATH_0000__ 更多内容
        ↓ restore_from_markers()
还原 LaTeX:      \textbf{这是一各错字} $x^2$ 更多内容
```

**解析流水线**：8 步顺序执行（VERB → 逐字类环境 → COM → ENV → MATH → CMD → 空白规范化）。Marker 格式为 `__{PREFIX}_{COUNTER:04d}__`，PREFIX 为 CMD / MATH / ENV / VERB / COM。还原时按 marker 长度降序替换。

### System Prompt 核心约束

LLM 在 system prompt 中强制约束：marker（`__CMD_0000__`）代表 LaTeX 命令，**绝对禁止修改、删除、拆分或重排**任何 marker。只允许修改纯文本部分的错字。每个 marker 在修正文本中必须与原文完全一致。

## Pydantic 输出模型

- `TypoChangeItem` — 单条修改（type, original, corrected, position, explanation）
- `TypoCheckResult` — 逐段检查结果（corrected, changes）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `preview_paragraphs` | 提取段落 + 剥离 LaTeX + 缓存 marker_map |
| `check_typos` | 分批并发调用 LLM 检查每个段落 |
| `check_typos_stream` | SSE 流式逐段推送检查进度 |
| `apply_changes` | 还原 marker + 原子写回源文件 |

**批量处理**：按 `batch_size` 分批，`asyncio.gather` + `Semaphore(max_concurrency)` 并发，批次间等待 `request_interval`。

**三层防护**：

1. `retry_with_validation()` 校验 JSON schema → 反馈重试
2. Marker 匹配验证 → 不一致时回退原文
3. 异常兜底 → 返回原始文本 + 空 changes

## API 路由

Prefix: `/api/typo-check`, Tag: `typo_check`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/preview` | 预览段落（剥离 LaTeX 后的纯文本） |
| POST | `/check` | LLM 逐段检查错字 |
| POST | `/apply` | 将审核通过的修改写回源文件 |
| GET | `/stream/{workspace_id}` | SSE 流式推送检查进度 |

请求模型：`TypoCheckRequest`（file_path, paragraph_indices, resolve_input）、`TypoApplyRequest`（file_path, changes, operation_id）

## 结果展示

后端返回的 `changes[]` 中，`original` 和 `corrected` 字段已由 `_build_success_result` 调用 `_restore_change_markers()` 预先将 marker 还原为原始 LaTeX（如 `\textbf{}`）。前端直接展示还原后的文本，无需额外处理 marker。`corrected` 顶层字段则保留 marker，供 apply 流程使用。

## 人工评估

`tests/fixtures/typo_check/` 是错字查找的人工评估语料（每条一个 LaTeX 段落片段 + `manifest.json`，按难度分层，含干净的对照段落用于误报检测），配套维护者工具位于 `tools/typo_check_eval/`：`run_eval.py` 在语料目录上打开工作区，逐条调用本模块 `TypoCheckService.check_typos()`，并把修正文本中的 marker 还原后生成原文/修正后对照 HTML 报告。该流程依赖真实 LLM 调用与人工判断，不属于 `pytest tests/` 自动测试范围，详见 `tools/typo_check_eval/README.md`。
