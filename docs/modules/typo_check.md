# 错字查找模块 `typo_check/`

## `latex_parser.py` — LaTeX 文本提取与 Marker 保护

这是整个项目的核心技术难点。将 LaTeX 源码转为纯文本，同时保证修改后的纯文本能精确映射回源文件位置。

**核心策略：替换-还原（Replace-then-Restore）**

```
原始 LaTeX:      \textbf{这是一个错字} $x^2$ 更多内容
        ↓ strip_to_plain_text()
纯文本:         __CMD_0001__这是一个错字 __MATH_0001__ 更多内容
        ↓ LLM 检查修改（不碰任何 marker）
修正后:         __CMD_0001__这是一各错字 __MATH_0001__ 更多内容
        ↓ restore_from_markers()
还原 LaTeX:      \textbf{这是一各错字} $x^2$ 更多内容
```

**解析流水线**（7 步顺序执行）：

| 步骤 | 方法 | 操作 | 示例 |
|---|---|---|---|
| 1 | `_replace_verb_inline()` | 内联逐字替换 | `\verb\|c\|` → `__VERB_0001__` |
| 2 | `_replace_protected_envs()` | 受保护环境整体替换 | `\begin{equation}...` → `\n__ENV_0001__\n` |
| 3 | `_replace_display_math()` | 展示公式替换 | `$$x^2$$` → `\n__MATH_0001__\n` |
| 4 | `_replace_inline_math()` | 行内公式替换 | `$x^2$` → `__MATH_0002__` |
| 5 | `_replace_commands()` | LaTeX 命令替换（字符扫描+深度计数） | `\textbf{hello}` → `__CMD_0001__` |
| 6 | `_replace_comments()` | 注释替换为 marker（保留 `\%` 转义） | `Hello % comment` → `Hello __COM_0001__` |
| 7 | `_normalize_whitespace()` | 空白规范化 | 连续 3+ 空行压缩为 2 空行 |

**命令解析（第 5 步）核心技术**：
- **花括号深度计数**：正确处理 `\textbf{\textit{nested}}`
- **`[]` / `{}` 交替扫描**：支持 `\command{arg1}[opt]{arg2}`
- **转义字符识别**：`\&` `\%` `\_` `\#` `\$` `\{` `\}` 作为单 token 替换
- **单 token 命令**：`\,` `\:` `\;` `\!` `\-` `\\` `\ ` `\~` `\^`
- **无参数命令**：`\LaTeX` `\today` `\hline` `\centering` 等（跳过后续 `{arg}` 扫描）

**受保护环境列表**（整体替换为单个 marker，LLM 不可见）：
`verbatim`, `lstlisting`, `table`, `table*`, `tabular`, `tabular*`, `longtable`, `tikzpicture`, `equation`, `equation*`, `align`, `align*`, `alignat`, `alignat*`, `eqnarray`, `eqnarray*`, `gather`, `gather*`, `multline`, `multline*`, `flalign`, `flalign*`, `subequations`

**Marker 格式**：`__{PREFIX}_{COUNTER:04d}__`
- PREFIX：`CMD`（命令）、`MATH`（公式）、`ENV`（环境）、`VERB`（逐字）
- 全局自增计数器，每次 `strip_to_plain_text()` 重置为 0

**核心接口**：

| 方法 | 功能 |
|-|-|
| `strip_to_plain_text(source) -> (str, dict)` | 返回 `(带 marker 的纯文本, marker→原始内容映射表)` |
| `restore_from_markers(processed, marker_map)` | 按 marker 长度降序还原，防止短 marker 误匹配长 marker 前缀 |

**还原策略（关键）**：
```python
# 按 marker 长度降序排列
for marker, original in sorted(
    marker_map.items(), key=lambda x: (-len(x[0]), x[0])
):
    result = result.replace(marker, original)
```
这确保 `__CMD_0010__` 不会被 `__CMD_001__` 的前缀误匹配。

