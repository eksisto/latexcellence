# LaTeX 样式修改模块 `style_modify/`

`style_modify` 提供两种模式：

1. 粘贴代码模式：用户粘贴 LaTeX 源码和自然语言描述，后端通过单次 LLM 调用返回完整修改结果，不写源文件。
2. 项目 Agent 模式：用户直接对当前打开的项目提出样式需求，Agent 浏览项目、搜索样式定义、将候选修改写入会话 overlay，在 shadow 项目编译后生成 proposal。用户确认后以批量事务写回真实项目，并支持安全撤销。

两种模式都不使用 Marker 保护。样式修改需要 LLM 理解真实 LaTeX 结构，因此 Agent 读取和修改的是原始 LaTeX 文件内容。

## 粘贴代码模式

LaTeX 不放在 JSON 字符串中传输，采用与 OCR 模块一致的分隔符协议：

```text
<<<LATEX>>>
<complete modified LaTeX code>
<<<END>>>
```

后端提取后用 `latex_json_guard.py` 的 `validate_latex_field(relaxed=True)` 校验，失败时回喂 LLM 重试。响应结构保持为 `{ "latex": "..." }`。

## 项目 Agent 模式

### 总体流程

```text
用户需求
  -> 创建 session
  -> Agent 只读真实项目
  -> stage_replace 写 session overlay
  -> 构建 shadow 项目并编译
  -> finish 生成 proposal revision
  -> 用户审核 diff
  -> apply 校验基线并批量事务写回
  -> operation 支持安全撤销
```

真实项目在用户确认前不会被修改。Agent 的私有工具不注册到全局 `ToolRegistry`，每个工具只绑定当前 workspace、项目根目录和 session。文件工具复用 `backend/utils/agent_file_tools.py`；共享层通过名称映射保留现有的 `stage_replace` / `read_project_file` / `list_project_files` 名称，模块仍负责 overlay 和路径/内容校验。

### 会话目录

会话目录位于应用缓存目录，不放入用户项目。默认根目录为 `data/style_modify_sessions/`。

```text
data/style_modify_sessions/<session_id>/
├── meta.json       # 会话状态、proposal、消息历史
├── baseline/       # 首次候选修改时冻结的原始文件
├── overlay/        # 当前候选文件
├── shadow/         # shadow 编译项目，可重建
└── trace.jsonl     # Agent 运行轨迹
```

操作备份保存在 `data/style_modify_sessions/operations/<operation_id>/`，包含 `before/`、`applied/` 和 `meta.json`。

### 私有工具

| 工具 | 作用 | 约束 |
| --- | --- | --- |
| `list_project_files` | 列出项目内允许后缀文件 | 返回相对路径和大小 |
| `search_project_text` | 搜索命令、环境或文本 | 限制结果数、单行长度和总输出 |
| `read_project_file` | 读取文件或指定行区间 | 使用 `line_range: [start, end]`（1-based 闭区间）；每次重新执行路径保护、大小和编码限制 |
| `stage_replace` | 将完整候选内容写入 overlay | 只允许修改已存在文件，校验基线和 LaTeX 内容 |
| `discard_file_changes` | 丢弃某文件候选修改 | 只影响当前 session overlay |
| `compile_staged_project` | 在 shadow 项目编译候选版本 | 复用 compile service 安全参数 |
| `finish` | 结束推理并生成 proposal | 必须提供摘要，附带编译状态 |

`stage_replace` 使用完整文件内容而不是 patch 字符串。后端根据 baseline 与 overlay 生成 unified diff，避免 Agent 输出的 patch 上下文歧义。

### 文件与内容校验

所有项目相对路径必须通过 `resolve_project_path()` 解析。默认允许后缀为 `.tex`、`.sty`、`.cls`、`.bib`，禁止符号链接、路径穿越、绝对路径、二进制文件和不可按 UTF-8 解码的文件。

候选内容进入 overlay 前调用 `validate_latex_field(relaxed=True)`；`.bib` 使用 `bibtex=True`。Function Calling JSON 参数中的 LaTeX 反斜杠要求按 JSON 规则双写，后端校验失败时把错误返回 Agent 修正。

### Shadow 编译

`compile_staged_project` 从当前项目创建受限 shadow 副本，跳过缓存、版本控制元数据、构建产物和会话目录，用当前 overlay 覆盖对应文件，然后在 shadow 内调用 `run_tex_compile()`。编译状态区分 `passed`、`failed`、`not_run`，界面不得把未执行编译伪装为成功。

