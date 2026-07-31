# LateXcellence — 设计文档

本文档阐述 LateXcellence 的架构设计、核心算法和关键技术决策。

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
│  ├─ code_review/              ├─ workspace_manager.py     │
│  ├─ terminology_check/       ├─ project_manager.py       │
│  ├─ compile/                 ├─ tool_registry.py         │
│  ├─ writing_chat/            └─ base_paragraph_check.py  │
│  ├─ bib_manager/            utils/                       │
│  ├─ ... (多个模块)        ├─ json_utils.py             │
│  └─ project_router.py       ├─ file_ops.py               │
│                             ├─ input_resolver.py          │
│                             ├─ time_utils.py              │
│  ┌──────────┐               ├─ latex_parser.py            │
│  │ router.py │→ service.py  ├─ web_search.py              │
│  │  (薄层)   │  (厚层)      ├─ search_router.py           │
│  └──────────┘               └─ (按需) Marker 替换/还原    │
│  └──────────┘                                            │
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
| LaTeX 项目通常拆分为多个 \input 文件 | `input_resolver.py` 递归展开，按文件分组结果 |
| 多浏览器同时处理不同项目 | `workspace_manager.py` 隔离各工作区 LatexProject 实例 |
| 联网搜索无需 API Key | DuckDuckGo HTML 引擎 + 验证码转发；SearXNG 自建实例 JSON API；工具通过 `ToolRegistry` 注册，LLM 自动调用 |

### 1.3 核心架构组件：BaseParagraphCheckService

四个段落处理模块（typo_check、expression_opt、code_review、terminology_check）共享相同的业务流程：文件解析 → marker 替换 → LLM 编排 → 结果写回。为避免重复代码，抽取 `BaseParagraphCheckService` 基类：

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

8 步顺序执行，每步替换一类 LaTeX 结构。所有前缀共享同一个全局计数器，每次 `strip_to_plain_text()` 重置为 0。

**关键约束**：注释替换（第 3 步）在环境/数学/命令（第 4-7 步）之前，确保注释内的 `\command{}`、`$math$`、`\begin{env}` 不被单独提取。逐字类环境（第 2 步）在注释之前，因其内部可含字面 `%`。

| 步骤 | 目标 | 替换为 |
| --- | --- | --- |
| 1. `_replace_verb_inline()` | `\verb\|...\|` | `__VERB_NNNN__` |
| 2. `_replace_protected_envs(envs=_VERBATIM_ENVS)` | 逐字类环境 `verbatim`/`lstlisting`（可含 `%`） | `__ENV_NNNN__` |
| 3. `_replace_comments()` | `%` 注释 | `__COM_NNNN__` |
| 4. `_replace_protected_envs()` | `table`/`equation`/`tikzpicture` 等环境 | `__ENV_NNNN__` |
| 5. `_replace_display_math()` | `$$...$$` / `\[...\]` | `__MATH_NNNN__` |
| 6. `_replace_inline_math()` | `$...$` / `\(...\)` | `__MATH_NNNN__` |
| 7. `_replace_commands()` | 所有 `\command` | `__CMD_NNNN__` |
| 8. `_normalize_whitespace()` | 连续空行 | 压缩为 2 行 |

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

还原时必须按 marker **长度降序**排列，防止短 marker 误匹配长 marker 的前缀：

```python
# 按 marker 长度降序排列
for marker, original in sorted(
    marker_map.items(), key=lambda x: (-len(x[0]), x[0])
):
    result = result.replace(marker, original)
```

如果没有这个排序，`__CMD_001__` 会错误地匹配 `__CMD_0010__` 的前 10 个字符。

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

## 5. 数据流详解

### 5.1 错字查找数据流

```
┌──────────┐    ┌──────────────┐    ┌────────────┐    ┌──────────┐
│ 前端请求  │ →  │ router.py    │ →  │ service.py │ →  │ LLM API  │
│ /preview  │    │ 参数校验     │    │ 编排逻辑   │    │          │
└──────────┘    └──────────────┘    └────────────┘    └──────────┘
                      │                   │
                      ▼                   ▼
              ┌──────────────┐    ┌────────────┐
              │ project_mgr  │    │ latex_parser│
              │ 段落提取     │    │ marker 替换 │
              └──────────────┘    └────────────┘
                                         │
                                         ▼
                                   ┌──────────────────────┐
                                   │ CheckOperationContext │
                                   │ (operation_id + TTL)  │
                                   └──────────────────────┘
```