## `service.py` — 错字查找业务逻辑

编排完整的错字检查流程：段落提取 → LaTeX 剥离 → LLM 调用 → JSON 校验 → Marker 验证 → 写回。

**Pydantic 输出模型**：

```python
class TypoChangeItem(BaseModel):
    type: str = "typo"            # 修改类型
    original: str                 # 错误原文片段
    corrected: str                # 修正后片段
    position: Optional[int] = None # 字符偏移量（可选）
    explanation: str = ""         # 错误说明

class TypoCheckResult(BaseModel):
    paragraph_index: int          # 段落序号
    original: str                 # 原始纯文本（含 marker）
    corrected: str                # 修正后纯文本（含 marker）
    changes: list[TypoChangeItem] = [] # 修改列表
```

**核心方法**：

| 方法 | 功能 |
|-|-|
| `preview_paragraphs(file_path, resolve_input, workspace_id)` | 同步：提取段落 + 剥离 LaTeX + 缓存 marker_map，返回预览数据 |
| `check_typos(file_path, paragraph_indices, resolve_input, workspace_id)` | 异步：分批并发调用 LLM 检查每个段落，返回所有结果 |
| `check_typos_stream(file_path, resolve_input, workspace_id)` | 异步生成器：逐段 yield `progress` / `result` / `error` / `done` 事件（SSE） |
| `apply_changes(file_path, changes, workspace_id, operation_id)` | 同步：还原 marker + 原子写回源文件；marker_map 缺失时跳过该段并返回 `errors` |
| `_check_one_paragraph(llm, slot, idx, text)` | 异步：调用 `retry_with_validation()` 进行 LLM 调用 + Pydantic 校验 + marker 验证 |

**批量处理**：
- 按 `config.features.typo_check.batch_size`（默认 5）分批
- 每批内通过 `asyncio.gather` + `Semaphore(max_concurrency)` 并发调用 LLM，最大并发数由 `config.llm.max_concurrency`（默认 5）控制
- 批次之间等待 `request_interval` 秒以避免 rate limit

**三层防护**：

| 层级 | 防护内容 | 失败处理 |
|------|---------|---------|
| 第 1 层 | `retry_with_validation()`（最多 3 次） | LLM 输出不符 JSON 规范或 Pydantic schema 时，将错误反馈给 LLM 修正 |
| 第 2 层 | Marker 匹配验证 | 检查 `corrected` 中的 marker 是否与 `original` 完全一致（无缺失、无新增），不一致时回退到原始文本 |
| 第 3 层 | 异常兜底 | LLM 调用抛出异常时，返回原始文本 + 空 changes |

**System Prompt 核心约束**：
```
CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:
1. Markers like __CMD_0001__ represent LaTeX commands. They are NOT text.
2. ABSOLUTELY DO NOT modify, delete, split, merge, or reorder any marker.
3. Every marker must appear in corrected text EXACTLY as in original.
4. Only modify plain text portions.
```

## `router.py` — API 路由

Prefix: `/api/typo-check`, Tag: `typo_check`

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/preview` | 预览段落（剥离 LaTeX 后的纯文本） |
| `POST` | `/check` | LLM 逐段检查错字（返回 JSON） |
| `POST` | `/apply` | 将审核通过的修改还原 marker 并写回源文件 |
| `GET` | `/stream/{workspace_id}` | SSE 流式推送检查进度 |

**请求模型**：

```python
class TypoCheckRequest(BaseModel):
    file_path: str
    paragraph_indices: Optional[list[int]] = None  # None = 全部
    resolve_input: bool = False  # 是否递归展开 \\input 引用

class TypoApplyRequest(BaseModel):
    file_path: str
    changes: list[ApplyChangeItem]  # ApplyChangeItem 定义于 base_paragraph_check.py
    operation_id: Optional[str] = None  # 检查操作 ID，用于查找 marker 映射
```
