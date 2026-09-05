# LaTeXcellence — 设计文档

本文档阐述 LaTeXcellence 的架构设计、核心算法和关键技术决策。

---


## 目录

1. [架构概览](#1-架构概览)
2. [核心算法：Marker 替换-还原策略](#2-核心算法marker-替换-还原策略)
3. [LLM 输出校验体系](#3-llm-输出校验体系)
4. [文件安全操作](#4-文件安全操作)
5. [数据流与模块索引](#5-数据流与模块索引)
6. [前端架构](#6-前端架构)
7. [配置管理](#7-配置管理)
8. [错误处理策略](#8-错误处理策略)
9. [测试策略](#9-测试策略)
10. [关键设计决策总结](#10-关键设计决策总结)
11. [多用户模式设计决策记录](#11-多用户模式设计决策记录)
12. [Docker 沙箱执行模型（v2 P1a）](#12-docker-沙箱执行模型v2-p1a)
13. [LLM 每用户治理模型（v2 P1b）](#13-llm-每用户治理模型v2-p1b)
14. [共享项目反向补丁撤销（v2 P3）](#14-共享项目反向补丁撤销v2-p3)
15. [多实例状态外置（v2 P7）](#15-多实例状态外置v2-p7)
16. [模板体系：内置模板与私有模板库](#16-模板体系内置模板与私有模板库)
17. [多模型支持：模型档案与角色槽位路由](#17-多模型支持模型档案与角色槽位路由)

---
## 1. 架构概览

### 1.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React)                      │
│  Pages → Hooks (useParagraphCheck) → API Layer → Store  │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────────┐
│                    Backend (FastAPI)                      │
│                                                          │
│  modules/                    core/                       │
│  ├─ typo_check/              ├─ config.py                │
│  ├─ expression_opt/          ├─ llm_client.py            │
│  ├─ code_review/             ├─ workspace_manager.py     │
│  ├─ terminology_check/       ├─ project_manager.py       │
│  ├─ compile/                 ├─ tool_registry.py         │
│  ├─ writing_chat/            ├─ prompts.py               │
│  ├─ bib_manager/             └─ base_paragraph_check.py  │
│  ├─ ... (多个模块)          utils/                       │
│  └─ project_router.py       ├─ json_utils.py             │
│                             ├─ file_ops.py               │
│  ┌──────────┐               ├─ input_resolver.py         │
│  │ router.py │→ service.py  ├─ latex_parser.py           │
│  │  (薄层)   │  (厚层)      ├─ web_search/               │
│  └──────────┘               ├─ scholarly_research/       │
│                             ├─ project_tools/            │
│                             ├─ search_router.py          │
│                             └─ (按需) Marker 替换/还原    │
└─────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    External Services                     │
│              OpenAI API (or compatible endpoint)         │
└─────────────────────────────────────────────────────────┘
```

**设计原则**：

- **路由层（薄层）**：仅负责参数校验、HTTP 异常转换、SSE 封装。禁止写业务逻辑。
- **服务层（厚层）**：业务编排、LLM 调用、marker 还原、批量处理。
- **基础设施层**：配置管理、LLM 客户端、项目管理、日志、文件操作。
- **工具层**：JSON 解析/校验、原子写入、备份管理、`\input` 递归解析。

### 1.2 为什么这么设计

| 问题 | 解决方案 |
| --- | --- |
| LLM 输出不可靠（格式错误、字段缺失） | `retry_with_validation()` 闭环重试 |
| LaTeX 结构必须保护，LLM 只能改纯文本 | Marker 替换-还原策略 |
| 用户可能在审核期间修改文件 | 乐观锁：读取时获取 MD5，写回时校验，冲突返回 409 |
| 多模块共享相同的段落检查流程 | `useParagraphCheck` hook 抽象 |
| 配置需要运行时热重载 | 单例 + 方法内实时 `get_config()` |
| 编译错误信息难以理解 | 提取 stderr 和 stdout → LLM 翻译为中文解释 |
| 术语一致性是语义问题而非文本匹配 | LLM 语义聚类替代固定算法 |
| 参考文献格式识别等可枚举判断 | 确定性规则表优先（文献类型标识、期刊缩写、DOI 前缀、版式特征），LLM 仅兜底并附带规范样例 |
| LaTeX 项目通常拆分为多个 \input 文件 | `input_resolver.py` 递归展开，按文件分组结果 |
| 多浏览器同时处理不同项目 | `workspace_manager.py` 隔离各工作区 LatexProject 实例 |
| 联网搜索 | DuckDuckGo HTML、SearXNG JSON API 与 Tavily Search API；搜索支持 legacy/aggregate 双路径，读页支持 legacy DeepReader、结构化抓取/抽取/缓存；结构化文档按标题切块并通过 BM25/MMR 选择可追溯证据；工具通过 `ToolRegistry` 注册，LLM 自动调用 |

### 1.3 核心架构组件：BaseParagraphCheckService

多个段落处理模块（typo_check、expression_opt、code_review、terminology_check、paragraph_translate）共享相同的业务流程：文件解析 → marker 替换（code_review 例外：无 marker，直接审查真实 LaTeX）→ LLM 编排 → 结果写回。为避免重复代码，抽取 `BaseParagraphCheckService` 基类：

```
BaseParagraphCheckService
├── 文件解析：project_manager.get_paragraphs()
├── Marker 替换：latex_parser.strip_to_plain_text()
├── LLM 编排：批量处理 + 流式进度推送（SSE）
├── 结果写回：MD5 校验 → 原子写入
│
└── 多个 Hook（子类定制点）：
    ├── _get_system_prompt()              → 系统提示词（含 marker 保护约束）
    ├── _build_user_content(text)         → 构建 LLM User 内容
    ├── _get_model_cls()                  → 返回 Pydantic 响应模型类
    ├── _get_llm_params()                 → LLM 调用参数（temperature 等）
    ├── _get_batch_size()                 → 批处理大小
    ├── _build_success_result(...)        → 从 validated model 构建成功结果 dict
    └── _restore_change_markers(...)      → 将 changes 中的 marker 还原为 LaTeX（供展示）
```

子类只需实现 Hook 方法即可获得完整的段落检查能力，无需重复实现文件操作、marker 管理、重试逻辑等基础设施。

### 1.4 CheckOperationContext 操作上下文

每次 preview/check 操作生成唯一 `operation_id`（12 位 hex），关联的上下文（`CheckOperationContext`）保存在服务实例的 `_operation_contexts` 字典中：

| 字段 | 含义 |
| --- | --- |
| `operation_id` | 唯一标识 |
| `marker_maps` | 深拷贝的 marker 映射（`file_path → para_index → {marker: original}`） |
| `file_path` | 主文件路径 |
| `file_md5s` | 检查时各文件的 MD5 快照 |
| `created_at` | 创建时间戳（TTL 清理依据，默认 1 小时） |

**流程**：

1. Preview/Check 时：生成 `operation_id` → 深拷贝 `_marker_maps` → 存入 `_operation_contexts`
2. Apply 时：前端回传 `operation_id` → 查找对应上下文 → 使用上下文中的 `marker_maps` 还原 marker → 写回文件
3. 上下文过期后自动清理（TTL 1 小时）

此机制将请求相关状态从服务单例字段中解耦，避免并发请求或重启后 marker 映射丢失导致字面 marker 写入 `.tex` 文件。

---

## 2. 核心算法：Marker 替换-还原策略

### 2.1 问题定义

错字查找和表达优化需要 LLM 修改纯文本，但 LaTeX 源码中混合了：

- 可编辑的自然语言文本
- 不可编辑的 LaTeX 命令、公式、环境

如果直接将原始 LaTeX 发给 LLM，它可能会：

- 破坏 `\textbf{}` 等命令结构
- 修改数学公式内容
- 无法理解 `\verb|...|` 等特殊语法

### 2.2 解决方案

将 LaTeX 结构替换为不可见的 marker，LLM 只看到纯文本：

```
原始 LaTeX:      \textbf{这是一个错字} $x^2$ 更多内容
        ↓ strip_to_plain_text()
纯文本:         __CMD_0000__这是一各错字 __MATH_0000__ 更多内容
        ↓ LLM 检查修改（不碰任何 marker）
修正后:         __CMD_0000__这是一个错字 __MATH_0000__ 更多内容
        ↓ restore_from_markers()
还原 LaTeX:      \textbf{这是一个错字} $x^2$ 更多内容
```

### 2.3 解析流水线

9 步顺序执行，每步替换一类 LaTeX 结构。所有前缀共享同一个全局计数器，每次 `strip_to_plain_text()` 重置为 0。

**关键约束**：注释替换（第 4 步）在环境/数学/命令（第 5-8 步）之前，确保注释内的 `\command{}`、`$math$`、`\begin{env}` 不被单独提取。内联逐字（第 1 步）、URL 参数（第 2 步）与逐字类环境（第 3 步）在注释之前，因其内部可含字面 `%`。

| 步骤 | 目标 | 替换为 |
| --- | --- | --- |
| 1. `_replace_verb_inline()` | `\verb\|...\|` | `__VERB_NNNN__` |
| 2. `_replace_url_args()` | `\url` / `\path` / `\href` 完整命令（含可选参数与全部花括号参数，URL 内 `%` 为字面字符） | `__CMD_NNNN__` |
| 3. `_replace_protected_envs(envs=_VERBATIM_ENVS)` | 逐字类环境 `verbatim`/`lstlisting`（可含 `%`） | `__ENV_NNNN__` |
| 4. `_replace_comments()` | `%` 注释 | `__COM_NNNN__` |
| 5. `_replace_protected_envs()` | `table`/`equation`/`tikzpicture` 等环境 | `__ENV_NNNN__` |
| 6. `_replace_display_math()` | `$$...$$` / `\[...\]` | `__MATH_NNNN__` |
| 7. `_replace_inline_math()` | `$...$` / `\(...\)` | `__MATH_NNNN__` |
| 8. `_replace_commands()` | 所有 `\command` | `__CMD_NNNN__` |
| 9. `_normalize_whitespace()` | 连续空行 | 压缩为 2 行 |

**Marker 前缀**：`CMD`（命令）、`MATH`（公式）、`ENV`（环境）、`VERB`（逐字）、`COM`（注释）

### 2.4 命令解析（第 5 步）的难点

LaTeX 命令的参数结构复杂，需要字符级扫描：

```
\textbf{nested \textit{inner}}    → 花括号深度计数
\command{arg1}[opt]{arg2}         → [] / {} 交替扫描
\& \% \_ \# \$ \{ \}             → 转义字符，单 token
\, \: \; \! \- \\ \ \ ~ \^       → 单 token 命令
\LaTeX \today \hline \centering   → 无参数命令
```

核心技术：**花括号深度计数** + **转义字符识别** + **单 token 命令列表**。

### 2.5 还原策略的关键

Marker 格式为 `__{PREFIX}_{COUNTER:04d}__`（如 `__CMD_0010__`）。

还原时必须按 marker **创建顺序（counter）降序**排列：嵌套时外层 marker 后创建，先还原外层、让内层 marker 重新出现在文本中，随后内层才能被还原；同时 marker 定宽，也不会出现短 marker 误匹配长 marker 前缀的问题。

```python
# counter 降序（外层先还原）；非标准 marker 回退为长度降序
for marker, original in sorted(
    marker_map.items(), key=_marker_restore_key
):
    result = result.replace(marker, original)
```

`_marker_restore_key` 优先按 counter 降序（`-counter`），非标准 marker 回退为按长度降序，避免短 marker 误匹配长 marker 前缀。

### 2.6 结果展示时的双通道设计

逐段检查结果中的文本走两条数据通道，服务于不同消费者：

| 字段 | 内容 | 消费者 |
| --- | --- | --- |
| `corrected` / `optimized` / `translated` | 含 marker 的原始 LLM 输出 | `apply_changes()`：依赖 marker_map 将 marker 还原后再写入 `.tex` 文件 |
| `changes[].original` / `changes[].corrected` | 已还原 marker 的干净 LaTeX | 前端结果卡片：用户直接阅读的"原文 vs 修改后"对比 |

**设计动机**：`corrected` 字段必须保留 marker，因为 `apply_changes()` 在写回文件前会调用 `restore_from_markers()` 统一还原。若在 `_build_success_result` 中提前还原，apply 链路会因找不到 marker 而误判或跳过写入。而 `changes[]` 仅用于前端展示，还原后用户看到的是 LaTeX 命令（如 `\textbf{}`）而非 marker（如 `__CMD_0000__`），便于直观对比。

**统一入口**：基类提供 `_restore_change_markers(changes, file_path, para_index, fields)` 方法，内部通过 `self._marker_maps` 查询对应的 marker_map 并执行还原。该方法同时返回 `(restored_changes, marker_map)`，让需要额外还原顶层字段的服务（如 paragraph_translate）可直接复用 marker_map，避免对 `self._marker_maps` 重复查询。

---

## 3. LLM 输出校验体系

### 3.1 为什么需要校验

OpenAI 的 `response_format={"type": "json_object"}` 只保证输出是合法 JSON，**不保证字段符合预期 schema**。LLM 可能：

- 缺少必填字段
- 字段类型错误
- 输出多余字段
- 甚至输出非 JSON 文本

### 3.2 三层防护

```
┌─────────────────────────────────────────────────┐
│  第 1 层：retry_with_validation()                │
│  ┌─────────────────────────────────────────┐    │
│  │ LLM 输出 → safe_parse_json() → Pydantic │    │
│  │              ↓ 失败                      │    │
│  │ 错误反馈给 LLM → 重试（最多 3 次）      │    │
│  └─────────────────────────────────────────┘    │
├─────────────────────────────────────────────────┤
│  第 2 层：Marker 完整性验证                      │
│  ┌─────────────────────────────────────────┐    │
│  │ 比较 corrected 与 original 的 marker    │    │
│  │ 1) 计数校验（Counter）：缺失/多余/重复  │    │
│  │    → 回退到原始文本                      │    │
│  │ 2) 顺序校验（list ==）：重排/错位        │    │
│  │    → 回退到原始文本                      │    │
│  │ 纯文本移动但 marker 序列不变属合法编辑  │    │
│  └─────────────────────────────────────────┘    │
├─────────────────────────────────────────────────┤
│  第 3 层：异常兜底                               │
│  ┌─────────────────────────────────────────┐    │
│  │ LLM 调用抛出异常 → 返回原始文本          │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

### 3.3 JSON 容错解析

`safe_parse_json()` 处理 LLM 常见的输出问题：

1. Markdown 围栏：````json ... ```` → 提取 `{...}`
2. 尾逗号：`{"a": 1,}` → `{"a": 1}`
3. 单引号：`{'a': 1}` → `{"a": 1}`
4. 非 JSON 前缀/后缀：提取第一个完整的 `{...}`

### 3.4 错误反馈重试

当 Pydantic 校验失败时，将格式化后的错误信息反馈给 LLM：

```
JSON validation failed. Please fix the following issues:
  Field 'changes -> 0 -> corrected': Field required (type: missing)
```

LLM 收到错误后，有机会修正输出。这比简单的"请重新生成"更有效。

### 3.5 LaTeX 内容校验（latex_json_guard.py）

JSON schema 校验保证字段类型正确，但拦不住"合法 JSON + 损坏内容"的 LaTeX 字段：LaTeX 命令以反斜杠开头，而 JSON 字符串中反斜杠是转义符，未转义的反斜杠会被 JSON 静默损坏（`\rho` → 回车 + "ho"）或解析失败（`\sigma`）。`backend/utils/latex_json_guard.py` 提供**内容级校验**：

- 控制字符（除换行外）—— JSON 反斜杠转义损坏特征
- `[object Object]` 等垃圾占位符
- 花括号配平（忽略 `\{` / `\}`，跳过注释、verbatim、URL 内容）
- `$` 配平（同上跳过规则）
- 可选 `\begin` / `\end` 环境栈式配平；BibTeX 模式 `%` 为字面字符
- 宽松模式（`relaxed=True`）：分隔符协议（非 JSON）路径放行 `\t` / `\r` 等合法 LaTeX 空白，仅拦截 `\b` / `\f` 等真实损坏特征，错误归因不再提示 JSON 转义

接入方式：以 `LatexText` 字段类型挂到 Pydantic 模型（code_review / bib_manager / OCR），或在写盘前由工具执行器调用 `check_latex_text()`（template_gen `write_file`、edit_tools `edit_paragraph`）。OCR 模块（formula_ocr / table_ocr）、style_modify 与 writing_chat 编辑输出（generate_edit）改用分隔符协议（`<<<LATEX>>>...<<<END>>>`）让反斜杠原样传输，并用该校验器把损坏输出转为可重试错误（writing_chat 走 `relaxed=True` 宽松模式）。

---

## 4. 文件安全操作

### 4.1 原子写入

写入文件时采用 **写临时文件 → flush → rename** 策略：

```
1. 在目标文件同目录创建 .tmp 临时文件
2. 写入内容并 flush 到磁盘
3. os.replace() 原子覆盖目标文件
```

这防止了写入中断（断电、崩溃）导致文件损坏。

### 4.2 乐观锁

多浏览器/多工作区场景下，两个用户可能同时编辑同一个 `.tex` 文件。乐观锁通过 MD5 哈希检测冲突：

```
1. 读取阶段（get_paragraphs / get_file_md5）：
   计算文件 MD5 → 返回给前端保存

2. 写回阶段（apply_change）：
   前端传回期望的 MD5（expected_md5）
      ↓
   重新计算当前文件 MD5
      ↓
   MD5 一致 → 继续写入（原子写入 + 更新内部缓存）
   MD5 不一致 → 抛出 FileConflictError（HTTP 409）
      ↓
   前端收到 409 → 刷新文件 MD5 → 提示用户重试
```

**校验优先级**：`apply_change()` 的 `expected_md5` 参数优先于实例内部缓存的 MD5。前者用于跨工作区乐观锁（前端传入），后者用于单工作区内校验（兜底）。

**冲突响应格式**：

```json
{
  "error": "file_conflict",
  "message": "File chapter1.tex has been modified by another workspace...",
  "file_path": "chapter1.tex",
  "expected_md5": "a1b2c3d4...",
  "current_md5": "e5f6a7b8..."
}
```

**批量应用冲突处理**：`BaseParagraphCheckService.apply_changes()` 逐段校验，冲突段收集到 `errors` 列表（含 `paragraph_index`、`file_path`、`reason="file_conflict"`、`expected_md5`、`current_md5`），成功段正常写入，前端据此区分已应用和冲突项。

### 4.3 撤销/重做栈

每文件维护独立的 `undo: deque` 和 `redo: deque`，最大 50 层：

```
修改 A → 修改 B → 修改 C
undo 栈: [A, B, C]
redo 栈: []

undo → 恢复到 B
undo 栈: [A, B]
redo 栈: [C]

redo → 恢复到 C
undo 栈: [A, B, C]
redo 栈: []

新修改 D → 清空 redo 栈
undo 栈: [A, B, C, D]
redo 栈: []
```

---

## 5. 数据流与模块索引

> 各模块的详细数据流、Pydantic 模型、API 与配置以模块文档为准；本节只保留跨模块的通用数据流与模块速览。

### 5.1 跨模块通用数据流

- **逐段检查**（typo_check / expression_opt / terminology_check / code_review / paragraph_translate）：文件解析 → marker 替换（code_review 例外）→ LLM 批量/流式处理 → SSE 推送 → apply 时还原 marker + MD5 校验 + 原子写入。基类细节见 [1.3 核心架构组件](#13-核心架构组件baseparagraphcheckservice)。
- **Agent 自主循环**（template_gen / plot_gen / style_modify 项目 Agent）：Function Calling 规划 → 写文件/执行 → 编译或沙箱验证 → 读结果自修 → finish；文件工具统一复用 `backend/utils/agent_file_tools.py` 的 schema、调度与 `line_range` 读取契约，模块只保留校验和存储策略；全程写入 `trace.jsonl`，事件经 SSE 推送。
- **图片 OCR**（formula_ocr / table_ocr）：上传图片 → 预处理 → Vision API 分隔符协议输出 → 内容校验重试 → 统一格式化。
- **外部进程**（compile / git_diff / format_converter）：调用 latexmk / pandoc / git；LLM 仅在编译失败翻译、宏包兜底等场景介入。

### 5.2 多文件 \input 展开数据流

多个段落处理模块（typo_check、expression_opt、code_review、terminology_check、paragraph_translate）共享相同的多文件展开机制：

```
用户点击「包含子文件」开关
     ↓ resolve_input = true
router 传递 resolve_input 查询参数给 service
     ↓
service._get_files(file_path, resolve_input=True)
     ↓ input_resolver.resolve_inputs()
递归解析 \input / \include / \subfile → 有序文件列表 [main.tex, chapters/a.tex, ...]
     ↓
逐文件提取段落 → strip_to_plain_text() → 缓存 per-file marker_map
     ↓
LLM 逐批处理（paragraph_index 为文件内局部索引）
     ↓
结果携带 file_path 字段 → 前端按文件 Tab 分组展示
     ↓
apply 时携带 file_path → 精确写回对应文件
```

**关键设计决策**：

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 展开时机 | marker 替换之前 | `\input{}` 命令必须在原始文本中可见，marker 替换后无法识别 |
| 段落索引 | 文件内局部索引 | 避免跨文件索引冲突，简化 apply 逻辑 |
| 循环引用 | `visited` 集合 | `\input{a}` → `\input{b}` → `\input{a}` 时终止 |
| 注释处理 | 展开前去除 `%` 注释 | 避免注释行中的 `\input` 被误识别 |
| 结果归属 | 携带 `file_path` 字段 | apply 时精确定位目标文件，避免跨文件误写 |

### 5.3 模块数据流索引

| 模块 | 一句话流程 | 详细文档 |
| --- | --- | --- |
| 错字查找 | 逐段检查典型流程：preview → LLM 校验 → diff → apply | [typo_check.md](docs/modules/typo_check.md) |
| 表达优化 | 同逐段检查，输出字段为 `optimized`，支持自定义提示词 | [expression_opt.md](docs/modules/expression_opt.md) |
| 术语一致性 | 逐段提取术语 → 语义聚类 → 检查一致性 | [terminology_check.md](docs/modules/terminology_check.md) |
| 逐段翻译 | 同逐段检查，输出字段为 `translated` | [paragraph_translate.md](docs/modules/paragraph_translate.md) |
| LaTeX 代码审查 | 工具格式化真实 LaTeX → LLM 只读审查 → 建议展示不写回 | [code_review.md](docs/modules/code_review.md) |
| 写作助手 | 多轮对话 + Function Calling（搜索/学术/项目浏览/段落编辑），编辑建议走 RS2 分隔符协议 | [writing_chat.md](docs/modules/writing_chat.md) |
| 参考文献管理 | 格式识别（规则层优先）→ 元数据提取 → BibTeX 生成 → 写入 .bib | [bib_manager.md](docs/modules/bib_manager.md) |
| 自动编译 | latexmk 编译（texfot 过滤）→ 失败时 LLM 翻译错误 | [compile.md](docs/modules/compile.md) |
| 模板生成 | 自主 Agent 写文件 → 编译验证 → 修错重试 → 产出可下载工程 | [template_gen.md](docs/modules/template_gen.md) |
| 科研绘图 | 数据画像 → 图表推荐 → Python 沙箱执行 → TikZ 确定性生成 → 编译 | [plot_gen.md](docs/modules/plot_gen.md) |
| 公式/表格 OCR | 图片预处理 → Vision 分隔符协议输出 → 内容校验重试 | [formula_ocr.md](docs/modules/formula_ocr.md)、[table_ocr.md](docs/modules/table_ocr.md) |
| 格式转换 | pandoc 子进程转换，宏包分析兜底补充 `\usepackage` 注释 | [format_converter.md](docs/modules/format_converter.md) |
| 样式修改 | 粘贴代码单次请求，或项目 Agent overlay + shadow 编译 + proposal 事务写回 | [style_modify.md](docs/modules/style_modify.md) |
| Git 差异高亮 | 两个 Git revision 内容对比 → 差异高亮文档 → latexmk 编译 PDF | [git_diff.md](docs/modules/git_diff.md) |

---

## 6. 前端架构

### 6.1 状态管理策略

采用 Zustand 5 + `persist` 中间件：

- **持久化字段**：项目路径、活动文件、各模块的段落和结果、`writingChatCurrentSessionId`、`workspaceId`、`rootPath`、`mainFile`、`currentTheme`
- **非持久化字段**：文件树、PDF URL、加载状态、`writingChatMessages`、`writingChatCompressed`

刷新页面后，项目身份和检查结果自动恢复，无需重新加载。

### 6.2 useParagraphCheck hook

多个逐段检查页面（TypoCheck、ExpressionOpt、CodeReview、TerminologyCheck、ParagraphTranslate）共享相同的段落检查流程。通过 `useParagraphCheck` hook 抽象：

```javascript
useParagraphCheck({
  apiModule,          // API 模块
  checkMethodName,    // 方法名（"check" / "optimize"）
  correctedTextField, // 修正文本字段名（"corrected" / "optimized"）
  storeSlice,         // Store 切片键
  messages,           // 提示文案
})
```

Hook 内部封装了：项目恢复 → 文件选择 → 预览 → 检查 → 排序 → 应用 → refresh 持久化。

新增 `resolveInput` 状态和 `activeFiles` 派生列表：开启多文件模式后，`activeFiles` 包含所有解析到的文件路径，前端按文件 Tab 分组展示段落和结果。

### 6.3 主题切换优化

问题：CSS `transition` 在 DOM 树上逐元素启动，深层元素天然晚于浅层，导致视觉不同步。

解决方案：

1. **CSS 自定义属性过渡**：在 `:root` 上过渡 `--background` 等 ~30 个变量，子元素通过 `var()` 继承插值后的值
2. **过渡期间抑制逐元素 transition**：`data-theme-transitioning` 属性 + CSS 规则 `transition-duration: 0s !important`

### 6.4 前端设计体系（学术手稿方向）

前端视觉以「被排好版的学术手稿」为方向，与产品题材（LaTeX 排版）同源：

- **字体角色**：`--font-display` = **STIX Two Text**（学术衬线），仅用于字标、§ 章节编号、英文 small-caps 标签（`.text-small-caps`）；中文标题保持 Noto Sans SC，正文保持霞鹜文楷，均不随主题色板改动。
- **签名组件**：`components/Wordmark.jsx`（字标）；`components/PageHeader.jsx`（共享页头，传 `groupKey` 自动生成 "§N · 分组" kicker）；`components/EmptyState.jsx`（无项目/恢复中/空结果）；`components/ProofDiff.jsx`（原文→改后对照）。
- **欢迎页**：标题页 + 点线目录（dotted-leader TOC），功能分组按写作流程编 § 号（`pages/WelcomePage.jsx` 的 `featureGroups`；§ 号映射 `SECTION_BY_GROUP` 定义在 `frontend/src/lib/writingFlow.js`，由 WelcomePage / PageHeader 共享）。
- **全局背景**：`App.jsx` 内联 `bgSvg`（`--primary` 圆形 + 双色 `\LaTeX` 字标，右下角固定，全站共享）。
- **动效**：欢迎页 `.reveal` 错落入场，尊重 `prefers-reduced-motion`；全站不使用 `hover:scale` 类通用缩放。
- **主题色板架构（"一色一阶"）**：所有配色主题共用同一套中性灰阶（亮/暗各一档），主题身份由单一色相承载，`--primary` / `--accent` / `--ring` / `--selection-bg` 均按一致的亮度/彩度偏移量从该色相派生；琥珀黄等高明度色相的主按钮使用深色前景保证对比度；深色模式主按钮统一使用深色文字。语义色（危险红、字标墨色）跨主题一致，深色模式下字标统一反白，避免装饰元素在暗色背景上不可见。

---

## 7. 配置管理

### 7.1 热重载机制

```python
# 全局缓存
_config: Optional[AppConfig] = None
_config_path: Optional[Path] = None
# 环境变量注入的敏感字段集合（SECRET_ENV_PATHS 值域），落盘前自动清空
_secrets_from_env: set[str] = set()
_config_fingerprint: tuple[int, int] | None = None  # config.json (mtime_ns, size) 指纹

def get_config() -> AppConfig:
    if _config is None:
        raise RuntimeError("Config not loaded")
    if _fingerprint_changed():   # v2 P7：探测 mtime+size 变化，多副本配置不再漂移
        _reload_from_disk()
    return _config
```

所有方法内实时调用 `get_config()`，不在 `__init__` 中缓存配置对象。这样前端修改配置后，后端立即生效。

v2 P7 多实例语义：

- **指纹热重载**：`get_config()` 每次调用比对 config.json 的 `(mtime_ns, size)` 指纹，变化即从磁盘重载（校验失败保持旧配置并告警一次，待下次修改自动重试）。多副本部署下管理员改一份配置文件，全部副本在下次访问时自然收敛；
- `save_config()` / `update_config_from_dict()` 写盘后同步刷新指纹——自身写盘不构成"外部变更"，不触发无意义重载；重载会重新应用敏感项环境变量覆盖（`SECRET_ENV_PATHS` 注册表），注入来源语义不变；
- **`multi_user.enabled` 锚定**：该开关决定认证中间件、数据库初始化、用户空间布局等启动期形态，热切换会造成半初始化状态。热重载检测到磁盘值与启动锚定值不同时保持锚定值并告警，**变更仍需重启生效**。

### 7.2 安全策略

- 敏感项（LLM Key / SMTP 密码 / Tavily 搜索与学术数据源 API Key）经 `SECRET_ENV_PATHS` 注册表支持环境变量注入，优先级高于配置文件
- 前端提交的敏感项以 `***` 开头时保留原值（掩码保护）
- `config.json` 加入 `.gitignore`，提供 `config.example.json` 作为模板

---

## 8. 错误处理策略

### 8.1 三层策略

1. **前置条件检查**（快速失败）：

   ```python
   if not path.exists():
       raise FileNotFoundError(f"File not found: {path}")
   ```

2. **try/except + 指数退避重试**：

   ```python
   for attempt in range(max_retries):
       try:
           result = await call_llm()
           return result
       except Exception:
           await asyncio.sleep(2 ** attempt)  # 1s → 2s → 4s
   ```

3. **日志后重抛**：
   ```python
   except Exception as e:
       logger.error(f"LLM call failed: {e}")
       raise
   ```

### 8.2 异常体系

复用标准异常（`ValueError`, `RuntimeError`, `FileNotFoundError`, `IndexError`），保持异常体系简洁。仅当异常需要携带结构化数据供 API 层使用时（如 `FileConflictError` 携带 `file_path`/`expected_md5`/`current_md5`），允许定义专用异常类。

---

## 9. 测试策略

### 9.1 分层测试

| 层级 | 测试内容 | Mock 策略 |
| --- | --- | --- |
| 单元测试 | latex_parser、project_manager | 无需 Mock |
| 集成测试 | 各模块的 service + router | Mock LLM 调用 |

### 9.2 LLM Mock 原则

单元测试中**严禁**发起真实网络请求。所有 LLM 调用通过 `pytest-mock` 或 `unittest.mock` Mock。

Mock 返回值必须符合 Pydantic schema，确保测试的可重复性。

### 9.3 LaTeX 数据

测试用的 LaTeX 字符串使用原始字符串 `r"..."`，避免转义问题。

### 9.4 多用户模式（P0–P7 全部阶段）

多用户模式（`multi_user.enabled=true`，默认关闭）通过**中间件 + 依赖**两条路径实现双模式共存，设计要点：

- **双模式中间件**（`security_middleware.py`）：`enabled=false` 时完全保持单用户现状（本地目录 + 共享访问令牌）；`enabled=true` 时对全部 `/api/*`（公开端点 `/api/health`、`/api/config/status`、`/api/auth/login`、`/api/auth/register` 除外）强制会话 Cookie 认证，变更类方法（POST/PUT/PATCH/DELETE）强制 `X-Latexcellence-CSRF` 请求头，会话校验实时查库（禁用账号即时失效）。
- **认证核心**（`backend/core/multiuser/auth.py`，同步函数）：argon2id 密码哈希；会话令牌为 32 字节随机数，数据库仅存 SHA-256 哈希，过期时间由配置（默认 7 天 / 记住我 30 天）决定；用户名大小写不敏感唯一（`COLLATE NOCASE` 唯一索引 + 注册事务）；登录限流按（用户名小写, IP）分钟级计数。
- **存储**：SQLite（WAL + 外键 + 部分唯一索引）单进程共享连接，`RLock` 串行化事务；所有压库操作经 `asyncio.to_thread` 调出事件循环。
- **错误契约**：认证/管理接口统一返回结构化 `detail={"error": code, "message": ...}`，错误码见方案 §10.5（`unauthorized` / `no_permission` / `invalid_csrf` / `account_pending` / `account_disabled` / `rate_limited` 等）。
- **管理员引导**：首次启动且用户表为空时从环境变量创建管理员，否则由 CLI（`python -m backend.manage create-admin`）创建；CLI 覆盖创建/重置密码/列表/状态流转。
- **用户空间与路径隔离（P2）**：项目目录由 `project_id` 派生（`data/users/<owner_id>/projects/<project_id>/`）；统一入口 `resolve_user_path()`（`path_guard` 封装）解析项目内路径，禁止直接拼接；符号链接默认拒绝。
- **访问控制（P2）**：`backend/core/access_control.py` 提供 `owner / editor / viewer` 权限矩阵、`require_project_access()` / `get_projects_for_user()` 与基于 workspace 绑定的请求级依赖 `require_workspace(write=...)`，权限实时查库不缓存。
- **workspace 绑定与端点收紧（P2）**：`/api/project/open` 多用户模式仅接受 `project_id`（拒绝 `path`），打开时写入 `workspace_id → (user_id, project_id, root_path)`；`/api/project/browse` 返回 403；写操作要求非 viewer；共享项目 v1 撤销禁令已在 v2 P3 由反向补丁取代（见 §14）。
- **权限协作（P4）**：`/api/projects/{id}/permissions` 提供协作者列表 / 按用户名添加 / 改角色 / 移除（owner 专属，权限实时查库即时生效）；`/api/projects/{id}/transfer` 支持转交 owner（原 owner 转后 viewer 或退出）。转交前校验接收方配额（超限拒绝）；转交时项目目录迁移到新 owner 空间、新 owner 同名项目自动 `xx (2)`、已打开 workspace 绑定与文件树同步更新。
- **衍生数据记账（v2 P4）**：四类衍生目录（写作会话 / 样式会话 / 模板 / 绘图）的用量缓存在 `user_derived_usage` 表（`(user_id, subdir)` 主键），配额校验与 `/api/auth/me` 用量读取为 O(1) 数据库查询，不再逐请求 rglob 全盘扫描。准确性由两级机制维护：**检查点增量刷新**（会话/模板/绘图创建删除、TTL 与自助清理触达某分类时，仅重扫该用户该分类目录）+ **维护循环每小时全量对账纠偏**（缓存表全空时首读自动引导，冷启动读数即准）。配额前置校验携带被触达分类时先刷新该分类缓存行再比较——创建时刻的边界严格性不放松（超限仍 413 `quota_exceeded`），纯缓存读对跨分类漂移容忍、由对账最终一致。
- **后台清理扩充（v2 P4）**：维护循环并入过期登录会话删除（修复 sessions 表无限增长）与写作会话保留期清理（`multi_user.writing_chat_retention_days`，默认 0 = 不自动删仅统计；按文件 mtime 判定，与既有 TTL 清理口径一致）；用户可在工作台按分类自助清理本人衍生数据（`POST /api/auth/cleanup-derived-data`，未知分类 400 `invalid_category`，二次确认由前端承担），不做管理员强制清理。
- **分片上传与断点续传（v2 P5）**：大文件上传改走五端点会话协议（`POST /api/projects/upload-sessions` 建会话、`PUT .../{sid}/parts/{file}/{chunk}` 传分片、`GET .../{sid}` 位图对位、`POST .../{sid}/complete` 拼装建档、`DELETE .../{sid}` 中止）：init 携 manifest（相对路径 + size + md5 + 分块数）做结构预检（复用解压管线的路径穿越 / 大小写冲突校验）与剩余配额预检；分片幂等落盘 staging 并以 `X-Part-MD5` 逐片校验，成功即续期会话；complete 校验分块齐全 + 逐文件全量 MD5 复核后拼装——压缩包走既有解压链路、目录按 manifest 拼布局走同一校验链——再配额二次复核、落位建档。**分片阶段不占配额**（init 预检 + complete 复核兜底，复核失败整包拒绝并清场）。会话行存 `upload_sessions` 表（manifest JSON + `last_activity_at` TTL 基准），过期会话与孤儿 staging 目录由维护循环清理；通用暂存区 mtime 兜底豁免带标记的会话目录。前端并发 3 分片队列、指数退避重试、暂停/续传、localStorage 持久会话支持刷新后重新选择内容断点续传（先查位图再补缺）；JSZip 客户端打包路径移除。
- **SMTP 邮件通知与自助找回（v2 P6）**：新增 `core/multiuser/mail.py`——业务方投递即返回，`email_outbox` 表为队列，lifespan 启动的发送协程经 aiosmtplib 异步消费：指数退避重试 `smtp.retry_attempts` 次、失败留档 failed（last_error）供排查、滚动 60s 窗口全局限速 `max_send_per_minute`；`smtp.enabled=false`（或缺 host）时全部通知点静默跳过优雅降级。事件矩阵：待审批注册聚合去抖（每 5 分钟一封给全部 admin）、审批通过/拒绝/禁用与管理员重置密码即时通知该用户、配额预警 80%/95% 迟滞检查（挂维护循环对账后，只读用量缓存；低于 reset 阈值清标记后可重发；95% 加送 admin）。模板中英两套存于 mail 模块集中管理，找回链接前缀取 `site_base_url` 未配置退化为相对路径提示。自助找回：`POST /api/auth/forgot-password` 按用户名查邮箱或按邮箱查用户（多用户同邮箱降级），令牌只存 SHA-256（TTL 1 小时单次有效、条件 UPDATE 原子消费防重放），`POST /api/auth/reset-password` 校验后 argon2id 写入并吊销全部会话；两端点均做 IP 维度滑动窗口限流（复用 `rate_limit_events`），forgot 响应恒定时长（占位 argon2 校验拉平）+ 统一 `delivery=sent/unavailable` 语义不泄露账号存在性。users.email 部分唯一索引（NOCASE、空值豁免）由每次启动的幂等补建保证：存量重复邮箱时告警列冲突行并跳过，管理员人工处理后重启自愈；`PUT /api/auth/profile` 绑定/改绑邮箱需当前密码确认。

- **注册自动审批（IP 策略）**：注册时解析真实客户端 IP（`clientip.py`：仅当 TCP 对端属于 `multi_user.trusted_proxies` 白名单才信任 X-Forwarded-For，防伪造头绕过）并始终落库（users 表 v9 迁移新增 `registered_ip` / `last_login_ip` / `auto_approved`）。审批决策（`registration_policy.py`）为正向白名单叠加：同 IP 历史账号数达上限 → 不自动通过；命中 CIDR 白名单或离线地理白名单（国家码 + 城市名 AND 语义）→ 自动通过 active 并发开通邮件。地理查询走双库解析链：优先 ip2region（.xdb 官方 binding vendored 于 `multiuser/ip2region_xdb/`，中国命中直接采用，国内城市精度显著优于 GeoLite2），非/未知中国回落 MaxMind GeoLite2（.mmdb）；两库均按 mtime 缓存、懒加载可缺省。全部白名单未配置即「全放行」最简模式。任何不通过只降级人工待审批——**只降级不拒绝**；地理库缺失/查询失败 fail-closed 转人工，注册主流程永不因策略故障 5xx。配套每 IP 注册滑动窗口限流（kind='register_ip'，超限 429 `register_rate_limited`）。

设计总览见 [docs/multi-user.md](docs/multi-user.md)，实现细节见 [docs/backend-core.md](docs/backend-core.md)，部署指南（HTTPS、风险声明）见 [docs/deployment.md](docs/deployment.md)。

---

## 10. 关键设计决策总结

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 后端框架 | FastAPI | 原生 async/await，自动 OpenAPI 文档 |
| 前端框架 | React 19 | 生态成熟，shadcn/ui 组件丰富 |
| 状态管理 | Zustand 5 | 轻量，支持 persist 中间件 |
| 配置校验 | Pydantic v2 | 类型安全，JSON Schema 自动生成 |
| 文件写入 | 原子写入 | 防止写中断损坏文件 |
| 撤销机制 | 内存 deque | 比文件备份更高效，支持多步 |
| LLM 校验 | Pydantic + 重试 | 闭环反馈，比简单重试更可靠 |
| Marker 还原 | 创建顺序（counter）降序；非标准 marker 回退按长度降序 | 外层 marker 先还原，同时防止短 marker 误匹配长 marker 前缀 |
| 编译错误翻译 | 外部进程 + LLM | 正则提取关键错误，LLM 翻译为中文解释，不修改源文件 |
| 写作助手 Marker 策略 | 全程无 marker | 对话与编辑建议都直接使用原始 LaTeX；编辑建议走分隔符协议（`<<<LATEX>>>...<<<END>>>`）避免 JSON 转义损坏 |
| 深度思考渲染 | `<details>` + parseThinking | 流式解析 `<thinking>` 标签，实时展开/折叠；页面刷新时 `processLoadedMessages` 恢复 |
| 助手提示词注入 | system prompt 注入 | 不污染用户消息体，用户不可见，可随时切换角色 |
| 参考文献元数据结构 | 动态 dict（非固定 Pydantic 字段） | 不同格式（GB/T、APA、IEEE）字段集不同，固定模型无法覆盖；`fields: dict[str, str]` 避免字段映射 |
| 缺失字段检测 | 基于 `raw_observed_fields` 过滤 | 只报告输入中实际出现的必填字段缺失，不报输入中没有的字段（如 abstract），减少误报 |
| 格式配置持久化 | JSON 文件，文件名由 `format_name` 自动生成 | 无需用户手动命名；合法字符替换为 `-`；支持 CRUD 加载复用 |
| 联网搜索引擎 | DuckDuckGo HTML + SearXNG JSON API + Tavily Search API | `legacy` 保持主引擎串行降级；`aggregate` 在统一截止时间内并发运行可用引擎，失败 lane 返回 `EngineStatus`，成功候选经安全 URL 规范化、近重复抑制、RRF、来源加权和域名上限得到稳定部分结果；显式指定引擎仍走单引擎路径 |
| 网页读取 | DeepReader + FetchCoordinator/ExtractorRegistry | `page_reading.strategy=legacy` 保留纯文本兼容路径；`structured` 使用公网 HTTPS 策略、原始/抽取缓存、条件请求、MIME 路由、多候选 HTML 质量评分、标题感知切块与 BM25/MMR 证据选择；抽取版本进入缓存键，配置可热重载回滚 |
| 多用户认证 | argon2id + HttpOnly 会话 Cookie（库只存哈希）+ 注册审批流 | 共享令牌只适合单用户本地部署；多用户模式强制会话认证与 CSRF 头，管理员审批制配合登录限流控制滥用 |
| 多用户执行安全 | v1 可信团队部署（不沙箱）；Docker 沙箱列入 v2 | latexmk / Python / pandoc / git 等面向不可信上传内容，隔离是开放部署的前提 |

## 11. 多用户模式设计决策记录

多用户模式的完整关键决策记录（双模式、认证、存储、配额、权限、上传、迁移等）见 [docs/multi-user.md §13](docs/multi-user.md)。P0–P7 全部落地为 `backend/core/multiuser/`（db/userspace/auth/dependencies/quota/upload/upload_sessions/maintenance/migrate/edit_log/sandbox_leases/llm_governance）、`backend/modules/auth/`（`/api/auth` 与 `/api/admin`）、`backend/modules/projects/`（`/api/projects` 与权限协作）与 `backend/manage.py`（CLI），设计与实现见 §9.4 与 [docs/backend-core.md](docs/backend-core.md)，部署见 [docs/deployment.md](docs/deployment.md)。

## 12. Docker 沙箱执行模型（v2 P1a）

多用户模式设计总览见 [docs/multi-user.md](docs/multi-user.md)；本节记录已落地的沙箱执行模型。

### 12.1 统一执行入口

全部外部命令执行（latexmk/texfot 编译、git/latexdiff、pandoc、Python 绘图、latexindent）收口到 `backend/utils/sandbox/` 的 SandboxRunner。调用方把"命令 + 工作目录 + 超时"描述为 ExecSpec，不感知后端；结果归一为 ExecResult（returncode / stdout / stderr / timed_out / oom_killed），既有错误翻译管线零改动。

```
调用点（ExecSpec）──▶ SandboxRunner.execute()
                         │ mode 解析（实时读配置，支持热重载）
                         │   local  ──▶ 本地子进程（进程组建组 + 超时整树击杀，与 v1 行为逐项一致）
                         │   auto   ──▶ daemon 探测（TTL 缓存）命中即容器，否则回落 local 并告警
                         │   docker ──▶ 强制容器；daemon 不可达 → sandbox_unavailable（503）
                         ├─ 全局并发：进程内 Semaphore(max_concurrent_global)
                         ├─ 每用户并发：sandbox_slots 表租约（多用户模式；BEGIN IMMEDIATE 原子校验，
                         │              TTL = 超时+120s，崩溃残留由维护循环清理，P7 起跨副本生效）
                         └─ docker 后端：一次性容器（无网络 / 只读根 fs + tmpfs /tmp /
                                        memory·cpus·pids 限额 / no-new-privileges /
                                        drop ALL caps / 宿主同 uid:gid），超时强杀，
                                        OOMKilled 或退出码 137 翻译为 oom_killed
```

### 12.2 关键决策

| 决策 | 说明 |
| --- | --- |
| 同绝对路径挂载 | bind mount 使用 host_path 即容器内路径：template_gen 等解析 .log 文件路径的逻辑零改动；产物属主一致（容器以宿主后端同 uid:gid 运行）。代价：Docker Desktop 的路径翻译不受支持（Linux/WSL2 一等支持） |
| 错误表达不新增异常类 | 沙箱基础设施错误复用 RuntimeError 实例 + `sandbox_code` 属性（遵守 AGENTS.md "禁止自定义异常类"）；路由层经 `to_http_exception()` 映射为 503/429 + `detail.error` 错误码，前端走统一 translateError |
| 可用性门控跟随后端 | pandoc/texfot/latexindent 的本地 PATH 探测在 docker 后端下跳过（工具由镜像提供），经 `cached_backend_for(kind)` 同步读取最近一次解析结果，不触发 I/O |
| plot_gen 双层防御 | LLM 脚本进 plot 容器执行的同时保留进程内审计钩子（sandbox_guard）：容器是边界，钩子是纵深 |
| 回落零行为变化 | local 后端完整复刻历史 subprocess 语义（环境变量合并、进程组建组、超时整组击杀），既有测试全量回归通过 |
| 契约不支持 stdin | ExecSpec 无 stdin 协议：需要输入数据的调用方（pandoc 文本转换）一律落工作目录内临时文件后以路径传入——docker 后端不注入标准输入，文件化让两条后端行为天然一致，避免"仅本地可用"的半支持协议（评审发现的 Critical 缺陷根因） |
| 沙箱镜像不设 ENTRYPOINT | runner 统一以完整 argv 驱动容器；镜像若设 ENTRYPOINT 会与完整 argv 叠加成错误命令（如 `pandoc pandoc ...`）。convert 冒烟测试含 ENTRYPOINT 回归哨兵 |

### 12.3 与 v2 后续阶段的衔接

- **P1b**：LLM 每用户限流/并发复用本阶段的 DB 访问模式与租约思路（rate_limit_events / llm_concurrency_leases 表）；
- **P7**：sandbox_slots 租约基于 BEGIN IMMEDIATE，在同一共享卷上天然跨副本生效；daemon 探测缓存为进程内状态，多副本各自探测互不影响。

## 13. LLM 每用户治理模型（v2 P1b）

多用户模式下 `llm.max_concurrency` 全局信号量之外的第二层治理：每用户速率与并发上限强制执行（`multi_user.llm_rate_limit_per_minute` / `llm_max_concurrency_per_user`），补齐 v1 P7 缺口。

### 13.1 准入流程

```
请求 ──▶ AccessSecurityMiddleware（多用户模式会话验证成功后全局注入
         userspace.set_current_user_id：纯 ASGI 中间件与下游路由/SSE 同任务
         上下文，覆盖全部 /api 端点，不依赖各路由单独声明依赖）
   └─▶ LLM 调用（chat / chat_stream / *_with_tools 等全部公共入口）
        └─▶ llm_governance.admission_gate()      # 注入点在 llm_client 最外层
             │ governed_user_id()：多用户关闭 或 无请求级用户上下文 → 直通（零 DB 依赖）
             ▼ 单事务 BEGIN IMMEDIATE（asyncio.to_thread 调度）
             ├─ 惰性清理过期租约
             ├─ 速率：COUNT(rate_limit_events, kind='llm', identity=user_id, ts ≥ now-60s)
             │        ≥ 阈值 → 抛 llm_rate_limited（Retry-After = 窗口内最老事件到期秒数）
             ├─ 并发：COUNT(llm_concurrency_leases WHERE user_id) ≥ 阈值
             │        → 抛 llm_concurrency_limited（Retry-After = 最早租约到期秒数）
             └─ 通过 → INSERT 限流事件 + INSERT 租约(task_id=uuid, TTL 900s)
                   调用结束 finally DELETE 租约（流式提前断开经 GeneratorExit 同样覆盖）
```

被拒绝的调用不落任何行——速率窗口只统计已准入调用、并发等待不消耗速率配额。逐段检查共享基类（`base_paragraph_check`）对治理超限原样重抛而非静默降级：流式路径转为 error 事件、批量路径直达 429。错误表达沿用沙箱约定：RuntimeError 实例 + `gov_code`/`retry_after` 属性（不新增异常类），main.py 经 `register_governance_handler()` 注册的全局异常处理器把带标记的异常翻译为 HTTP 429 + Retry-After 头 + 应用既有 `detail` 包装的结构化错误体；SSE error 事件中的英文原文由前端 PATTERN_MAP 兜底翻译。

### 13.2 与登录限流的关系

登录限流自 v2 P1b 起从进程内字典迁移到同一张 `rate_limit_events` 表（kind='login'，pair/user 双维度各落一行），修复多实例部署下阈值被副本数放大的问题；维护循环按保留期统一清理两类事件，`purge_expired_llm_leases()` 清理崩溃残留租约。P7 多实例阶段同一共享卷上天然跨副本生效，本模块无需修改。

## 14. 共享项目反向补丁撤销（v2 P3）

多用户模式下撤销/重做从"工作区内存快照栈"（`BackupManager`，仅单用户模式沿用）升级为**按操作者归属的反向补丁日志**：只回退当前用户自己的修改，冲突即拒绝，共享项目解禁（v1 全局禁令移除）。

### 14.1 记账与快照布局

```
LatexProject.apply_change / apply_whole_files（全部项目写通道）
  └─▶ edit_log.record_write(root, [(rel, before, after), ...])
       │ 多用户关闭 / 无请求级当前用户 / before==after → 跳过（零开销）
       ▼
       ├─ SQLite workspace_edit_ops：一行一 op（seq 严格递增定序；
       │  同一 apply_whole_files 事务共享 op_group_id，一次撤销整组回滚）
       └─ 磁盘快照 <users_root>/<owner_id>/.edit_logs/<project_id>/<op_id>/
            manifest.json（files: path + before/after MD5 + 尺寸）
            <n>.before / <n>.after（全文）
```

- owner 维度放置：不进项目目录、不进下载包、不占项目/衍生配额；
- 单 op 快照总量 > `edit_log_max_op_mb` → 只记索引（`non_undoable=1`），状态接口展示"过大不可撤"；
- 记账失败只打日志不抛错——编辑日志绝不影响写入主流程；
- 逐段检查 / 写作助手工具与 apply-edit / code_review / bib_manager / style_modify 全部经由上述两个写通道，自动获得记账，无需逐模块改造。

### 14.2 撤销 / 重做状态机

```
            undo（校验当前 MD5 == after_md5）
   active ────────────────────────────────▶ undone
      ▲                                       │
      │  redo（校验当前 MD5 == before_md5，      │
      │  且无相关更新的 active op 取代）          │
      └────────────────────────────────────────┘
   failed（防御终态：op 记录的文件已消失，状态接口提示 failed_ops）
```

- LIFO：undo/redo 只作用于 (project, actor) 自己的栈；他人 op 不出现在自己的栈中；
- 冲突即拒绝：组内任一文件当前 MD5 与预期不符 → 409 `file_conflict`，绝不静默覆盖他人修改；MD5 校验失败视为不可撤销但保留 op，待文件回到对应状态后可再撤；
- 配额：恢复导致项目体积增长时先校验配额，超限 413 `quota_exceeded_recover`（op 保持原状态可重试）；
- 恢复写回复用 `apply_whole_files`（乐观锁事务保护）并抑制记账，避免回滚自身被记成新 op；
- redo 候选取"最近一次被撤销"的组（`undone_at` 降序），存在 seq 更大且**相关**（同 undo 组或共享文件路径）的 active op 时视为已被取代——异文件的新编辑不终止本组重放链；同文件冲突仍报 superseded，且即使判定漏放，恢复写回前的 MD5 乐观锁兜底不会静默覆盖；
- 生命周期：每 (project, actor) 保留最近 `edit_log_max_ops` 条（按组整体淘汰、连带快照删除）；维护循环清理超过 `edit_log_ttl_hours` 无活动的整栈；项目永久删除 / 回收站清空级联清理。

### 14.3 与既有机制的边界

| 机制 | 职责边界 |
| --- | --- |
| `BackupManager` 内存栈 | 单用户模式撤销/重做（行为不变）；多用户模式不再暴露 |
| style_modify `OperationMeta` | 模块内提案级撤销（四道防线）保留，与本机制语义一致 |
| 写回配额回滚 | `apply-change` 超限以写前全文经抑制记账的 `apply_whole_files` 恢复（以本次 op 的 after_md5 作版本守卫，检查窗口内他人已写入则放弃覆盖、只撤销本次记账并如实报错）+ `discard_op` 删除本次记账——不经 undo 通道、不污染撤销栈 |
| 乐观锁（MD5） | 单次写冲突防护（409），反向补丁的冲突校验与其同源 |

---

## 15. 多实例状态外置（v2 P7）

v1/v2 前期以单进程为运行前提；P7 把全部跨请求共享的进程内状态外置到共享 SQLite（迁移 v8 建表），使 `docker compose up --scale app=N` 的任意副本可服务任意请求，无需粘性会话。

### 15.1 状态外置总览

```
副本 A ──┐
         ├──▶ 共享 SQLite(WAL, data/)：业务表 + 外置状态表
副本 B ──┤      ├─ workspace_bindings      绑定权威（进程内 LatexProject 为可重建缓存）
         │      ├─ operation_contexts     逐段检查 preview→apply 跨副本
         │      ├─ pending_interactions   工具确认/澄清/DDG challenge + 短间隔轮询
         │      ├─ maintenance_lease      维护循环选主（持租约者执行）
         │      ├─ rate_limit_events / llm_concurrency_leases / sandbox_slots
         │      └─ email_outbox           发送前原子认领（queued→sending）防重复投递
         ├──▶ 共享卷 data/users/ data/staging/（flock 会话互斥锁文件同卷生效）
         └──▶ /var/run/docker.sock（只读）──▶ 沙箱容器
```

### 15.2 关键机制

| 状态 | 外置方式 | 语义要点 |
| --- | --- | --- |
| workspace 绑定 | `workspace_bindings` 表为权威，`bind`/`unbind` 双写；`get_binding` 实时读表并节流刷新 `last_active_at` 打点 | 进程内 miss 时按绑定 root_path 重新 `open` 项目（LatexProject 状态全部可由磁盘重建）；重建失败降级空实例不阻塞请求 |
| CheckOperationContext | payload JSON 入 `operation_contexts`（TTL 1h，维护循环清理 + 读路径惰性过期） | 段落索引键 JSON 往返变 str，反序列化时还原 int；进程 dict 降级为 L1 缓存 |
| 工具确认 / 澄清 | `pending_interactions(kind=confirm/clarify)` + 条件 UPDATE 原子应答；等待侧以 0.5s 轮询替代 asyncio.Event | 应答与 SSE 等待可落在不同副本；双副本竞争同一待定项先到先得；status 列承载原子迁移，避免依赖 JSON 表达式 |
| DDG challenge | 同表 `kind=ddg_challenge`（TTL 300s） | 验证码数据存储与解答提交可分属不同副本 |
| 维护任务重复执行 | `maintenance_lease(key, holder, expires_at)` 选主：每轮开始尝试获取（TTL = 2×循环间隔），持租约者执行循环体，取消时释放 | 非持有者跳过本轮；租约到期自动易主；模块各自 cleanup_loop 幂等无害、保持独立运行 |
| 会话互斥（style_modify/template_gen/plot_gen） | 进程内 asyncio.Lock 叠加 flock 锁文件（会话目录**旁** `<session_id>.lock`，不进目录本身——避免被 zip 打包/扫描误收） | 同机共享卷上跨副本有效；fcntl 缺失平台降级为仅进程内互斥；会话删除/TTL 清理连带删除锁文件 |
| 配置漂移 | config.json mtime+size 指纹热重载（见 §7.1） | `multi_user.enabled` 锚定启动值，变更需重启 |
| 日志 | env `LATEXCELLENCE_LOG_STDOUT=1` 时只输出 stdout/stderr | TimedRotatingFileHandler 多进程共写轮转有 rename 竞争；容器模式交 docker logs 收集，裸机维持单进程文件日志 |

保持进程内不变的状态：LLM client / httpx / Playwright / ToolRegistry / web_cache / 元数据缓存（无用户态或纯缓存，多副本安全）；writing_chat 会话级 asyncio.Lock 仅约束同副本并发，跨副本写冲突由会话文件原子写兜底。

### 15.3 编排形态

仓库根 `docker-compose.yml`：app 服务（healthcheck `/api/health`，`LATEXCELLENCE_LOG_STDOUT=1`，docker.sock 只读挂载，data/config bind 卷）+ 可选 Caddy 反代 profile；副本扩展 `docker compose up -d --scale app=N`。反代要求服务名 DNS 每请求重解析（Caddy 天然支持；nginx 需 upstream 变量 + resolver），SSE 必须关闭缓冲。部署步骤、备份窗口与监控口径见 [docs/deployment.md](docs/deployment.md)。

---

## 16. 模板体系：内置模板与私有模板库

模板体系由两个互补模块构成：`template_gen`（自主 Agent 按描述**生成**新模板，LLM 密集）与 `template_library`（模板的**沉淀与复用**，零 LLM）。本节记录后者的设计决策，模块细节见 [docs/modules/template_library.md](docs/modules/template_library.md)。

### 16.1 定位与双模式

模板库是内置模板与用户私有模板的统一浏览/复用入口（`/api/templates`）。数据面按存放位置天然分成两类，`kind`（builtin/user）由位置判定、不写入 manifest：

- **内置模板**：随代码分发的包内资源（`builtin_templates/<template_id>/`），只读；带 `manifest.json` 元数据与 `assets/` 预编译元资源（缩略图 + PDF 预览）。发现不做启动期缓存，每次请求实时读盘，与配置热重载/模板热更新语义一致。
- **用户模板库**：多用户按用户空间隔离（`<users_root>/<user_id>/template_library/`，目录归属即权限，他人模板统一 404 不泄露存在性）；单用户走全局库目录，保持存量行为。入库通道（上传 zip / 项目另存为 / template_gen 生成结果）共用同一清洗复制 + manifest 生成 + 配额校验管线，元数据优先级为「请求显式提供 > 已有 manifest > 自动回退」。

### 16.2 复用而非新建：安全与配额边界

设计原则是**不引入新的安全面**，全部复用既有基础设施：

- 上传解压复用 `extract_archive` 安全链，解压预算直接取「模板」类别剩余衍生配额，超限即时拒绝；上传原样保存的前提是全包扩展名白名单收窄（LaTeX 相关 + 图形/字体）。
- 模板内文件读取统一走 `path_guard.resolve_project_path()`（拒绝穿越/符号链接/白名单外扩展名），模板 id 经正则校验杜绝路径形态注入；响应与错误消息只携带相对路径（路径脱敏约束同全局规则）。
- 配额双轨：**创建项目**走项目空间配额（预检超限 413，与上传创建同链路落位 + 建行 + 自动初始化 Git）；**模板库本体**占衍生配额并并入既有「模板」类别（templates sub），工作台用量条与自助清理零改动。

### 16.3 创建项目 = 清洗复制（无变量替换）

从模板创建项目刻意保持**纯复制语义**：不做变量替换、不注入用户信息，项目名由用户输入。所有入库/创建路径共用唯一的清洗实现（`copytree` ignore 回调）：剔除 `.git/` 与 LaTeX 编译产物；创建项目额外剔除 `manifest.json` 与内置 `assets/`（库工件不混入新建项目）；生成结果入库额外剔除 `trace.jsonl` 与 PDF 输出。清洗规则只有一份实现，避免多通道口径漂移。

### 16.4 与 template_gen 的闭环

`template_gen` 的会话沙箱是模板库的来源之一：`POST /from-template-gen` 按用户隔离解析会话目录（`userspace.user_resource_root`），清洗后入库为「我的模板」。前端在生成完成态提供「存入模板库」入口（模板名契约必填、默认名兜底，分类可选），成功后引导跳转模板中心——生成（LLM）→ 验证（编译）→ 沉淀（入库）→ 复用（创建项目）形成完整闭环。

---

## 17. 多模型支持：模型档案与角色槽位路由

完整方案与评审决策见 [docs/multi-model.md](docs/multi-model.md)，实现细节见 [docs/backend-core.md](docs/backend-core.md)；本节记录核心设计决策。

### 17.1 决策概览

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 路由模式 | 角色槽位路由（非全局手动切换） | 防止"忘切换导致 OCR 用了非视觉模型"；用户零操作 |
| 配置结构 | 顶层字段保留 + `profiles` 档案列表 + `assignments` 指派表 | 旧配置零迁移成本；"只配一个模型"的用户无需理解档案概念 |
| 能力校验 | 硬校验（启动加载与 `/api/config` 保存同门禁，非法配置拒绝且旧配置不覆盖） | 错误在配置期暴露，而非运行期失败；`vision` 参与校验（OCR 槽必需），`reasoning` 为提示性标注 |
| 槽位缺失 | 两级解析：槽位指派 → `default` 槽 → 内置档案 | 功能永不中断 |
| 旧配置兼容 | `profiles` 为空时读取侧内存合成内置档案（`__default__`），不回写文件 | 存量用户零感知 |
| 推理模型兼容 | 封装层统一适配（参数类 4xx 降参重试一次 + `<think>` 剥离），service 无感知 | 不依赖 JSON 重试兜底，重试仍只服务其原有职责 |
| 节流语义 | 每档案独立 Semaphore 与请求间隔 | 慢速 API 与快速 API 互不拖累 |

### 17.2 请求路由路径

```
service ──▶ get_client_for(role)
              │ 两级解析：assignments[role] → assignments["default"] → 内置档案
              ▼
         客户端池 {profile_id: LLMClient}
         （双检锁惰性创建；指纹 base_url/api_key/model 变化时惰性重建）
              ▼
         LLMClient._resolve_cfg() 每请求实时合并「顶层字段 + 档案覆盖项」
              ▼
         AsyncOpenAI 调用（封装层：参数 4xx 降参重试 / <think> 剥离 /
         流式 reasoning_content 仅透传展示）
```

配置热重载经 `reset_client_pool()` 清空整个客户端池，是配置失效的主路径；`get_llm_client()` 保留为 `get_client_for("default")` 薄别名，存量测试与 default 槽辅助调用零改动。槽位与档案结构由前端「模型管理」设置分区编辑，各功能入口的模型徽标经 `GET /api/llm/routes` 展示两级解析结果（不含密钥；多用户模式已登录即可读，模型为服务端全局只读配置）。