**preview 阶段**：生成 `operation_id` → 提取段落 → 替换为 marker → 深拷贝 marker_map 到 `CheckOperationContext` → 返回纯文本 + operation_id

**check 阶段**：前端回传 operation_id → 查找对应上下文 → 发送纯文本给 LLM → 校验输出 → 返回结果

**apply 阶段**：前端回传 operation_id → 查找对应上下文 → 从上下文取 marker_map 还原 marker → MD5 校验 → 原子写入

### 5.2 源码格式化的数据流差异

源码格式化**不使用 marker 保护**，因为 LLM 需要直接修改 LaTeX 结构：

```
typo_check / expression_opt:
  LLM 输入 = 纯文本（含 marker）
  LLM 输出 = 修正后纯文本（含 marker）
  apply = restore_from_markers() → 写入

code_review:
  LLM 输入 = 工具格式化后的含 marker 纯文本
  LLM 输出 = 审查建议列表（含 original / suggested 片段）
  apply = 直接写入工具格式化结果（原始 LaTeX），建议仅展示
```

### 5.3 术语一致性检查的数据流

术语一致性检查使用**两阶段 LLM 调用**——提取 + 语义聚类——而非固定算法：

```
Phase 1: 逐段提取（N 次 LLM 调用，可并发）
  LLM 输入 = 纯文本（含 marker）
  LLM 输出 = 该段的关键术语列表

Phase 2: 语义聚类（1 次 LLM 调用）
  程序去重 → LLM 接收去重后的术语列表
  LLM 输出 = 语义相同的术语组 + 每组推荐项
  例: [{"recommended": "深度学习", "variants": ["Deep Learning", "deep learning", "DL"]}]

Phase 3: 程序生成修正结果
  构建 variant → recommended 替换映射
  按段落生成 corrected 文本 + changes 列表
```

关键设计决策：使用 LLM 进行语义聚类（而非正则/启发式），因为术语一致性本质上是语义问题——"Deep Learning"、"deep learning"、"深度学习"、"DL"在文本层面完全不同，但语义上是同一概念。固定算法无法可靠判断跨语言同义词和缩写等价关系。

### 5.4 写作助手聊天的数据流

写作助手 Chat 采用**双轮协议 + Function Calling**，每轮走不同路径：

```
RS1（流式对话 + Function Calling）:
   用户消息 + 文件上下文（原始 LaTeX，无 marker）
        ↓
   _get_enabled_tools() 动态注入工具：
       SearchManager    → web_search
       ResearchManager  → search_scholarly_works / find_related_works / ...
       ProjectToolsManager → list_files / read_file_content / ...
       EditToolsManager → edit_paragraph / undo_last_edit
        ↓ SSE fetch streaming
   chat_stream_with_usage(tools=...) → LLM 流式回复
        ↓
   LLM 可能返回 tool_call（非流式检测）
        ↓
   ┌─ 工具调用循环（最多 3 轮）───────────────┐
   │ ToolRegistry.execute(tool_name, args)   │
   │    ↓                                     │
   │ 敏感检查（edit_paragraph / undo_last_edit）│
   │   ↓ SSE: tool_pending                    │
   │   用户确认/取消（超时 120s）               │
   │   ↓ SSE: tool_cancelled 或 继续执行       │
   │ 结果回传 LLM                              │
   │    ↓                                     │
   │ LLM 继续生成回复或发起新的 tool_call       │
   └──────────────────────────────────────────┘
        ↓
   工具消息通过 append_tool_messages() 持久化
        ↓
   parseThinking() 逐 token 解析 <thinking> 标签
        ↓ streaming 结束
   落盘 assistant 消息（含 thinkingContent / displayContent / tokens）
   
RS2（结构化编辑）:
   用户点击"生成编辑建议" → instruction 发送
        ↓
   原始 LaTeX 直接发送给 LLM
        ↓ retry_with_validation() → LLM 结构化 JSON
   校验完整 LaTeX → 构造 unified diff → 附挂 EditOp
        ↓
   用户点击"应用到 .tex"（可选：手动编辑后传递 new_latex）
        ↓ project_manager.apply_change()
   MD5 校验 → 原子写入
```