`finish` 前会校验 overlay 与最后一次编译（成功或失败）时的快照是否一致：只要最后一次编译后又发生 `stage_replace` / `discard_file_changes`，Agent 就拒绝 finish 并要求重新编译，避免交付与编译结果不一致的候选方案。快照按 staged 文件 MD5 计算，成功编译后持久化到会话 meta（`last_verified_md5s`），因此跨 refine 轮次同样生效；工具链缺失（`not_run`）时不以此快照拦截 finish。

### Proposal 与事务写回

`finish` 生成不可变 proposal revision，包含需求、变更说明、逐文件 baseline MD5、candidate MD5、unified diff、编译摘要和可应用状态。前端 refinement 只能基于当前 revision 发起；应用前检查 workspace、项目指纹、最新 revision、路径、真实文件 MD5 和 LaTeX 内容。

写回通过 `LatexProject.apply_whole_files()` 完成：

- 一次锁定整组文件
- 写入前为所有目标建立操作备份
- 使用临时文件和原子替换更新文件
- 任一文件失败时自动恢复已更新文件
- 成功后可基于 applied MD5 校验整组撤销

撤销前验证每个真实文件仍等于 applied MD5。只要任一文件在应用后被修改，整组撤销返回冲突，不覆盖较新的内容。

## API 路由

Prefix: `/api/style-modify`, Tag: `style_modify`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/modify` | 粘贴代码模式，行为不变 |
| POST | `/agent/run` | 创建 session 并通过 SSE 执行 Agent |
| POST | `/agent/sessions/{session_id}/refine` | 基于最新 proposal 追加要求并流式生成新 revision |
| GET | `/agent/sessions/{session_id}` | 恢复会话和最新 proposal |
| GET | `/agent/sessions/{session_id}/trace` | 获取结构化执行轨迹（含完整工具参数与结果） |
| GET | `/agent/sessions/{session_id}/trace/download` | 下载完整可公开轨迹 |
| POST | `/agent/sessions/{session_id}/apply` | 提交指定 revision 的完整 proposal |
| POST | `/agent/operations/{operation_id}/undo` | 安全撤销已应用操作 |
| DELETE | `/agent/sessions/{session_id}` | 删除未运行中的 session 和临时文件 |

SSE 事件类型包括 `session`、`plan`、`tool_call`、`tool_result`、`file_staged`、`compile`、`proposal`、`warning`、`done`、`error`。SSE 不发送完整文件内容或未截断编译日志。

## 配置

`config.json` 中 `features.style_modify.agent` 控制项目 Agent 模式：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用项目 Agent 模式 |
| `sessions_dir` | `data/style_modify_sessions/` | 会话缓存目录 |
| `max_rounds` | `20` | Agent 最大轮数 |
| `max_tool_calls` | `100` | 单会话工具调用上限 |
| `max_compile_runs` | `10` | 单会话 shadow 编译次数上限 |
| `max_file_size_kb` | `1024` | 单个候选文件大小上限 |
| `max_session_size_kb` | `10240` | 单会话候选总量上限 |
| `max_tool_output_chars` | `12000` | 工具输出回传 LLM 的字符上限 |
| `shadow_compile_timeout` | `120` | shadow 编译超时（秒） |
| `allowed_suffixes` | `.tex/.sty/.cls/.bib` | 允许修改的文件后缀 |
| `cleanup_enabled` | `true` | 是否启用过期会话清理 |
| `cleanup_interval_seconds` | `3600` | 清理检查间隔 |
| `cleanup_retention_days` | `7` | 会话保留天数 |

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `modify(latex, description)` | 粘贴代码模式：分隔符协议 + LLM 修改 + 内容校验 |
| `agent_run(...)` | 创建 Agent session 并驱动私有工具循环 |
| `agent_refine(...)` | 基于最新 proposal 追加要求 |
| `get_agent_session(...)` / `get_agent_trace(...)` | 恢复会话和轨迹 |
| `apply_agent_proposal(...)` | 校验基线并批量事务写回 |
| `undo_agent_operation(...)` | 校验 applied MD5 后批量撤销 |
| `delete_agent_session(...)` | 删除未运行中的会话 |

## 维护者工具

`tools/stylemodify_opt/` 提供 style_modify 项目 Agent 的离线优化闭环：构造 5 个
可复现的样式修改场景，驱动 Agent 生成 proposal 并采集需求遵守、内容保留、
编译状态、事务应用/撤销与轮次/时长指标，支持 trace 分析与多版本对比。详见
`tools/stylemodify_opt/README.md` 与 `tools/stylemodify_opt/report/optimization_report.md`。