**深度思考（Thinking Block）流式解析**：

LLM 输出中可能包含 `<thinking>...</thinking>` 标签，包裹推理过程。前端在流式接收时：
1. 每收到一个 token chunk，对累积的 rawText 执行 `parseThinking()` 正则匹配
2. `<thinking>` 已出现但 `</thinking>` 未到 → `thinkingOpen=true`，UI 显示旋转 spinner + Lightbulb 图标
3. `</thinking>` 到达 → `thinkingClosed=true`，`<details>` 元素自动折叠，正文部分提取为 `displayContent`
4. 页面刷新时，`processLoadedMessages()` 从存储的 `content` 字段重新提取，通过 `setMessages` 触发 Zustand 更新

**助手提示词注入**：

激活助手的提示词内容通过 `ChatSendRequest.assistant_prompt` 传递，后端在构建 LLM messages 时注入 **system prompt**，而非 user message。这样：
- 助手提示词对用户不可见（不显示在聊天 UI 中）
- 可随时切换不同助手角色而不污染对话历史

**上下文压缩**：

与 typo_check 等单向模块不同，写作助手是有状态的多轮对话。`cumulative_tokens` 跟踪最新一次 LLM 调用的 `total_tokens`（上下文窗口大小），当达到阈值时触发压缩：
1. LLM 对历史消息生成纯文本摘要（直接 `llm.chat()`）
2. 原始消息深拷贝到 `summary.snapshot` 字段
3. 用一条 `role="summary"` 消息替换全部历史
4. 用户可通过"删除压缩消息"一键还原

**Function Calling 工具调用（联网搜索 + 学术研究 + 项目浏览 + 段落编辑）**：

LLM 在流式输出过程中可发起 tool_call，触发各类工具调用。支持自动化工具调度和敏感操作确认：

```
RS1 流式对话中 tool_call:
  LLM 输出 tool_call（function.name="web_search" / "edit_paragraph" 等）
       ↓ SSE event: tool_call（前端停止打字机显示）
  ToolRegistry.execute(tool_name, arguments)
       ↓
  ┌─ 工具分发 ───────────────────────────────────┐
  │ SearchManager   → web_search (联网搜索)       │
  │ ResearchManager → OpenAlex 检索/详情/引文追踪 │
  │ ProjectToolsManager → list_files / ... (浏览) │
  │ EditToolsManager → edit_paragraph / ... (编辑) │
  └──────────────────────────────────────────────┘
       ↓ 搜索工具走引擎路由
  ┌─ 引擎选择（根据配置）────────────────────────────┐
  │ DDG: https POST https://html.duckduckgo.com/html/  │
  │ SearXNG: httpx GET {base_url}/search?format=json    │
  └────────────────────────────────────────────────────┘
       ↓
  ┌─ DDG 路径 ────────────────────────────────────────┐
  │ 二次验证（HTTP 202 + captcha_challenge）           │
  │   → SSE event: captcha_challenge                   │
  │   → 前端 CaptchaDialog → POST /api/search/ddg-challenge  │
  │   → done(captcha_pending=true)，用户需重新发送     │
  └───────────────────────────────────────────────────┘
       ↓ 正常结果
  ┌─ SearXNG 路径（无验证码） ───────────────────────┐
  │ 直接解析 JSON 结果                                 │
  └───────────────────────────────────────────────────┘
       ↓
  ┌─ 敏感操作检查（edit_paragraph / undo_last_edit）─┐
  │ require_confirmation=true → SSE: tool_pending     │
  │   → 前端确认弹窗 → confirm-tool / cancel-tool     │
  │   用户确认后继续执行，取消后回传 LLM "用户跳过"   │
  └──────────────────────────────────────────────────┘
       ↓
  提取结果 → SSE event: tool_result / search_results / scholarly_results
  → 工具结果注入 LLM 继续对话（SSE event: message）
  → 工具消息通过 append_tool_messages() 持久化到上下文
  → 前端显示结果引用
```

`ResearchManager` 将 OpenAlex work 映射为统一 `ScholarlyWork`，按 DOI、OpenAlex ID、arXiv ID 或标题年份去重。作者姓名先解析为 Author ID；参考文献和相似作品按 OpenAlex 每批 100 个 ID 的限制批量获取，极端情况下最多检查前 500 条候选，避免逐篇请求和无界开销。所有请求共享 FastAPI lifespan 管理的 `httpx.AsyncClient`。当前全文工具只返回 OpenAlex 摘要和 OA 定位，不抓取第三方全文。外部元数据视为不可信内容，system prompt 明确禁止执行其中包含的指令。

### 5.5 参考文献管理数据流

参考文献管理采用**四步独立请求**模式，无 SSE 流式，无 Marker 保护，无段落分割：

```
Step 1: 格式识别
  用户输入参考文献示例
       ↓ POST /analyze-format
  retry_with_validation(ReferenceFormatResult)
       ↓
  返回格式名称、entry_type、required_fields、optional_fields、confidence、raw_observed_fields
  （置信度为 low 时前端弹出 toast.warning）

Step 2: 元数据提取
  用户输入文本或截图
       ↓ POST /extract（FormData）
  retry_with_validation(ExtractedMetadata)
       ↓
  _check_missing_fields(fields, format_info)
  只报告 raw_observed_fields 中同时出现的必填字段缺失
       ↓
  返回 {"fields": {...}, "missing_fields": [...]}
  前端根据 fields 的 key 动态渲染编辑表单（非写死字段列表）

Step 3: BibTeX 生成
  前端将编辑后的 fields dict 发送
       ↓ POST /generate
  retry_with_validation(BibEntry)
       ↓
  返回 {cite_key, entry_type, fields, raw_bibtex}
  前端渲染代码区，用户可编辑 cite_key 和 raw_bibtex 后复制或保存

Step 4: 写入 .bib 文件
  用户选择项目中的 .bib 文件
       ↓ POST /save
  safe_read → 检查 cite_key 是否存在 → 追加或替换 → atomic_write
       ↓
  返回 {status, action, cite_key}
```

**关键差异（与其他模块对比）**：

| 特性 | typo_check / writing_chat | bib_manager |
| --- | --- | --- |
| 交互模式 | 逐段 + SSE 流式 | 四步逐步提交 |
| Marker 保护 | 必须 | 不需要（不操作 .tex 文件） |
| 元数据字段 | 固定 Pydantic schema | 动态：由格式分析阶段决定 |
| LLM 调用次数 | 每段落 N 次 | 可重叠：分析 1 + 提取 1 + 生成 1 共 3 次 |
| 格式持久化 | 无 | 格式 JSON 文件 CRUD |

**动态字段设计**：`ExtractedMetadata` 不使用固定字段（如 `title: str`、`authors: list[str]`），而是使用 `fields: dict[str, str]` 的 key-value 结构。key 由格式分析阶段的 `ReferenceFormatResult.required_fields` + `optional_fields` 动态决定。这避免了格式差异导致的字段映射问题——GB/T 7714 可能要求 `language` 和 `document_type_marker`，APA 则不需要，但都可以用同一个模型表达。

**缺失字段智能检测**：利用 `raw_observed_fields`（格式分析时 LLM 提取的"输入中实际出现的字段列表"）来减少误报。例如输入示例为 `Devlin, Jacob.BERT[J].arXiv,2018.`，其中明显不含 abstract、keywords 等字段，`raw_observed_fields` 不会包含它们，`_check_missing_fields` 就不会报告它们缺失。

### 5.6 自动编译数据流

编译模块与 LLM 模块不同——LLM 不参与文本修改，仅翻译编译错误：

```
┌──────────┐    ┌──────────────┐    ┌────────────┐    ┌──────────────┐
│ 前端请求  │ →  │ router.py    │ →  │ service.py │ →  │ latexmk 进程  │
│ /run      │    │ 参数校验     │    │ subprocess │    │              │
└──────────┘    └──────────────┘    └────────────┘    └──────────────┘
                                          │
                                          ▼ returncode != 0
                                    ┌────────────┐    ┌──────────┐
                                    │ 错误提取    │ →  │ LLM API  │
                                    │ (正则筛选)  │    │ 翻译错误  │
                                    └────────────┘    └──────────┘
```

关键差异：
- 使用 `asyncio.create_subprocess_exec` + `wait_for`（带超时）管理外部进程
- LLM 仅在编译失败时被调用，且使用 `retry_with_validation()` 保证输出格式
- 不涉及 Marker 保护、段落提取或源文件修改

### 5.7 多文件 \input 展开数据流

四个段落处理模块（typo_check、expression_opt、code_review、terminology_check）共享相同的多文件展开机制：

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

### 5.8 模板生成数据流

模板生成从零创建 LaTeX 模板，不操作已有 `.tex` 文件，不走 SSE 流式：

```
POST /api/template-gen/generate { description: "..." }
        ↓
retry_with_validation(TemplateGenResult)
  一次 LLM 调用（system prompt = 模板设计师）
        ↓
返回 { template_name, preamble, body_structure, packages, explanation }
        ↓ (可选保存)
POST /api/template-gen/save  →  组装完整 .tex → 写入 output_dir
```

**关键差异**：不使用 Marker 保护（生成全新模板，无 LaTeX 结构需保护）；一次 LLM 调用完成，非批量段落模式。

### 5.9 公式 OCR 数据流

公式 OCR 接收图片，经 Vision API 提取 LaTeX 公式代码：

```
POST /api/formula-ocr/recognize (FormData: UploadFile)
        ↓
_validate_content_type() → _preprocess() → _to_base64()
  校验 MIME / 缩放超2000px边 / 转 JPEG base64
        ↓
retry_with_validation(FormulaOCRResult)
  Vision API（system prompt = 公式 OCR 专家，temperature=0.1）
        ↓
返回 { latex, inline, confidence }
  confidence < 0.7 时附加 low_confidence: true
```

**关键差异**：无 SSE 流式、无 Marker 保护；通过 `UploadFile` 接收图片而非 LaTeX 源码；`max_image_size_mb` 由 `config.json` 控制。

### 5.10 表格 OCR 数据流

表格 OCR 与公式 OCR 共享相同的图片预处理流水线，区别在于 LLM 输出完整 `table` 浮动体：

```
POST /api/table-ocr/recognize (FormData: UploadFile)
        ↓
_validate_content_type() → _preprocess() → _to_base64()
  与 formula_ocr 完全一致的预处理逻辑
        ↓
retry_with_validation(TableOCRResult)
  Vision API（要求输出 \begin{table}[htbp]...\end{table} 和三线表样式，temperature=0.1）
        ↓
返回 { latex, confidence }
  confidence < 0.7 时附加 low_confidence: true
```

**关键差异**：与 formula_ocr 相同的图片处理流程，但 LLM 输出约束不同——要求完整 table 浮动体、`booktabs` 三线表样式、支持合并单元格。

### 5.11 Markdown→LaTeX 转换数据流

本模块不调用 LLM，完全依赖外部 Pandoc 进程：

```
POST /api/format-converter/convert { markdown: "..." }
        ↓
_check_pandoc() → shutil.which("pandoc")
  未安装则抛 RuntimeError
        ↓
asyncio.to_thread(subprocess.run)
  pandoc -f markdown -t latex --wrap=preserve（超时 30s）
        ↓
返回 { latex: "..." }
```

**关键差异**：零 LLM 调用；通过 `asyncio.to_thread()` 将同步子进程丢入线程池避免阻塞事件循环；不涉及 Marker 保护、段落分割或文件写入。

### 5.12 样式修改数据流

样式修改让用户通过自然语言描述修改 LaTeX 样式（如改列对齐、调整间距），LLM **直接操作原始 LaTeX 结构**，不使用 Marker 保护：

```
POST /api/style-modify/modify { latex: "...", description: "..." }
        ↓
retry_with_validation(StyleModifyResult)
  一次 LLM 调用（要求返回完整 LaTeX 代码，temperature=0.3）
        ↓
空结果回退为原始代码
        ↓
返回 { latex: "..." }
```

**关键差异**：不使用 Marker 保护（设计要求 LLM 直接操作 LaTeX 结构，如改 `\begin{tabular}{lcr}` 为 `\begin{tabular}{ccc}`）；无 SSE 流式；单次请求-响应。

### 5.13 Git 差异高亮数据流

Git 差异模块生成两个 Git revision 间的差异高亮 PDF：

```
GET /api/git-diff/refs
        ↓
list_git_revisions() → 分支/标签/commit 列表 → 返回 GitRefsResult
```

```
POST /api/git-diff/render-pdf { main_file, base_ref, target_ref }
        ↓
_flatten_main_document()（递归展开 \input/\include/\subfile）
  通过 git show 或工作目录读取各 revision 的文件内容
        ↓
difflib.SequenceMatcher 逐行差异对比
  增行 → \LXDiffAdded（绿色底纹），删行 → \LXDiffDeleted（红色底纹）
        ↓
git archive → 复制 target revision 到临时目录
        ↓
latexmk -xelatex 编译差异高亮文档
        ↓
返回 { pdf_url, job_id }
        ↓
GET /api/git-diff/pdf/{job_id} → FileResponse 内联预览
```

**关键差异**：差异对比阶段零 LLM 调用，纯算法通过 `difflib.SequenceMatcher` 生成；生成 PDF 涉及完整 LaTeX 编译链；通过 `git show` 和 `git archive` 读取历史版本文件。当编译失败时，会调用 `compile_error_translator.translate_compile_errors()` 将错误日志翻译为中文解释和修复建议（复用 compile 模块的 LLM 能力），因此在失败场景下会涉及 LLM。

---

## 6. 前端架构

### 6.1 状态管理策略

采用 Zustand 5 + `persist` 中间件：

- **持久化字段**：项目路径、活动文件、各模块的段落和结果、`writingChatCurrentSessionId`、`workspaceId`、`rootPath`、`mainFile`、`currentTheme`
- **非持久化字段**：文件树、PDF URL、加载状态、`writingChatMessages`、`writingChatCompressed`

刷新页面后，项目身份和检查结果自动恢复，无需重新加载。

### 6.2 useParagraphCheck hook

四个页面（TypoCheck、ExpressionOpt、CodeReview、TerminologyCheck）共享相同的段落检查流程。通过 `useParagraphCheck` hook 抽象：

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

---

## 7. 配置管理

### 7.1 热重载机制

```python
# 全局缓存
_config: Optional[AppConfig] = None
_config_path: Optional[Path] = None
_api_key_from_env: bool = False  # 标记 API Key 是否来自环境变量，防止写回磁盘

def get_config() -> AppConfig:
    if _config is None:
        raise RuntimeError("Config not loaded")
    return _config
```

所有方法内实时调用 `get_config()`，不在 `__init__` 中缓存配置对象。这样前端修改配置后，后端立即生效。

### 7.2 安全策略

- 环境变量 `LX_LLM_KEY` 优先级高于配置文件
- 前端提交的 `api_key` 以 `***` 开头时保留原值（掩码保护）
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
| Marker 还原 | 长度降序排序 | 防止短 marker 误匹配长 marker 前缀 |
| 编译错误翻译 | 外部进程 + LLM | 正则提取关键错误，LLM 翻译为中文解释，不修改源文件 |
| 写作助手 Marker 策略 | 分层处理 | 纯对话无 marker（方便 LLM 理解论文）；编辑操作走 marker pipeline（保护 LaTeX 结构） |
| 深度思考渲染 | `<details>` + parseThinking | 流式解析 `<thinking>` 标签，实时展开/折叠；页面刷新时 `processLoadedMessages` 恢复 |
| 助手提示词注入 | system prompt 注入 | 不污染用户消息体，用户不可见，可随时切换角色 |
| 参考文献元数据结构 | 动态 dict（非固定 Pydantic 字段） | 不同格式（GB/T、APA、IEEE）字段集不同，固定模型无法覆盖；`fields: dict[str, str]` 避免字段映射 |
| 缺失字段检测 | 基于 `raw_observed_fields` 过滤 | 只报告输入中实际出现的必填字段缺失，不报输入中没有的字段（如 abstract），减少误报 |
| 格式配置持久化 | JSON 文件，文件名由 `format_name` 自动生成 | 无需用户手动命名；合法字符替换为 `-`；支持 CRUD 加载复用 |
| 联网搜索引擎 | DuckDuckGo HTML + SearXNG JSON API | DDG 免费无 API Key，HTTP 202 验证码转发；SearXNG 自建实例通过 `/search?format=json` 获取结果，无验证码；工具通过 ToolRegistry 注册，LLM 可自动调用；前端设置页面可切换引擎 |
