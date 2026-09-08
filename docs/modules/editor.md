# 网页编辑器模块 `editor/` + `/editor`

> 设计依据：[docs/editor-design.md](../editor-design.md)（含 grilling 决策记录
> Q1–Q26）、[docs/editor-productivity-design.md](../editor-productivity-design.md)
> （editor v2，八个阶段均已实施：阶段一多缓冲区基座、阶段二编辑基本功包、
> 阶段三大纲面板、阶段四引用补全、阶段五一键格式化、阶段六 KaTeX 公式
> 悬停预览、阶段七编译错误列表与跨文件跳转，以及阶段八修改行色条 /
> 状态栏 / 选中高亮匹配 / 会话持久化升级 localStorage，借鉴 Notepad++）
> 与 [docs/editor-experience-design.md](../editor-experience-design.md)
> （editor v3 写作体验收口；阶段一 SyncTeX 正反向同步、阶段二用户自定义
> 片段库、阶段三实时语法诊断、阶段四跨文件大纲与公式固定预览卡、
> 阶段五助手接入联网与学术检索（5a 工具接入 + 5b 确认流）及阶段六
> 自研 Lezer 子集语法（6a P0 高亮 + 6b 折叠、环境缩进与括号匹配
> 精确化）已实施）、[docs/editor-file-upload-design.md](../editor-file-upload-design.md)
> （文件树上传任意格式资源，决策 D1–D12，已实施）、
> [docs/editor-file-move-design.md](../editor-file-move-design.md)
> （文件树拖拽移动 / 目录组织，决策 Q1–Q13，已实施）。

## 设计概述

网页编辑器提供工作台之外的第二内容入口：全宽三栏布局的 `/editor` 页，
支持项目内文本文件的**多缓冲区（多标签页）**编辑与 MD5 乐观锁保存、LaTeX
命令快速输入（中文 / 拼音全拼 / 首字母 / 英文四路模糊查找）、编译联动与
编辑器侧 AI 助手；文件树点击 PDF
以只读覆盖层内嵌预览。后端由
`backend/modules/editor/`（整文件读写服务）与 `writing_chat` 包内的
编辑器助手端点两部分组成。

### 复用边界（决策 Q22）

| 处理 | 内容 |
| --- | --- |
| 直接复用 | `ConversationContext` 会话持久化（磁盘存储 / token 计数 / 上下文压缩）、SSE 流式协议、`ToolRegistry` 只读项目工具（list_files / read_file_content / search_across_files）、提示词集中管理、多用户 LLM 治理（共享 `llm_client` 自动限流）、会话保留期清理 |
| 编辑器专属 | 编辑器缓冲区协议端点、自适应上下文注入、三种建议应用形态的前端交互、助手专用 system prompt（`backend/core/prompts.py` 的 `editor_assistant.chat_system`） |
| 不复用 | Writing Chat 的段落双轮编辑协议——其编辑对象是磁盘上的第 N 个段落且应用即落盘，而助手操作的是**未保存的编辑器缓冲区** |

AI 建议**不经后端落盘**：建议以结构化形式随 SSE 返回并随消息持久化
（仅供会话历史展示），应用动作发生在前端 CM6 缓冲区，最终经 save-file
单一路径落盘，与"所有写盘走统一记账"的全局约束一致。

## 后端

### 整文件读写（阶段一）

`backend/modules/editor/service.py`，路由挂载于 `project_router`：

| 接口 | 语义 |
| --- | --- |
| `GET /api/project/file-content` | 打开读取：路径经 `resolve_project_path()` 白名单校验（`.tex/.bib/.sty/.cls`），返回 `{content, md5}`；按字节保真（不做换行归一化），非 UTF-8 报稳定错误码 `invalid_encoding` |
| `GET /api/project/raw-file` | 二进制只读预览（白名单：`.pdf` 与常见位图 `.png/.jpg/.jpeg/.gif/.webp/.bmp`）：`Content-Disposition: inline` 返回原始字节，供编辑器页与工作台页（WorkspacePage 文件树点击）内嵌预览；不进入缓冲区与撤销历史 |
| `GET /api/project/file-md5` | 当前 MD5（乐观锁基线；检查结果保鲜横幅也复用） |
| `GET /api/project/latex-symbols` | 引用符号扫描（阶段四 / 决策 Q7）：全项目只读遍历 `.tex` / `.bib`（隐藏目录跳过、单文件 2MB 上限），`.tex` 逐行提取 `\label{...}`（1 基行号）与 `\cite` 家族 key（可选参数 `[p.~5]` 跳过、`\cite{a,b,c}` 拆分、注释行剔除），`.bib` 经 `bib_parser.py` 提取条目 key 与标题；每个文件经 `resolve_project_path()` 白名单 + 符号链接校验（逃逸文件静默跳过）；响应 `{labels, cites}` 只含相对路径（多用户脱敏），cites 中 bib 条目（kind="bib"，带净化标题）优先、`.tex` 已引用 key（kind="tex"）去重补尾；零 LLM、零写盘，扫描经 `asyncio.to_thread()` 调度 |
| `GET /api/project/outline` | 跨文件大纲（editor v3 阶段四 / 决策 Q10）：全项目只读遍历 `.tex`（隐藏目录跳过、单文件 2MB 上限、`resolve_project_path()` 校验与逃逸文件静默跳过，同 latex-symbols 安全口径），逐行提取分节命令 `\section` / `\subsection` / `\subsubsection` / `\paragraph`（含星号变体、可选参数跳过、注释与 `\verb` 剔除、verbatim 类环境豁免、标题净化与前端 `outlineScan.js` 同正则口径）；响应 `{files: [{file_path, entries: [{level, title, line}]}]}` 只含相对路径，无条目文件不进响应；项目级缓存（封顶逐出）+ 保存失效（save-file 落盘成功后重扫）；零 LLM、零写盘，扫描经 `asyncio.to_thread()` 调度 |
| `POST /api/project/format-latex` | 一键格式化（阶段五 / 决策 Q8）：**纯转换端点**——接收缓冲区全文 `{content}`，复用 `latex_formatter.format_latex_source()`（latexindent 优先、内置轻量格式化器兜底，外部命令执行沿既有 SandboxRunner 规则与超时语义）返回 `{content, changed}`；不接收路径参数、不落盘、不进入配额记账、零 LLM；内容上限与 save-file 同量级（超限 413 `content_too_long`）；写权限依赖使 viewer 服务端拒绝；两层格式化器按契约"失败返回原样"（`changed=false`），意外异常收敛为 500 `format_failed` |
| `POST /api/project/save-file` | 统一保存：`latex_json_guard` 宽松损坏特征校验（不做括号/$ 配平检查）→ MD5 乐观锁（不符返回 **409** 结构化错误 `file_conflict`）→ 单用户推入 BackupManager 撤销栈 / 多用户记入 workspace_edit_ops 反向补丁快照 → 刷新项目用量记账，超限回滚 |
| `POST /api/project/create-file` | 文件树新建文件：白名单后缀路径校验 → 目标已存在报 409 `file_exists`、父目录缺失报 400 `parent_not_found` → utf-8 落盘（内容固定为空，`content` 字段预留模板初始化）→ 多用户模式落盘前按新文件字节做项目配额预检（超限 413 `quota_exceeded`）、落盘后 `refresh_owner_usage_after_write` → 失效大纲缓存 → 响应携带重扫后的 `file_tree`（前端单次往返刷新）。**不纳入统一撤销记账**（edit_log 恢复通道只支持改写已存在文件），超长内容 413 `content_too_long`、损坏特征 400 `invalid_content` 与 save-file 同口径 |
| `POST /api/project/delete-file` | 文件树删除文件/目录：**主文档删除保护**——当前 main file 等于目标**或位于目标子树内**均报 400 `main_file_protected`（需先移走或先 set-main-file 更换）→ 隐藏组件拒绝 + 路径校验（不限后缀，任意非隐藏后缀可删，决策 D9；`_reject_hidden_components` 与上传路径共用）→ 项目根本身拒绝（400 `invalid_path`，防 `.` / `./` 经 path_guard 解析为项目根后被连根删除）→ 目标为目录时 `shutil.rmtree()` 递归删除整棵子树（决策 Q9）、文件 `unlink()`（不存在报 404 `file_not_found`）→ 多用户模式刷新用量记账（配额按磁盘重算随之释放）→ 失效大纲缓存 → 响应携带重扫后的 `file_tree`。同样不纳入撤销记账，误删由前端确认弹窗兜底 |
| `POST /api/project/move-file` | 文件树拖拽移动（决策 Q1–Q6，交互见[文件树新建 / 上传 / 删除 / 拖拽移动](#文件树新建--上传--删除--拖拽移动)）：JSON body `{from_path, to_path}`，文件与目录通用（目录整棵子树随迁）。校验链按序全部通过后才执行任何磁盘变更（无半完成态）：from/to 各自隐藏组件拒绝 + `resolve_project_path()`（不限后缀）→ from 存在（404 `file_not_found`，与 delete 同码）→ to 父目录存在（400 `parent_not_found`）→ into_self（to 等于 from 或位于 from 子树内，400 `into_self`，**先于** target_exists 检查——拖回原位应得 400 而非 409）→ to 已存在同名文件或目录均拒绝（409 `target_exists`，决策 Q2）→ `asyncio.to_thread` 执行 `shutil.move` → 主文件记录跟随改写（决策 Q3，见下文）→ 失效大纲缓存 → 响应 `{status, from_path, to_path, main_file, file_tree}`，main_file 与 file_tree 为前端单一事实源（决策 Q12）。**不产生新字节落盘**：不做配额预检 / 用量刷新；**不纳入统一撤销记账**（决策 Q11）、零 LLM；不做 git mv 联动。工作区未绑定 400 `invalid_workspace`；错误消息一律相对路径（多用户脱敏） |
| `POST /api/project/create-dir` | 文件树新建目录（为拖拽提供落点，决策 Q7/Q9）：名字消毒与 create-file 同口径（复用上传目标名消毒助手，拒绝空名 / 控制字符 / 路径分隔符 / 点开头）→ 隐藏组件拒绝 + `resolve_project_path()`（不限后缀）→ 已存在同名文件或目录 409 `target_exists`、父目录缺失 400 `parent_not_found` → `asyncio.to_thread` 单级 `mkdir()`（不做递归创建）→ 响应 `{status, dir_path, file_tree}`。**不纳入统一撤销记账**、不产生新字节落盘（无配额影响）、零 LLM；错误消息一律相对路径（多用户脱敏） |
| `POST /api/project/upload-file` | 文件树上传任意格式资源（决策 D1/D5/D6，详见[文件树新建 / 上传 / 删除 / 拖拽移动](#文件树新建--上传--删除--拖拽移动)）：multipart + query 形态（multipart 无法携带 JSON body，故字段 `file` 走表单、`file_path` / `workspace_id` 走 query），鉴权用 query 版 `require_workspace(write=True)`。校验链按序：20MB 流式上限（`MAX_UPLOAD_FILE_BYTES`，超限即中止并清理暂存，413 `file_too_large`）→ 目标文件名消毒（拒绝空名 / 控制字符 / 路径分隔符）→ 隐藏组件拒绝（任一路径组件点开头即 400 `invalid_path`）→ `resolve_project_path()` 不限后缀 → 同名冲突 409 `file_exists`、父目录缺失 400 `parent_not_found` → 多用户模式落盘前按实际上传字节数做项目配额预检（超限 413 `quota_exceeded`）→ 同目录临时文件 + `Path.replace()` 原子落盘（整链路字节搬运，二进制不经 `write_text`）→ 多用户用量刷新 → 失效大纲缓存 → 响应 `{status, file_path, file_tree}`。工作区未绑定项目 400 `invalid_workspace`；错误消息一律相对路径（多用户脱敏）。**不纳入统一撤销记账**、零 LLM |

手动主文件经 `set_main_file()` 持久化到项目内隐藏标记
`.latexcellence/main_file`（原子写），`open()` 时优先读取、缺失/非法/
指向文件不存在时安全回落自动识别（优先 main.tex）——否则页面刷新
直达恢复等重开项目的场景会把用户选择静默重置回 main.tex，删除保护
随之再次拦截；标记内容经 path_guard 白名单校验（仅 .tex、拒绝穿越与
符号链接），上传压缩包夹带的恶意标记无风险。主文件本身或其祖先目录被
拖拽移动时，该记录由 move-file 按「旧路径前缀 → 新路径前缀」跟随改写
（决策 Q3，见 move-file 行）。隐藏目录不进文件树，
也不参与大纲/符号扫描；git_manage 的 .gitignore 模板已含
`.latexcellence/` 避免污染用户版本库。

约束：面向用户的错误消息只用相对路径（多用户路径脱敏）；同步阻塞 I/O
与损坏特征扫描一律经 `asyncio.to_thread` 调度。

### 编辑器助手端点（阶段四，writing_chat 包内）

| 接口 | 语义 |
| --- | --- |
| `POST /api/writing-chat/editor/send` | SSE 流式对话；请求携带 `{session_id, file_path, buffer, cursor_line, selection, include_full_file, user_message, language}` |
| `POST /api/writing-chat/editor/apply-suggestion` | 记录建议应用结果（请求可携带 `suggestion_index` 做逐条标记，消息持久化 `applied_suggestions` 列表并兼容旧消息级标记），供会话历史展示；无任何文件写入 |

两个端点均受功能开关把关（决策 Q26）：`features.editor.enabled=false`
或 `assistant_enabled=false` 时服务端直接拒绝（403，错误码
`editor_disabled` / `assistant_disabled`），控制 LLM 开销是明确动机。

#### 上下文注入策略（§7.3 / 决策 Q24）

纯函数 `build_editor_context()`（`service/editor_chat.py`）构造
`<editor_state>` XML 信封：

- 行数低于阈值（`SMALL_FILE_LINE_THRESHOLD = 400`）：全文携带；
- 大文件：章节结构摘要（`\section` 等分节命令行号+标题）+ 光标前后各约
  50 行（`CURSOR_CONTEXT_RADIUS = 50`）+ 当前选区单独携带，附截断说明；
- 用户可勾选「把全文纳入本次提问」（`include_full_file`）覆盖大文件裁剪。

#### 建议提取

`extract_suggestions()` 从 assistant 回复按围栏代码块提取建议：
```latex/tex → snippet（插入光标处 / 替换选区）；
```diff/patch → unified diff 卡片。提取结果随消息持久化并随 `done`
事件返回前端。提示词（`editor_assistant.chat_system`）要求 diff 每个
hunk 至少带 3 行上下文，供前端锚定定位。

#### 数据模型增量

- `EditorSuggestion`：kind（snippet/diff）/ language / content / index
- `ChatMessage` 新增字段：`suggestions`、`suggestion_applied`（默认值
  向后兼容存量会话 JSON）
- `EditorSendRequest` / `ApplySuggestionRequest`

#### 工具白名单与多源汇聚（editor v3 阶段五a / 决策 Q12）

`_EDITOR_TOOL_NAMES`（`service/editor_chat.py`）从项目只读三件套扩为
工具集：三件套 + 联网搜索（`web_search` / `open_page` /
`read_page_sections` / `find_in_page`）+ 学术检索（`search_scholarly_works`
/ `get_work_details` / `find_related_works` / `get_full_text_or_sections`
/ `search_research_data_resources`），与 Writing Chat 主对话注册表对齐；
其中联网/学术九工具在编辑器侧**逐次确认执行**（阶段五b 确认流，见
下节；比 Writing Chat 主对话——这些工具直通——更严格，属编辑器侧
计划本意），项目只读三件套直通；`edit_paragraph` / `undo_last_edit`
仍不入白名单（写入一律走缓冲区协议）。

`_get_editor_tools` 从单一 project_tools 源扩为**三源汇聚、按源独立
容错**（单一源故障只降级该源，不连坐其余）：

- 联网搜索源：双重开关把关——`function_calling.web_search.enabled`
  AND `writing_chat.web_search_enabled`（镜像 Writing Chat；该灰度总闸
  现同时约束编辑器），不依赖 workspace / 项目打开；
- 学术检索源：`scholarly_research.enabled` AND
  `providers.openalex.enabled`（`get_research_manager` 依赖 lifespan
  初始化的共享 httpx 客户端，未初始化时按源捕获降级为空）；
- 项目工具源：现状不变（workspace 门只约束本源）。

功能开关在 schema 收集层把关（schema 不注入即对模型不可见），工具
循环执行前另按同口径复核联网/学术源开关（`_tool_source_gate_open`）：
`ToolRegistry.execute` 只查运行时 enabled 标志、不复查 config_path，
仅靠 schema 层挡不住模型幻觉调用产生真实网络请求。

**执行前白名单硬校验**：工具循环在执行任何工具前校验工具名 ∈
`_EDITOR_TOOL_NAMES`，联网/学术分组工具再复核源开关
（web：`web_search.enabled` AND `writing_chat.web_search_enabled`；
学术：`scholarly_research.enabled` AND `providers.openalex.enabled`）——
`ToolRegistry` 全局存有 `edit_paragraph` 等写工具，缺此校验时模型幻觉
调用即可绕过编辑器边界直达写盘或出网。被拒调用不执行、不推
`tool_status`，注入模型可自纠的失败 tool result 并记服务端日志。

提示词（`editor_assistant.chat_system`）增补工具使用约定：何时检索
（答案依赖不确定事实时检索，缓冲区可答不检索）、引用只来自工具真实
返回（URL / DOI / OpenAlex ID）、引用置于回复文本而非 ```latex /
```diff 建议块内。

助手 assistant 消息与 Writing Chat 不同，不持久化搜索/学术元数据
（search_results / scholarly_results 等），会话历史只有纯文本与建议，
属阶段五a 的范围裁剪而非缺陷。

#### DDG 挑战（editor v3 阶段五a）

联网搜索触发 DDG 验证时，工具循环在通用失败分支之前显式捕获
`DDGChallengeError`，SSE 下发 `captcha_challenge` 事件（challenge_id /
images / query / engine_name / instruction）后以 `done(captcha_pending=true)`
终流；本轮工具消息不持久化（会话仅剩已落盘的 user 消息），与前端
"删占位"语义对齐。挑战数据存储与提交端点
（`POST /api/search/ddg-challenge`，TTL 300s、多用户跨副本）复用
Writing Chat 既有设施，零改动。挑战解决后由用户手动重发原提问。
挑战与确认互斥：captcha 分支直接终流，确认流不与之组合（工具先经
确认、执行时才可能触发挑战）。

#### 工具确认流（editor v3 阶段五b）

联网/学术九工具在编辑器侧**逐次确认**：每次工具调用产生一个 pending
交互，用户确认后才执行；项目只读三件套维持直通。复用 Writing Chat
既有 confirm/cancel 端点与 pending 基础设施（多用户
`pending_interactions` 表驱动跨副本先到先得 / 单用户进程内 Event，
TTL 120s），零新增端点与配置项。

时序要点：

- **先登记后 yield**：`state.py` 把确认拆为登记
  （`_register_tool_confirmation`）与等待（`_await_tool_confirmation`）
  两阶段；工具循环先登记 pending、再下发 `tool_pending` SSE 事件、
  后进入等待。Writing Chat 既有次序是 yield 先于登记（亚秒级确认会
  命中"查无此 pending"404），编辑器侧不复现该缺陷；
  `_request_tool_confirmation` 组合两阶段，Writing Chat 原行为不变。
- **流内等待**：SSE 生成器挂起于等待处（与 Writing Chat pending 同
  构），confirm/cancel 端点决议后续流，确认后同一流内继续执行与
  后续模型轮次；确认参数可被前端修改（`modified_args`）。
- **取消语义**：用户拒绝或 TTL 过期统一注入取消语义 tool result
  （tool_call 配对完整、模型明确知晓用户拒绝了本次检索且不得原样
  重试），模型可基于已有信息继续回答或换策略。
- **幂等与竞态**：单用户 Event `is_set` 先查、多用户条件 UPDATE
  先到先得；过期/已决议后到达的 confirm/cancel 返回 404，前端静默
  清卡。
- **SSE 断流**：无回放机制，照搬 Writing Chat"断流即丢弃、用户手动
  重发"语义（前端确认卡为内存态，hook finally 兜底清卡）；pending
  等待期间流内无心跳，反向代理空闲超时属已知部署限制。

## 前端

```
frontend/src/
├── pages/EditorPage.jsx              # /editor 三栏布局、tab 条接线、编译联动、右栏 Tab、URL ?file= 双向、路由守卫
├── components/editor/
│   ├── LaTeXEditor.jsx               # CM6 受控封装（value/onChange）+ 多缓冲区 per-file EditorState 换档
│   ├── EditorTabBar.jsx              # 标签条（脏标记圆点 + 磁盘已变更标记 + 关闭按钮 + 全部保存）
│   ├── OutlinePanel.jsx              # 左栏「大纲」tab 内容（当前文件 / 本项目双视图；四级缩进条目 + 空态，点击经回调定位）
│   ├── FormulaPreviewCard.jsx        # 当前公式固定预览卡（编辑区底部悬浮，光标跟随防抖渲染，空态/失败静默隐藏）
│   ├── EditorToolbar.jsx             # 后端撤销重做 · 面板 · 格式化 · 编译 · 保存 · 设置
│   ├── EditorCloseTabDialog.jsx      # 关闭脏 tab 三选弹窗（保存并关闭 / 放弃改动 / 取消）
│   ├── CreateFileDialog.jsx          # 文件树新建文件/文件夹弹窗（单段名称 + 实时校验：文件白名单后缀 / 目录名消毒 + 目标路径预览）
│   ├── EditorSettingsDialog.jsx      # 偏好开关 + 自定义片段管理（增删改 + 实时预览）+ 偏好导入/导出双通道入口
│   ├── ConflictDialog.jsx            # 409 弹窗（动作作用于冲突归属 tab：放弃重载 / 剪贴板逃生 / 覆盖保存）
│   ├── CommandPalette.jsx            # 命令面板 + m×n 网格选择器二级步骤
│   ├── GridSizePicker.jsx            # 表格/矩阵网格拖选
│   ├── AssistantPanel.jsx            # AI 助手侧栏（SSE 对话 + 建议卡 + 来源 tab 守卫 + DDG 挑战弹窗 + 工具确认卡）
│   ├── StaleCheckBanner.jsx          # 检查结果过期横幅（各检查页复用）
│   └── …（BinaryFilePreview / CompilePreviewPanel / EditorRightPanel / FilePreviewOverlay / FullscreenPDFPreview）
├── CaptchaDialog.jsx                 # DDG 搜索验证码弹窗（共享：Writing Chat 页与编辑器助手侧栏共用，阶段五a 提升）
├── ProjectExplorer.jsx               # 文件树（工作台页与编辑器页共享）：新建文件/文件夹 / 上传（多选，loading 防重复）/ 删除文件/文件夹（强警告）/ 拖拽移动（原生 HTML5 DnD + 悬停自动展开）/ 设为主文件入口
├── lib/editor/
│   ├── tabs.js                       # 多缓冲区 tab 状态机纯归约（打开/激活/键入/保存回写/陈旧/关闭）+ 移动后路径重映射（remapMovedPaths）+ 会话选择器
│   ├── sessionPersistence.js         # 会话 sessionStorage 持久化纯函数（序列化 / 白名单清洗 / LRU 配额淘汰 / 项目键隔离）
│   ├── tabViews.js                   # per-tab CM6 EditorState / 滚动位置登记表（单实例换档的数据底座）
│   ├── language.js                   # 语言包抽象层（唯一入口，按文件后缀分流：.tex/.sty/.cls → 自研 Lezer 子集语法，.bib 及未知 → stex StreamLanguage）
│   ├── latexGrammar/                 # 自研 MIT Lezer LaTeX 子集语法（阶段六a P0 高亮 + 六b 折叠/环境缩进/括号匹配精确化；生成物 parser.js/parser.terms.js 不入库，npm predev/prebuild/pretest 先行生成）
│   │   ├── latex.grammar             # grammar 源（注释/命令/数学区/环境/分组/上下标/verbatim 吞掉/分节 heading，错误恢复兜底）
│   │   ├── verbTokens.js             # \verb 参数外部分词器（任意相同定界符、行界口径，fallback 语义）
│   │   ├── highlight.js              # 叶子节点 → @lezer/highlight tags 映射（与 stex 视觉对齐，对照清单见 test/editorLatexGrammar.test.mjs 头注）
│   │   ├── folding.js                # 折叠/配对纯函数层（环境与分节折叠区间、verbatim 折叠、begin↔end 配对、折叠占位符摘要，带记忆化与扫描上限）
│   │   ├── indentation.js            # 环境体自动缩进纯函数层（最内层开环境回溯、\end 对齐、verbatim 豁免）
│   │   ├── brackets.js               # 括号匹配精确化（openedBy/closedBy 配对 prop + begin↔end 配对高亮插件）
│   │   ├── index.js                  # parser + tags + 六b 扩展组装 LanguageSupport（foldService/indentService/配对 prop，模块级单例）
│   │   └── build.mjs                 # @lezer/generator Node API 生成脚本（dev/build/test 前自动执行）
│   ├── files.js                      # 可编辑/可预览白名单 + 大小上限 + 新建文件/文件夹·上传文件名校验与路径拼接纯函数（joinProjectPath / getBaseName）
│   ├── preferences.js                # 偏好默认值/白名单清洗/导出导入校验纯函数（v2 含自定义片段条目级清洗）
│   ├── matcher.js                    # pinyin-pro 四路模糊匹配（多音字词级读音修正）+ 自定义片段派生索引
│   ├── customCommands.js             # 自定义片段消费侧合并层（归一化 / 与内建库合并 / 面板独立分组）
│   ├── commandZh.js                  # 命令中文名按界面语言本地化展示
│   ├── completionTheme.js            # 补全弹层主题（索引卡式浮层 + 引导点）
│   ├── latexCompletion.js            # \ 与 \begin{ 行内补全 source（+ 阶段四 \ref/\label/\cite 引用 source；自定义片段经合并层参与匹配）
│   ├── latexSymbols.js               # 引用补全符号缓存（项目为键 / 在途共享 / 保存失效）+ 候选映射过滤
│   ├── snippetInsert.js              # 模板插入 + 占位符 Tab 循环
│   ├── wrap.js                       # 包裹命令纯逻辑（多选区包裹计算）+ 直包快捷键键位
│   ├── beginClose.js                 # \begin{env} 打字机自动补 \end（transactionFilter 纯决策分层）
│   ├── searchPanel.js                # CM6 查找替换面板主题化 + 三语短语（EditorState.phrases）
│   ├── outlineScan.js                # 大纲扫描纯模块（分节命令解析 / 注释与 verbatim 排除 / 标题净化）
│   ├── outlineProject.js             # 跨文件大纲分组纯模块（按文件分组 / 激活文件置顶排序，后端 outline 响应 → 展示结构）
│   ├── formulaHoverScan.js           # KaTeX 悬停公式区域识别纯模块（行内/显示定界符 + equation/align 系环境，注释与转义边界）
│   ├── formulaHover.js               # CM6 hoverTooltip 公式悬停预览（katex 动态分块 import / 错误提示 / 主题化）
│   ├── formatApply.js                # 一键格式化纯模块（快捷键判定 / 应用决策 view·state·store / 单事务整文档替换）
│   ├── preambleScan.js               # 导言区扫描 + 宏包缺失检测
│   ├── applyDiff.js                  # unified diff 解析与上下文锚定应用纯模块
│   ├── assistantStream.js            # 助手 SSE 事件归约纯函数（占位消息 / 工具状态 / 工具确认卡 / 会话 id / DDG 挑战，hook 与单测共用）
│   └── （命令数据库 data/latexCommands/ 按类目拆分 JSON + index.js 统一加载器，含 wrap 包裹类目）
└── hooks/useEditorBuffer.js          # 多缓冲区编排（打开/激活/保存/冲突/陈旧/自动保存/刷新恢复/项目切换守卫）
└── hooks/useEditorAssistant.js       # 助手 SSE hook（归约委托 assistantStream 纯函数 + 项目切换守卫 + 工具确认/取消动作 + 复用 useWritingChatCaptcha 挑战通道）
└── hooks/useEditorOutline.js         # 大纲数据源（当前文件视图防抖重扫 + tab 切换立即重扫；本项目视图拉取后端 outline、保存后防抖重拉）
└── hooks/useEditorFormat.js          # 一键格式化编排（在途守卫 / 发起 tab 定位 / 应用决策分派）
```

Zustand：`editorStore` 含三个切片——`editorPreferences`（persist v2：
autoSave / softWrap / autoCloseEnv / formulaHover / selectionMatches /
fontSize / customSnippets；v1 存档经 migrate 透传后按白名单清洗补默认
空数组）、`editorSession`（多缓冲区会话，
不持久化）、`editorAssistant`（助手会话 id 与消息视图，不持久化；
后端会话按项目 scope 隔离，切换文件延续会话）。

### 多缓冲区基座（editor-productivity-design §5.1，阶段一）

`editorSession` 切片为多缓冲区模型：`tabs: [{ filePath, buffer, baseMd5,
dirty, stale, lastActive }]` + `activeTabPath` + `conflict`。tab 转移逻辑
（打开 / 激活 / 键入回写 / 保存回写 / 陈旧回写 / 关闭）与拖拽移动后的
路径重映射（`remapMovedPaths`，决策 Q6）全部是
`lib/editor/tabs.js` 的不可变纯归约，node --test 直接单测。

- **单 CM6 实例 + EditorState 交换**：tab 切换时 `LaTeXEditor` 把当前视图
  的 `EditorState`（撤销历史与选区随之冻结）按 filePath 收进
  `lib/editor/tabViews.js` 登记表，换入目标 tab 存档；滚动位置不属于
  EditorState，由登记表单独记忆并在换回时恢复。无存档的 tab 按当前
  缓冲区重建 state（扩展配置复刻 `@uiw/react-codemirror` 的
  `getDefaultExtensions` 组装，basicSetup / 语法包不因换档缺位）；
  存档与 store 缓冲区或外观（softWrap/readOnly）失配时同样重建（撤销
  历史复位，对齐重挂载语义）。换档产生的整文档变更经 switchGuard 吸收，
  不误标脏标记。
- **激活缓冲区单点订阅**：content 的组件级订阅仍只存在于 `SessionEditor`
  隔离层（A2 按键渲染解耦保持）；tab 条用 `useShallow` 投影路径数组，
  tab 项各自订阅自身 dirty/stale 原始值，键入不牵连页面骨架重渲染。
- **陈旧检测（Q4）**：激活 tab 时惰性调 `GET /api/project/file-md5` 与该
  tab `baseMd5` 比对（复用检查结果保鲜的"MD5 比对 + 只提示不销毁"模式），
  不一致显示"磁盘已变更"标记；保存前经确认弹窗（自动保存通道静默跳过，
  不弹窗不抢激活位）；保存时 409 冲突流程照旧兜底，保存成功后 stale 清除。
  MD5 获取失败不误报。
- **关闭脏 tab（Q5）**：三选弹窗（保存并关闭 / 放弃改动 / 取消）；
  `autoSave` 偏好开启时默认「保存并关闭」；Escape / 点击遮罩一律「取消」。
  关闭激活 tab 时右侧邻居接管激活位、末尾回落左侧。
- **刷新恢复（Q14）**：打开的 tab 集合（含脏缓冲区全文 + `baseMd5` + 陈旧
  标记）与激活位带项目隔离键写入 sessionStorage（防抖 1s，pagehide /
  页面隐藏兜底 flush）；刷新后整体恢复并 toast 告知，磁盘内容以缓冲区为
  准，期间磁盘被改由激活时陈旧检测提示、保存时乐观锁兜底。超配额
  （约 5MB）按 LRU 淘汰最久不活跃的非脏 tab；全为脏 tab 仍超限时 toast
  提示保存。URL `?file=` 跟随激活 tab；刷新时与 sessionStorage 恢复结果
  合并（恢复全部 tab 集合，`?file=` 指定者激活）。SPA 内切换项目即清空
  会话与视图存档（项目键守卫，sessionStorage 载荷单键单项目）。
- **自动保存（Q15）**：per-tab 防抖器，所有脏 tab 各自停改 2.5s 落盘
  （`autoSave` 偏好开启时；冲突快照门闩按 tab 隔离，防 409 回路）。
- **编译联动（Q16）**：编译前依次自动保存**所有**脏 tab（tab 条顺序），
  任一失败即中断编译——409 由保存侧切换到归属 tab 进入冲突流程，陈旧
  确认弹窗同理。
- **AI 助手 tab 守卫（Q17）**：助手上下文与建议卡片携带发送时的来源
  filePath（随消息记录）；应用建议时活跃 tab 不匹配则禁用按钮并提示
  切换，不自动切 tab（纯前端改动）。
- **全部保存**：多于一个脏 tab 时 tab 条提供「全部保存」入口（按 tab 条
  顺序依次落盘）。
- **后端撤销/重做**：工具栏按钮绑定激活 tab 的文件；「脏改动时禁用后端
  历史」的既有规则不变；undo/redo 落盘后重拉内容重建该 tab（撤销历史
  随 EditorState 重建复位）。

按键渲染解耦（A2）：激活缓冲区的组件级订阅只存在于 `SessionEditor`
隔离层（EditorPage 内模块级组件，包住 LaTeXEditor），每次按键仅该层
与 CM6 内部参与渲染（value 与文档恒等时 dispatch 跳过），页面骨架 /
文件树 / tab 条 / 工具栏 / 右栏不随键入重渲染。`useEditorBuffer` 不订阅
content/dirty——beforeunload、per-tab 自动保存防抖与冲突快照门闩改为
`store.subscribe` 命令式副作用，行为与组件级实现逐条等价；冲突弹窗的
剪贴板逃生在点击时刻经 `getState()` 现取冲突归属 tab 的全文，助手面板
的大文件行数判定按文件切换惰性重算。

### 编辑基本功包（editor-productivity-design §5.3，阶段二）

只作用于活跃缓冲区（单 CM6 实例），撤销历史随 per-tab EditorState 天然隔离。

- **包裹命令（决策 Q13）**：Ctrl/Cmd+B / I / U / K 分别直包
  `\textbf{}` / `\emph{}` / `\underline{}` / `\texttt{}`；Ctrl+L 打开
  命令面板的 wrap 专项浮层（`categoryFilter` 过滤，覆盖 wrap 全集）。
  数据源为 `data/latexCommands/wrap.json` 新增的 wrap 类目（直包四命令 +
  textit / textsc / textup / textsl / textnormal / textsf / textrm /
  textsuperscript / textsubscript；原 text 类目中的包裹命令整体迁入），
  顺带进 matcher 四路匹配索引与命令面板——面板对
  `category === "wrap"` 的条目执行包裹动作而非占位符插入。
  纯逻辑在 `lib/editor/wrap.js`：`computeWrapEdits()` 对每个选区包成
  `\cmd{内容}` 并保持内容选中（便于连续叠加包裹实现嵌套），空选区插入
  空命令并把光标定位到参数内；多选区逐个包裹、单事务完成（Ctrl+Z 一步
  撤销），选区方向保留；重叠区间整体拒绝。
- **多光标（§4）**：basicSetup 的 `allowMultipleSelections` 放开，默认
  开启、无偏好开关；Alt+Click 添加光标、Ctrl/Cmd+D 选中下一处匹配等
  CM6 默认行为随之可用。
- **`\begin{env}` 自动配对（决策 Q12）**：输入 `\begin{env}` 完成 `}`
  后自动追加 `\n\n<行首缩进>\end{env}`（Overleaf 式打字机补全），光标
  留在中间空行；该 `\begin` 之后的缓冲区内已存在对应 `\end{env}`
  （向后扫描，嵌套同名环境逐层抵消、注释行不参与配对）则不再补；
  `%` 注释行内的 `\begin` 不触发补全。纯决策函数
  `beginAutoCloseDecision()`（输入
  光标前后文本，输出插入文本与光标偏移）与 transactionFilter
  （识别「光标恰落在打字插入区间末尾」的事务，多光标下逐光标决策，
  追加事务以 `sequential: true` 与打字事务合并，单步撤销）分居于
  `lib/editor/beginClose.js`。`autoCloseEnv` 偏好开关（默认开）经
  isEnabled 读取器每事务求值，切换无须重配置扩展。
- **查找替换面板**：basicSetup 内置 searchKeymap（Ctrl/Cmd+F 唤起），
  `lib/editor/searchPanel.js` 补齐面板主题（面板 / 输入框 / 按钮 /
  匹配高亮全部走主题 CSS 变量，亮暗自适应）与本地化——官方短语键经
  `SEARCH_PHRASE_KEY_MAP` 映射到 i18n `components:editor.searchPanel.*`
  命名空间，注入 `EditorState.phrases`（以语言字段为依赖的 compute
  facet）；界面语言切换时经 `setSearchLanguage` 效果对活跃视图热更新
  （换档收起的存档 EditorState 保留建档时的语言，可接受边界）。样式
  细节：按钮必须显式 `appearance: none` + `background-image: none` 压掉
  Tailwind preflight 的 `appearance: button` 与 UA 样式表原生渐变
  （否则拟物质感、暗色下亮灰底白字不可读），配色对齐全站 Button 的
  secondary（动作按钮）/ ghost（关闭 ×）变体；面板保持 block 流布局
  （CM 插入的 `<br>` 仅在块级布局中折行，flex/grid 下盒尺寸不可改写，
  替换行折行会失效）；勾选框为 `appearance: none` 纯 CSS 自绘（勾选态
  clip-path 勾 + 150ms scale 微动画，对齐 shadcn Checkbox 观感）——原生
  控件的绘制不保证跟随 `accent-color` 过渡中间值逐帧重绘，自绘后颜色
  落在 background-color / border-color 上，亮暗切换随全局主题过渡可靠
  渐变（View Transitions 路径下两者均被 `:active-view-transition` 关闭，
  由整页交叉淡入接管）。

### 自定义片段库（editor v3 阶段二 / 决策 Q7、Q8）

用户个人高频模板（如实验室宏包专属环境）并入编辑器偏好持久化与消费链路，
`data/latexCommands` JSON 保持构建期冻结，新增的全部是消费侧能力。

- **数据与持久化（Q7）**：偏好 persist（`"latexcellence-editor"`）version
  1 → 2，载荷增 `customSnippets` 数组；`sanitizePreferences()` 对其做条目
  级清洗——条目形状 `{ name, insert, zh, category: "custom" }`（category
  恒定、grid/pkg 等运行期字段不落盘）、命令名形状校验（与内建同规则）、
  同名去重（先到先得）、上限 100 条、单条 insert ≤ 4000 字符（超限整条
  丢弃，截断可能产生残缺 LaTeX）。偏好导出载荷升 version 2；导入通道
  同时接受 v1（无片段字段，清洗回落空数组，不丢数据）与 v2。
- **消费侧合并层（Q8）**：纯模块 `lib/editor/customCommands.js`——
  `normalizeCustomSnippet()` 把存储条目归一化为运行时形状（补齐
  `pkg: null` 与派生 `grid`：insert 含 `%CELLS%` 标记时默认 2×2，使
  `%COLS%`/`%CELLS%` 网格标记对自定义条目经既有 GridSizePicker /
  `expandEntryInsert()` 机制天然生效）；`withCustomCommands(base, custom)`
  把自定义条目（与内建同名者丢弃，避免运行期命中歧义）追加在内建之后，
  作为补全 source、命令面板 / 包裹浮层数据与插入入口的统一数据通道；
  `groupCommandsWithCustom()` 在面板把自定义条目归入独立 "custom" 分组
  置尾（三语条目）。条目机制层零改动：`expandEntryInsert` /
  `insertSnippet` / `$n` 占位符跳转原样复用。
- **匹配索引**：matcher 共享索引保持单实例，`getExtendedMatcherIndex()`
  仅为追加的自定义条目计算拼音序列并按条目数组引用缓存（store 不可变
  更新，引用不变即零重算）；`\begin{` 环境补全的环境清单同样经合并层
  提取并按引用缓存。偏好更新即时生效，无须重建编辑器扩展。
- **管理界面**：`EditorSettingsDialog` 新增「自定义片段」分区——列表
  （命令名 + 中文名/模板摘要）与增删改表单（名称 / 插入内容 / 中文名，
  插入内容带实时预览：占位符与网格标记解析后的实际插入文本）；表单校验
  名称形状、与内建及其他片段的重名、插入内容非空与长度上限。自定义片段
  跟随既有偏好导入导出双通道分发，不另开通道。

### 大纲面板（editor-productivity-design §5.3，阶段三）

左栏「文件 / 大纲」双 tab（决策 Q6）：大纲 tab 的「当前文件」视图**仅解析
当前激活缓冲区**（跨文件大纲由 editor v3 阶段四「本项目」视图收口，见
[跨文件大纲与公式固定预览卡](#跨文件大纲与公式固定预览卡editor-v3-阶段四--决策-q10--q11)），
条目为 `\section` / `\subsection` / `\subsubsection` / `\paragraph` 四级缩进展示（含 `*` 星号变体）。

- **扫描纯模块** `lib/editor/outlineScan.js`：`scanOutline(docText)` 单趟
  O(n) 逐行扫描（正则无嵌套量词），输出 `{ level, title, line }`
  （line 为 1 基行号）。边界处理：非转义 `%`（含 `\%` / `\\%` 转义奇偶
  判定）之后的注释不产生条目；`\section[短标题]{长标题}` 跳过可选参数；
  标题花括号逐字符配平（`\{` 转义不计数），跨行标题（花括号未在同一行
  配平）保守跳过；verbatim 类环境（verbatim / verbatim* / Verbatim /
  Verbatim* / lstlisting / minted 保守集合）与 `\verb` 行内参数整体排除
  （单行剥离顺序固定为 `\verb` 参数 → 注释 → verbatim 起点：`\verb`
  参数内 `%` 为字面字符、`\begin{verbatim}` 为字面文本，不得先于参数
  剥离前产生注释 / 环境语义）。
- **标题净化规则**（实现层既定决策）：标题内命令名后紧跟花括号参数时
  丢弃命令名保留参数内容（`\textbf{Bold}` → Bold）；无参数命令保留命令
  名去反斜杠（`\LaTeX` → LaTeX）；转义符号字面化（`\_` → _）；`~` 转空
  格；花括号定界符丢弃、连续空白折叠为单个空格。
- **重扫时机** `hooks/useEditorOutline.js`：激活 tab 变化（切换 / 关闭 /
  项目切换）立即重扫目标缓冲区；编辑激活缓冲区经
  `useEditorStore.subscribe` 命令式订阅（A2 渲染解耦，键入不触发面板重
  渲染）防抖 300ms 重扫，到点后现取当下激活缓冲区，期间换档不串档；
  防抖器在卸载 / 激活位变化路径清理。大纲状态仅保存激活 tab 的扫描结果
  （组件局部 state，不进 store——会话切片无需持久化大纲，重扫成本可忽略）。
- **点击定位**：点击条目经页面注入的回调取活跃 CM6 视图
  （`EditorPage` 的 `editorViewRef`），`selection anchor` 置目标行行首 +
  `EditorView.scrollIntoView(pos, { y: "start" })`——行首落光标复用既有
  `cm-activeLine` 主题高亮该行；行号越界（编辑期间条目行漂移）直接忽略。
- **空态**：无激活 tab 与无大纲条目两分支，文案三语
  （`components:editor.outline.*`）；条目过多随 CardContent 滚动。
  左栏已有 tab 入口即开关语义，不新增偏好项（对齐 Q18 边界）。

### 引用补全（editor-productivity-design §5.3，阶段四 / 决策 Q7）

`\ref{` / `\label{` / `\cite{` 三路触发的行内补全 source（挂入
`latexCompletion.js` 的 autocompletion override，复用 completionTheme
弹层主题），数据来自后端 `GET /api/project/latex-symbols` 全项目符号
扫描（跨文件 `\ref` 命中、`.bib` key 命中）。

- **触发与替换区间**：`\ref` / `\cref` / `\Cref` / `\eqref` / `\pageref` /
  `\autoref`（含星号与已敲出的可选参数）与 `\label{` 补全已有 label
  （`\label{` 提供已有标签提示，避免重复定义）；`\cite` 家族（任意含
  cite 的命令名，含星号与可选参数如 `[p.~5]`，`\nocite{*}` 通配不进候选）
  补全 bib 条目 key 与
  `.tex` 已引用 key（bib 优先）。应用补全只替换 `{` 之后已敲入的部分
  key（CM6 默认 apply，`\ref{` 前缀原样保留）；`validFor` 为 key 字符集
  （字母数字 + `: _ - .`），弹层存续期间继续敲 key 不重复查询。
- **纯模块** `lib/editor/latexSymbols.js`（node --test 直接单测）：
  `reduceProjectKey()` 项目隔离键归约（单用户 projectPath / 多用户
  projectId，与 useEditorBuffer 口径一致）；`createProjectSymbolCache()`
  缓存（应用级单例 `projectSymbolCache`，拉取以共享在途 Promise 去重，
  失败静默出缓存下次重试、不弹 toast）；`buildLabelOptions()` /
  `buildCiteOptions()` 把扫描结果映射为补全 option（label 显示名 +
  detail 显示出处：label 为 `文件:行号`、bib 条目为净化标题、tex 已引用
  key 为文件；type 字段区分 label / cite），`filterCompletionOptions()`
  前缀命中优先、子串其次、上限 60 条。
- **缓存失效（Q7）**：缓存以项目为键；`useEditorBuffer` 保存成功路径
  （手动 / 自动 / 编译前置 / 三选弹窗共用 `doSaveTab`）调 `invalidate()`
  下次触发补全重拉；项目切换守卫 effect 调 `clear()` 清空全部。
- **拉取接线**：`SessionEditor` 提供 `getRefSymbols` 读取器（项目未打开
  返回 null → source 短路），经 `LaTeXEditor` prop 注入
  `latexCompletionExtension`（缺省时不挂引用 source）；首次触发补全时
  缓存缺失自动拉取，loading 期间走 CM6 默认行为。

### 一键格式化（editor-productivity-design §5.3，阶段五 / 决策 Q8）

工具栏「格式化」按钮 + Ctrl/Cmd+Shift+F 快捷键（避开阶段二已占用的
Ctrl+B/I/U/K/L、Ctrl+/ 与 CM6 内置查找面板 Ctrl/Cmd+F）。点击后调后端
`POST /api/project/format-latex` 纯转换端点（携带**发起时**的 tab 缓冲区
全文），成功后**直接全文替换发起 tab 的缓冲区并置脏**——不做 diff 预览，
落盘仍走 save-file 单一路径 / 自动保存，toast 提示「Ctrl+Z 可撤销」。

- **应用决策**（纯模块 `lib/editor/formatApply.js` 的 `planFormatApply()`，
  node --test 直接单测）：目标始终是**发起时的 tab**（按 filePath 定位）。
  结果与原文一致 → `skip_unchanged`（不替换，提示已符合格式规范）；目标
  tab 已被关闭 → `skip_missing`（静默丢弃）；目标即激活 tab 且 CM6 视图
  正显示其缓冲区 → `view` 通路，对视图 dispatch **单个**整文档 changes
  事务（CM6 history 记录一次变更，Ctrl+Z 一步撤销回原文）；活跃 tab 在
  请求期间被切走 → `state` 通路，对 `tabViews` 登记表中的 EditorState
  存档做单事务 `update()`（history facet 随存档保留，不切视图，仅写回
  发起 tab 的缓冲区数据）；存档缺失 / 失配 → `store` 通路降级，仅写回
  store 缓冲区（下次换入按缓冲区重建，历史复位——可接受降级）。
- **后台 tab 缓冲区替换**：`tabs.js` 新增 `replaceTabBufferReducer()` +
  store `setTabBuffer` 动作（置脏、不改激活位 / 乐观锁基线 / 陈旧标记）。
- **并发守卫** `hooks/useEditorFormat.js`：请求在途时拒新请求（工具栏
  按钮同步禁用防连点）；Q8 未要求内容一致性校验——返回后直接应用最新
  结果，请求期间继续编辑的少量改动会被覆盖，Ctrl+Z 一步可回（决策记录）。
- **viewer 禁用**：工具栏按钮按写权限禁用；后端端点同样挂
  `require_workspace_write`，服务端兜底。

### KaTeX 公式悬停预览（editor-productivity-design §5.3，阶段六 / 决策 Q9）

悬停浮层形态（Q9）：指针在公式区域停留约 500ms（CM6 `hoverTooltip` 的
`hoverTime`）后弹出 KaTeX 渲染预览；右栏固定预览卡留二期。开关为
`formulaHover` 偏好（默认开，Q18），经 isEnabled ref 注入（同
`autoCloseEnv` 模式，切换偏好不重配置扩展），设置弹窗 + 三语 i18n。

- **公式识别**（纯模块 `lib/editor/formulaHoverScan.js`，node --test
  单测）：`findFormulaAt(text, pos)` 判定位置是否在公式内，返回
  `{ from, to, text, display }`。支持行内 `$...$` / `\(...\)`、display
  `$$...$$` / `\[...\]` 与 equation / align 系环境（环境清单与
  `utils/formulaWrap.js` 的 STANDALONE_DISPLAY_ENV_RE 口径一致，含带星
  变体；同名环境嵌套按深度配对）。`\$` / `\%` 转义不算定界符，悬停在
  顶层转义对（`\\`、`\$`、`\%` 等）的字面字符上不判定公式；注释行
  （未转义 `%` 之后）不参与定界符匹配、不判定公式；未闭合定界符 / 环境
  不判定公式；verbatim 类环境不特殊处理（与 `formulaWrap.js` 同源口径，
  其内部 `$...$` 仍判定为公式）。区域包含判定为 `from <= pos < to`（悬停
  定界符任一字符均命中）；公式区域内部的注释文字仍显示所在公式（覆盖
  不留空洞）。
- **性能策略**：CM6 hover 回调只在指针停留 500ms 后触发（非 mousemove
  高频），单次调用为「文档起点 → 悬停位置」的单向单遍状态机扫描
  （O(pos)，命中后再向后补扫至闭合定界符），不做全文重扫、不建索引。
- **katex 动态分块**（决策 Q9）：`import("katex")` 与
  `import("katex/dist/katex.min.css")` 均为动态 import，模块级缓存
  Promise 全程只加载一次；构建产物为独立 `vendor-katex` chunk
  （vite manualChunks 既有规则），编辑器路由静态引用零 katex，首屏零
  加载（CSS 同拍按需注入，不阻塞首屏）。
- **渲染与安全**：`renderToString` 固定 `trust: false`（禁止 `\href`
  等写入任意属性）+ `output: "html"` + `strict: "ignore"`，
  `throwOnError: true` 下 KaTeX parseError 统一捕获 → 浮层显示三语
  错误提示（`components:editor.formulaHover.renderError`），不抛未捕获
  异常；katex 块加载失败同样落错误提示。浮层内不放原始用户 HTML。
- **主题与换档**：浮层经 `EditorView.theme` 走全站 CSS 变量（--card /
  --border / --surface-*，与 completionTheme 同源浮层词汇），亮/暗主题
  切换零 JS 参与；tooltip 属于 EditorState 状态，tab 换档（setState）
  与文档变更时由 CM6 自动收起，per-tab 换档后扩展随 buildState /
  存档恒在。

### 编译错误列表与跨文件跳转（editor-productivity-design §5.3，阶段七 / 决策 Q10 + Q19）

编译失败时右栏在既有解释 / 建议摘要之下展示可点击错误列表（不做行内
波浪线，Q10）：条目为「文件相对路径 · l.N 行号 + 消息摘要」，原始日志
行做 title detail；点击条目联动跳转——目标文件 tab 已存在则激活、未
打开则打开，随后滚动定位到该行行首并高亮（复用阶段三 jumpToLine 的
selection anchor + scrollIntoView 方案）；跨 tab 跳转经待决标记延迟到
activeTabPath 换档（EditorState 交换）完成后执行，打开失败时清除待决
标记防迟到误定位。

- **日志文件栈解析**（纯模块 `lib/editor/logFileStack.js`，node --test
  单测）：`parseLogErrors(logText, fallbackFile)` 把每条 `l.N` 错误归属
  所在文件——`(./path` 压栈、`)` 弹栈（TeX 日志的文件嵌套约定），`l.N`
  归属栈顶文件，支持跨文件跳转；高置信要求栈顶为项目内可编辑白名单
  文件（.tex/.bib/.sty/.cls），输出条目含相对路径、行号、消息摘要、
  `l.N` 源码回显 detail 与置信标记。
- **歧义保守回落**（Q19）：`\(` / `\)` 数学括号、消息文本中的成对括号
  （`(see the transcript)`、`Overfull \hbox (15.3pt too wide)`）经
  「文本括号深度」记账隔离，不参与文件栈运算；错误块（`!` 行到 `l.N`
  行）上下文收录期间跳过栈运算，上下文收满仍无 `l.N`（`File not found`
  等无行号错误）则恢复扫描，防止错误块无限吞行导致后续文件栈失配；
  空栈弹栈等畸形日志置 stackBroken，此后错误与
  栈顶为系统文件（texmf 绝对路径）/ `.aux` 等不可编辑路径的错误一律
  回落发起编译的文件（`useEditorCompile` 记忆的 compileTarget）并标
  `lowConfidence: true`，UI 以琥珀色角标 + 三语 tooltip 标注；无文件栈
  （单文件项目 / texfot 过滤）同样回落低置信。
- **79 列折行**：TeX 日志按 max_print_line 折行，解析先按「折行段必
  打满 79 列」启发式 dewrap 再逐逻辑行处理，长文件名 / 长消息跨行正确
  续接（保守启发式：恰好 79 列的完整逻辑行会被误并入下一行，只影响
  文本拼接、不影响括号栈正确性）。
- **脱敏与路径形态**：解析对象为编译响应携带的 stdout 原始日志（多用户
  模式后端已由 `sanitize_output_paths` 把工作目录前缀改写 `./`），条目
  文件路径统一为项目内相对路径展示，绝无绝对路径。
- **防御上限**：错误条目数上限 200、单条消息 / 上下文截断，防止异常
  日志（如无限循环输出）拖垮右栏渲染；未以 `l.N` 收尾的错误块丢弃
  （无行号不可跳转）。

### 修改行色条与状态栏（editor-productivity-design §5.3，阶段八；借鉴 Notepad++）

**修改行色条**（借鉴 Notepad++ 8.4.6 的 Change History margin）：编辑区
行左侧以内阴影色条标记修改行——**琥珀色** = 相对保存基线有未保存修改的
行，**翠绿色** = 本会话改过且已保存的行，橙色优先于绿色；行号集合随
事务重算，`Ctrl+Z` 撤回到保存点时橙标精确清零（与脏标记语义一致）。
渲染用 `Decoration.line` 零 gutter 列实现，不挤压代码区。

- **基线数据**：tab 记录新增 `savedBuffer`（最近保存落盘的内容，保存
  快照回写推进）与 `openedBuffer`（本次会话打开时的原始内容，仅会话
  内存态、不持久化）；`dirty` 同步改为内容比对 `buffer !== savedBuffer`
  （键入后撤回到保存内容即自动清脏，关闭弹窗 / 自动保存 / beforeunload
  联动受益）。
- **行级 diff**（纯模块 `lib/editor/changeHistoryDiff.js`，node --test
  单测）：全文相同直通 → 公共前后缀剪除 → 中段 Myers O(ND) 回溯收
  集变更行；纯删除块不标（after 侧无对应行，对齐 Notepad++）。防退化
  护栏：中段超行数上限或 Myers 步数预算超支时整段标记（整文档格式化
  等全量替换场景结果正确、开销有界）。
- **CM6 扩展**（`lib/editor/changeHistory.js`）：`changeHistoryField`
  持有基线文本与行号集合，基线经 `setSavedTextEffect` /
  `setOpenedTextEffect` 由 LaTeXEditor 随 tab 激活（换档/重建）与保存
  回写同步注入。性能取舍：橙色集每次键入全量重算（剪除后开销可忽略）；
  绿色集为「保存时冻结」模型——冻结集仅在基线效果触发时重建，展示集 =
  冻结集 − 橙色集派生，已保存行被再编辑转橙后撤回可恢复绿标。已知近似：
  键入期间冻结绿集行号不随上方插入/删除漂移（越界行过滤），下次保存 /
  换档时精确重建；无保存基线时（v1 载荷恢复降级）不显示色条。

**底部状态栏**（借鉴 Notepad++ 状态栏）：中栏编辑器下方常驻条展示光标
行列、选区字数（有选区时）与全文行数 / 字数；字数口径为 CJK 汉字逐字 +
西文词连续段（`lib/editor/stats.js` 纯模块，LaTeX 命令按原文近似统计，
定位为粗略参考）。统计经 `createStatsHub` 发布-订阅直通
`EditorStatusBar`（useSyncExternalStore 订阅），不经过页面 React 树，
键入与光标移动只重渲染状态栏自身；光标/行数即时发布，字数 250ms 尾随
防抖（大文档逐键全量统计不可取），换档/整缓冲区替换后同步计算一次。

**选中即高亮全部匹配**（借鉴 Notepad++ 智能高亮）：新增
`selectionMatches` 偏好（默认开），按偏好挂载 `@codemirror/search` 的
`highlightSelectionMatches`（minSelectionLength 2 / maxMatches 100），
选中文字后高亮全文相同片段，便于核对 `\label` 与 `\ref` 对应；偏好经
换档失配检测重建存档 EditorState 生效到各 tab。

**会话持久化升级 localStorage**（借鉴 Notepad++ snapshot：未保存内容不
随浏览器关闭丢失）：多缓冲区会话载荷由 sessionStorage 迁移到
localStorage（同一浏览器重启后仍可恢复脏缓冲区）；升级前的 sessionStorage
遗留载荷在主存缺失时自动收养迁移（`loadSessionAdoptingLegacy`），旧会话
不因升级丢失；项目切换守卫双存储清除。载荷 v2：脏 tab 额外携带
`savedBuffer`（保存基线，恢复后色条橙基线 / 关闭弹窗语义 / 撤销基线复
用），干净 tab 不冗余写入；`openedBuffer` 不持久化，恢复后回落
`savedBuffer`（绿色色条从零重建，橙色基线不受影响）。已知取舍：localStorage
跨浏览器窗口共享（恢复载荷后者胜出）；公共机器上脏缓冲区内容留存本机
（与 Notepad++ snapshot 同类取舍，隐私敏感场景建议开启自动保存落盘）。

### 文件树新建 / 上传 / 删除 / 拖拽移动

左栏 `ProjectExplorer` 在可写（`useCanWrite()`）时提供文件与目录级操作入口：
面板标题行「新建文件」「新建文件夹」按钮在项目根创建；目录行 hover 的
「+」「+文件夹」在该目录创建（单段名称输入弹窗 `CreateFileDialog` 按
`mode` 分流：文件走 `validateNewFileName()` 单段路径 + 白名单后缀实时
校验、文件夹走 `validateNewDirName()` 名字消毒预检——与服务端 create-dir
同口径，空名 / 分隔符 / 控制字符 / 点开头拒绝、目录不限扩展名；
`joinProjectPath()` 拼相对路径）；文件行 hover 的垃圾桶按钮删除文件，
目录行 hover 的垃圾桶按钮删除文件夹（均 `ConfirmDialog` destructive 二次
确认：文件夹为**递归删除强警告**「将永久删除该目录及其内部全部文件与
子文件夹，此操作不可恢复」（决策 Q9），目标命中脏 tab（目录按路径前缀
命中子树）时描述追加未保存警示）。

创建 / 删除 / 移动经 `POST /api/project/create-file` / `create-dir` /
`delete-file` / `move-file`（见后端一节），响应携带重扫后的 `file_tree`，
前端 `setFileTree` 单次往返完成刷新——`LatexProject.refresh_file_tree()`
就地更新 open 时扫描的缓存。创建文件成功后自动打开新 tab（复用
`doOpenFile` 通路）、创建文件夹仅刷新树；删除成功后命中文件/子树的全部
tab（含脏缓冲区）联动关闭、只读预览覆盖层按同前缀命中收起、引用符号
缓存失效。操作不进撤销记账（见后端小节），由确认弹窗与强警告兜底。

**拖拽移动**（决策 Q1–Q7、Q12、Q13，机制详见
[editor-file-move-design.md](../editor-file-move-design.md)）：文件与
目录行均可拖拽（原生 HTML5 Drag & Drop，零新依赖，Q5；`canWrite` 门槛，
viewer 无拖拽入口，WorkspacePage 只读用法不受影响）。dragstart 以自定义
MIME（`application/x-latexcellence-tree-node`）携带 `{path, isDir}` 并置
`effectAllowed = "move"`，被拖行半透明。合法落点为目录行（被拖目录自身
与子孙按前缀排除）与树底部空白区（= 项目根）：合法落点高亮 +
`dropEffect = "move"`；非法落点（文件行、自身 / 子孙）不响应放置且不
高亮。拖到折叠目录上悬停约 500ms 自动展开（Q7，展开即成不回弹）；
dragleave / drop / dragend / 组件卸载统一取消计时器并复位全部拖拽状态。
落位路径为 `to_path = joinProjectPath(targetDir, getBaseName(fromPath))`
（原名落位，`getBaseName` 为 `lib/editor/files.js` 纯函数）；拖回原位
静默忽略不发请求（目录拖入自身子树由前端兜底拦截）。

不做乐观更新（Q12）：drop 后发请求，成功才依次同步本地状态——
`remapMovedPaths()`（`lib/editor/tabs.js` 纯归约）重映射 `tabs[].filePath`、
`activeTabPath` 与 409 冲突归属路径（**buffer、savedBuffer、baseMd5、
脏标记、陈旧标记原样保留**，Q6；目录按「等于 from 或位于 from 子树内」
前缀一次匹配覆盖整棵子树，`/` 边界防误伤同名前缀目录；无任何命中时
状态引用不变、订阅方零触发）；全局 `activeFile`（useStore）与只读预览
覆盖层归属路径按同口径重映射（预览文件随移动换路重拉，未命中零触发）；
`setMainFile(res.main_file)` 与 `setFileTree(res.file_tree)` 以响应为
单一事实源；引用符号缓存失效（`\label` / `\cite` 来源路径变化）。
sessionPersistence 持久化键与 URL `?file=` 由既有订阅随 tabs /
activeTabPath 变化自动跟进改写（Q13，防止刷新后按旧路径打开 404 或丢
脏缓冲区）。失败仅 toast（`translateError()` 链路），树与标签页零改动。

**主文件记录跟随**（决策 Q3）：移动主文件本身或其祖先目录时，后端按
「旧路径前缀 → 新路径前缀」改写 `.latexcellence/main_file` 记录（切片
前先规范化 `./` 前缀；复用 set-main-file 内部写入通路，内部机制写隐藏
路径不受 `_reject_hidden_components` 约束），响应携带改写后的
`main_file`，前端星标即时迁移。已知限制：`\input` / `\include` 引用不
自动改写（Q4，由用户自行维护）、触屏不支持拖拽（Q5），见设计文档
§8 非目标与已知限制。

**资源上传**（决策 D1–D5、D8，机制详见
[editor-file-upload-design.md](../editor-file-upload-design.md)）：
面板标题行与目录行 hover 各有「上传」按钮（与新建「+」同模式），系统
文件选择框一次多选任意格式。选中后 `useEditorBuffer` 的
`handleUploadFiles()` 串行逐个直传 `POST /api/project/upload-file`：
发起前 `validateUploadFileName()` 做与服务端同口径的消毒预检（拒绝空名 /
路径分隔符 / 控制字符 / 点开头文件名，无效名不发请求；服务端校验仍为
权威）；每次成功以响应携带的重扫整树 `setFileTree`（末次即最终态）并
失效引用符号缓存（上传 `.tex` / `.bib` 会引入新的 `\label` / `\cite`
来源）；失败逐个收集，toast 汇总文件名与原因（经 `translateError()`
翻译），已成功文件保留。单文件成功 toast 附「插入引用」动作（决策
D4：仅位图 / PDF 且存在激活文本 tab 时提供）：复用命令面板
`\includegraphics` 条目模板与 `snippetInsert` 占位符 Tab 跳转通路，
路径参数预填相对项目根的正斜杠路径（graphics 路径相对编译 cwd，与
当前编辑文件位置无关，决策 D8），缺 `graphicx` 时经
`notifyMissingPackage` 自动补宏包声明；多文件仅汇总提示。上传中入口
按钮呈 loading 态防重复提交；viewer 无上传入口（canWrite 门控 +
后端写权限双保险）。

配套的树扫描行为：文件树收录条件为「非隐藏 + 非编译产物」（决策
D2/D7，黑名单见 [backend-core.md](../backend-core.md) project_manager
一节），任意格式资源（上传或 zip 导入自带）均在树中可见、可预览类型
点击即预览；未知类型点击树条目 toast 提示不支持预览（决策 D12，不做
静默无响应）。

文件行另有「设为主文件」hover 星标按钮（仅 `.tex` 且非当前主文件时
显示；当前主文件行常驻实心星标识，任意深度子目录各自比对
`node.path === mainFile`）。点击走 `POST /api/project/set-main-file`
（手动主文件持久化见后端一节），成功后同步全局 `mainFile` 使星标
即时迁移；viewer 无此入口（canWrite 门控 + 后端写权限双保险）。

### 图片与 PDF 只读预览

文件树点击 `.pdf` 或常见位图（前端 `isPreviewableFilePath()` 与后端
`PREVIEWABLE_SUFFIXES` 双白名单把关）经 `GET raw-file` 拉取 blob URL，
在中栏编辑器区上方以只读覆盖层展示（标题栏显示相对路径 + 关闭按钮）：
PDF 走 pdf.js 渲染（`PdfJsPreview`，editor v3 阶段一替换 iframe，见下文
SyncTeX 一节），位图走 `<img>` 居中 contain。位图/PDF 的渲染分支抽为共享
组件 `BinaryFilePreview`（填满父容器高度的纯渲染契约，加载文案经
`loadingKey` 定制），编辑器覆盖层（`FilePreviewOverlay`）与工作台页
（WorkspacePage 文件树点击预览）共用同一实现。预览不进入 CM6 缓冲区：
无脏跟踪、不写 URL `?file=`；URL 直达 `?file=xx.pdf` 刷新时恢复预览。
blob URL 在切换文件 / 关闭 / 卸载时就地 revoke，竞态败者（响应晚于
路径切换）不入 state 直接回收；打开文本文件成功时自动收起覆盖层。
白名单刻意排除 `.svg`（inline 提供用户上传的 SVG 存在存储型 XSS 风险）
与 `.eps`（浏览器无法原生渲染）。

### 三种建议应用形态（§7.2 / 决策 Q23）

| 形态 | 触发 | 实现 |
| --- | --- | --- |
| 插入光标处 | snippet 卡「插入光标处」按钮 | CM6 transaction 在光标处插入文本 |
| 替换选区 | snippet 卡「替换选区」按钮（有选区才可用） | CM6 transaction 替换选区范围 |
| diff 卡片逐卡审阅 | diff 卡展开预览 + 「应用到缓冲区」 | `applyUnifiedDiff()` 按 hunk 上下文锚定匹配（行号漂移容忍），单趟正向组装应用；任一 hunk 无法定位、窗口内存在多处可能匹配（歧义）或 hunk 源区间相互重叠时均整体失败，不落半套改动。同锚点的相邻「纯插入 + 替换」组合按 git 语义放行（插入紧贴替换块前/后边界合法，插入锚点落入替换区间内部仍判重叠） |

应用动作只改缓冲区并置脏标记；viewer 可问答但应用按钮禁用。
多缓冲区下建议卡片携带发送时的来源 filePath（随消息记录），活跃 tab
不匹配时禁用应用按钮并提示切换，不自动切 tab（v2 决策 Q17，纯前端）。
应用成功后调用 apply-suggestion 端点逐条（index 级）登记应用标记。

### 助手 DDG 挑战侧栏内完成（editor v3 阶段五a）

`useEditorAssistant` 复用 Writing Chat 的 UI 无关通道
`useWritingChatCaptcha`（内部调 `POST /api/search/ddg-challenge`）：
SSE `captcha_challenge` 事件经 `reduceAssistantCaptcha` 归一化后写入
挑战状态，`AssistantPanel` 渲染共享 `CaptchaDialog`（阶段五a 从
pages/writing-chat/ 提升到 components/，Writing Chat 页同步改导入，
文案沿用既有三语键零新增），挑战在面板内提交/放弃。`done` 归约对
`captcha_pending` 终流形态删除占位消息（后端未持久化 assistant 回复，
避免刷新后出现"幽灵"空消息）并照常回写会话 id（user 消息已在服务端
落盘）；`captcha_challenge` 事件同时清空工具指示器，不留滞留状态。
挑战解决后由用户手动重发原提问（无自动续流，与 Writing Chat 同语义）。

### 工具确认卡（editor v3 阶段五b）

联网/学术工具调用先经侧栏确认卡放行：`tool_pending` 事件经
`reduceAssistantPendingTool` 归一化写入 `editorStore.assistantPendingTool`
（时间戳由 hook 副作用层补盖），`AssistantPanel` 渲染 `ToolConfirmCard`
（工具名 + 查询/URL/文献 ID 摘要，三语）。确认/取消动作由
`useEditorAssistant` 接 Writing Chat 既有 confirm-tool / cancel-tool
端点（静默 404：过期或已决议一律清卡不报错），确认后同一 SSE 流内
续流，无需重新发起请求。卡片内建 TTL 120s 过期视觉态（与后端
`_TOOL_CONFIRM_TTL_SECONDS` 对齐，超时按钮禁用并灰色化，等待后端
`tool_cancelled` 事件收尾）；`tool_pending` / `tool_cancelled` /
`done` / `error` 均清工具指示器与卡片，不留滞留状态。确认对象是
检索而非写盘，viewer 亦可确认（无 canWrite 守卫，与写工具确认的
刻意差异）。pending 期间流仍在途（`assistantStreaming` 为 true），
输入框禁用天然防并发轮次。

### 语法高亮语言层（editor v3 阶段六a / 决策 Q14；六b 折叠·缩进·括号匹配）

`language.js` 保持「语言包进入编辑器的唯一入口」契约，导出改为
`latexLanguageExtension(suffix)` 并新增 `fileSuffixOf(path)` 后缀提取；
`LaTeXEditor` 以激活 tab 的文件后缀传参（进入 useMemo 依赖，仅随后缀
类变化触发扩展重配置）：`.tex` / `.sty` / `.cls` 返回自研 Lezer
LanguageSupport，`.bib` 及未知后缀沿用 stex StreamLanguage（自研最小
bib 语法列为可选尾项）。语言实例模块级缓存，跨 EditorState 复用；
`latexFoldingExtension()` 经同一入口转发折叠 UI 扩展组（应用层按
`codeFolding` 偏好条件挂载）。

**latexGrammar 包**（`lib/editor/latexGrammar/`，全部 MIT 自研，不引入
AGPL 依赖）：`latex.grammar` grammar 源 + `verbTokens.js` 外部分词器 +
`highlight.js` tags 映射 + `folding.js` / `indentation.js` 纯函数层 +
`brackets.js` 配对 prop 与配对高亮 + `index.js` LanguageSupport 组装 +
`build.mjs` 生成脚本。`@lezer/generator`（MIT，devDependency）经 npm
`predev` / `prebuild` / `pretest` 钩子在 dev / build / test 前把 grammar
编译为 parser.js / parser.terms.js（生成物不入库，已列 .gitignore，clean
checkout 直接构建可重复）。P0 子集范围：行注释（`\%` 豁免、`\\%` 为真
注释）、命令（含 `*` 变体与 `{}` / `[]` 参数）、四种数学定界（`$...$` /
`$$...$$` / `\(...\)` / `\[...\]`）、`\begin`/`\end` 环境、分组、上下标、
分节命令（part/chapter/section/subsection/subsubsection/paragraph 含
星号变体）映射 heading tag；verbatim 类环境（verbatim / verbatim* /
Verbatim / Verbatim* / lstlisting / minted，与 `outlineScan` 同口径）与
`\verb` 任意定界符参数整体原样吞掉。`\verb` 定界符「两侧同字符」无法用
CFG 表达，由 `verbTokens.js` 外部分词器实现（以 `{ fallback: true }`
声明——主 token 表已产出 token 时外部分词器默认不再被询问），行界与
非法定界符口径对齐 `outlineScan.stripVerbSpans`。明确不追求 catcode 级
正确与 `$` 配对零误判，依赖 Lezer 错误恢复（error-insensitive）保证
畸形输入可解析、不崩溃。高亮着色采用「叶子包裹」模式（只对恰好覆盖
单 token 的节点打 tag，容器节点不着色），tags 与 stex StreamLanguage
视觉逐类对齐，回归对照清单见 `test/editorLatexGrammar.test.mjs` 头注；
`latexHighlightStyle` 据新增 heading tag 补充 `--code-token-title` 映射。
语言层带 `commentTokens: { line: "%" }` 行注释切换数据。

**阶段六b（折叠 / 环境体缩进 / 括号匹配精确化）**：全部在组装层接线，
不改动 grammar 树形状——`\begin{env}` 与 `\end{env}` 是兄弟节点且
「环境名相同才配对」无法用 CFG 表达（强行加 Environment 包裹规则会把
`\begin{a}…\end{b}` 贪心错误配对、让未闭合环境吞掉后文），而 CM6 的
foldNodeProp / indentNodeProp 树路径机制都要求包裹节点存在于祖先链上，
故按 CM 官方语言包惯例在 `parser.configure` / LanguageSupport 组装层
挂载，配对与缩进逻辑落为零 `@codemirror` 依赖的纯函数层（node --test
直测）。折叠区间口径：环境 = `\begin` 行尾 → 匹配 `\end` 行首（起止
同行 / 未配对 / 无名 → 不可折叠）；分节 = 命令行尾 → 下一同级或更高级
分节命令行首（无后继 → 文档尾；subsection 属于 section 内部，不关闭
其区间）；verbatim 类环境有跨全环境的 `VerbatimEnvironment` 包裹节点，
走 CM 原生 foldNodeProp 路径。折叠 / 配对前向扫描带「命中即提前退出 +
扫描字符上限 + 按树记忆化」护栏，缩进的「最内层开环境」回溯带节点
访问上限（2MB 文档抽样查询无 O(n²) 劣化，耗时数据点见测试日志）。折叠
占位符显示环境名 / 分节标题摘要（`⟨itemize⟩` / `⟨标题…⟩`，无锚点回落
`⋯`，点击展开）。环境体自动缩进经 indentService（优先级高于 CM 树路径
缩进，返回 null = 保持现状）：环境体内换行缩进 = 最内层开环境 `\begin`
列 + 缩进单位（沿袭项目现状——未显式设定 indentUnit，即 CM 默认 2 空格），
行首为 `\end` 时与 `\begin` 起始列对齐，`languageData.indentOnInput`
（`/^\s*\\end\b/`）让打出 `\end` 时自动回正该行（与 `\begin` 打字机补
`\end` 联动）；verbatim 体内返回 null 原样保留。括号匹配精确化：把
`{}` / `[]` / `()` / `$` / `$$` / `\[` `\]` `\(` `\)` 定界节点经
`NodeProp.closedBy` / `openedBy` 声明配对（CM6 既有 bracketMatching 的
matchBrackets 优先读 prop 并在同一父节点兄弟序列内配对——花括号不跨出
Group / MathGroup 作用域、`$` 定界不跨对吞并、verbatim 体内无括号节点
天然豁免，配不上就不高亮、不做文本回退猜测）；`\begin{env}` ↔
`\end{env}` 名称配对由 ViewPlugin 经 folding.js 求配对并复用 CM 的
`cm-matchingBracket` 样式类（与既有 bracketMatching 并存，二者装饰对象
不同节点）。`codeFolding` 偏好（默认开，白名单缺省补齐不升版本，设置
弹层三语开关，存档 tab 失配重建即时生效）控制折叠 gutter 挂载；`.bib`
缓冲区无折叠区间、行为与现状一致。

### 编译联动（§8 / 决策 Q18 + v2 Q16）

工具栏「编译」：先解析编译目标（优先项目主文件，无主文件时回退当前
激活的 `.tex`；解析失败直接提示并跳过，不先行落盘）；多缓冲区下编译前
依次自动保存**所有**脏 tab（tab 条顺序，任一 409 即中断、切换到归属
tab 进入冲突弹窗，陈旧确认弹窗同理），全部成功后继续。
编译全程持在途序号守卫：双击竞态下过期响应整体丢弃并自回收其 blob
URL。编辑器发起的编译恒传 `synctex=true`（editor v3 决策 Q3，供
SyncTeX 正反向查询；编译页 / git_diff 等调用方默认 false 零变化），
并恒传 `clean=true`——编译前先由后端清理项目内 `.aux/.log/.fdb_latexmk`
等可再生产物（详见 [compile.md](compile.md)），避免 latexmk 依上次失败
状态短路为 "Nothing to do" 且提示无具体错误，同时保证失败日志始终来自
本次运行。
刷新恢复既有产物：编译状态与 PDF blob URL 本就不跨刷新持久，页面加载
后 `useEditorCompile` 主动按编译目标（主文件，缺省回落激活 `.tex`）请求
既有 `/compile/pdf` 产物，命中即直接载入预览（面板提示「正在预览已有的
编译产物」），从未编译（404）静默回落空态；主文件切换时按需重取。
用户发起过编译后恢复逻辑退出，PDF 生命周期完全归编译流程管理（在途
竞态经 compiledRef 守卫，迟到响应自回收 blob URL）。既有产物无
`.synctex.gz` 时正向同步查询失败，按既有 Q6 口径静默不打断写作。
编译结果与 PDF 预览（复用 `PDFPreview` 与既有
compile API）在右栏「编译与预览」Tab 展示；右栏可在该 Tab 与 AI 助手
之间切换并整体折叠。PDF 预览左下角提供「全屏查看」按钮，全屏覆盖层
（标题栏 + 退出按钮）挂在页面级——右栏 aside 带 transform 裁切，
`fixed` 覆盖层须放在面板外才能铺满视口——交互对齐 Git 差异页，
Escape 亦可退出。收起态呈现为整条可点展开的「书脊」——卡片同源
外观、竖排文字标注当前隐藏面板名；展开/收起动效按壳层宽度 → 内容
淡入左滑 → 书脊浮现三层编排（`cubic-bezier(0.22,1,0.36,1)`），
焦点在头部折叠按钮与书脊间交接，`prefers-reduced-motion` 下全部直切。

### SyncTeX 正反向同步（editor v3 阶段一 / 决策 Q1–Q6）

PDF 预览由 iframe 整体替换为 pdf.js 渲染（`PdfJsPreview`，
`pdfjs-dist` Apache-2.0 经 `THIRD_PARTY_NOTICES.md` 登记）：渲染层
可控是正反向定位的前提——iframe 无法程序化滚动、无法取点击位置。
`pdfjs-dist` 仅经动态 `import()` 首次预览才加载（vite 独立分包
`vendor-pdfjs`，worker 资产经 Vite `?url` 引入），首屏零加载；
`getDocument` 需传入 `cMapUrl` / `cMapPacked` 指向预置 CMap 资源——
LaTeX 工具链（xdvipdfmx）嵌入的 CID 字体普遍无 ToUnicode（如 ctex
默认 Fandol 中文字体），pdf.js 翻译字体时按 `/CIDSystemInfo` 拉取
预置 CMap（实测 `Adobe-GB1-UCS2`），缺失时 `translateFont` 整体
失败、对应字形（典型表现：中文）不渲染，而桌面查看器直接按 CID
绘制字形不受影响；资源由 `src/lib/editor/pdfjsCmaps.build.mjs` 经
predev/prebuild/pretest 钩子从 `pdfjs-dist/cmaps` 拷贝到
`public/pdfjs/cmaps`（生成物不入库，与 latexGrammar parser 同模式）。
`PDFPreview` 对外 props 兼容 `{url}` 并新增可选 `forwardTarget` /
`onBackwardSync` 通道，GitDiff / 工作台 / 模板中心等既有使用点
自动受益于 pdf.js 渲染与最小工具条（± 缩放含 Ctrl+滚轮、适宽、
页码输入；无文本层），缩放态跨编译（url 更新）保留。

- **后端**（compile 模块，详见 [compile.md](compile.md)）：编译链路
  `-synctex=1` 按请求可选追加；`POST /api/compile/synctex/forward`
  （源码位置 → PDF 页面矩形，72dpi 点单位、左上原点）与
  `POST /api/compile/synctex/backward`（PDF 点位 → 源码相对路径 +
  行列）经 SandboxRunner 零写盘执行 `synctex view` / `synctex edit`，
  响应不含绝对路径（Q1：不用前端 JS 解析 `.synctex.gz`）。
- **正向（Q4+Q6）**：光标位置自动跟随——stats hub 光标通道快照只进
  ref（键入零页面重渲染），尾随防抖约 800ms 后在「预览可见（编译 tab
  展开或全屏）且有编译产物」时发查询；缓冲区行号直发（脏缓冲区偏差
  容忍不补偿），失败静默、不做偏好开关。命中记录经 props 下发到
  `PdfJsPreview`：滚动到对应页并绘制高亮矩形浮层（点 → CSS 像素
  换算，纯函数 `synctexMath.js`），下一次 forward 替换。
- **反向（Q5）**：PDF 内**双击**取点（单击保留给滚动/选择习惯）——
  pdf.js `viewport.convertToPdfPoint` 后按 synctex 左上原点约定做
  `y = pageHeight − y` 换算，回调 EditorPage 查询 backward 端点；
  命中即复用编译错误跳转的「开 tab + 行定位」通路（文件未打开时经
  `pendingErrorJumpRef` 延迟到换档后），查询失败 toast 三语提示。

### 实时语法诊断（editor v3 阶段三 / 决策 Q9）

编辑停顿约 500ms 后对激活缓冲区做轻量语法检查，以内联波浪线 + 悬停提示
呈现——不进编译错误列表、不阻断编译（chktex 镜像集成为可选增强不排期，
触发条件为纯模块方案实测误报率不可接受时再评估）。

- **诊断纯模块** `lib/editor/diagnostics.js`（node --test 直接单测）：
  `computeDiagnostics(docText, formatMessage?)` 单趟 O(n) 状态机扫描
  （无嵌套量词正则；主线程护栏：`\end` 栈内查找经「环境名 → 出现次数」
  索引 O(1) 判空防深栈平方退化、超限且双栈皆空后只计数不构建条目、消息
  仅对返回的 ≤ 200 条组装），输出 `{ from, to, severity, rule, params,
  message }`（from/to 为 CM6 偏移），上限 200 条——超出按文档位置截断并
  在首条标注（`truncated`/`total` 字段 + message 附截断提示，total 恒为
  精确总数）。规则两条：
  - `unmatched-env`（error）：`\begin`/`\end` 栈匹配，环境名扫描思路与
    `formulaHoverScan` 同源（命令名全读，杜绝 `\endnote` 误判 `\end`）。
    `\end{env}` 与栈顶同名弹出；命中更深层同名时其上内层环境全部按未闭合
    报告并整段弹出（LaTeX 要求环境正确嵌套）；栈内无同名报 stray `\end`；
    文档末尾栈内残留按未闭合报告（定位在各 `\begin` token 区间）。环境名
    本行未闭合（畸形或键入中途）按已敲出前缀继续栈匹配、不闪报。
    verbatim 类环境（verbatim / verbatim* / Verbatim / Verbatim* /
    lstlisting / minted，与 `outlineScan` 同一保守清单）整体豁免：内容全
    字面（注释、花括号、`\begin` 均为普通文本）只找 `\end{同名}`；未闭合
    的 verbatim 本身仍在文末报告，其后内容随之字面化（抑制级联误报）。
  - `unbalanced-braces`（warning）：全文档花括号栈计数——多余的 `}` 定位
    所在行（每行最多一条）；文末未闭合 `{` 定位最内层 opener（params
    携带未闭合总数）。豁免：注释（`stripComment` 同源口径：非转义 `%` 起
    注释、`\%` / `\\%` 奇偶判定）、`\verb` / `\verb*` 参数（`outlineScan`
    同口径：定界符为字母/空白视为非法形态不豁免、参数不跨行）、转义
    `\{` / `\}`、URL 字面量（`\url` / `\path` / `\href` 第一个花括号参数，
    与后端 `latex_literals.py` 同源：参数内 `%20` 等为字面字符不再起注释，
    花括号转义感知配对整体吞掉）。
- **诊断消息三语**：纯模块只产出 rule + params（formatMessage 注入组装
  message，缺省英文兜底文案保证 node --test 可测）；接线层
  `lib/editor/liveLint.js` 按 `components:editor.diagnostics.*` 文案表
  组装（i18n 每次诊断运行时求值，语言切换后下一次诊断生效）。
- **CM6 接线**：`@codemirror/lint`（MIT）新增为显式依赖并登记
  `THIRD_PARTY_NOTICES.md`；`linter(..., { delay: 500 })` 模块级单例随
  `liveDiagnostics` 偏好条件挂载，挂载/失配重建遵循 selectionMatches
  先例（激活 tab 经包装层重配置即时生效，存档 tab 换入时 config 失配
  重建）。
- **偏好通道**：`EDITOR_PREFERENCES_DEFAULTS` 增 `liveDiagnostics: true`
  （默认开），经 `sanitizePreferences` 布尔白名单清洗并随导入导出载荷
  分发；不升 persist/导出 version——布尔新键走白名单缺省补齐，v1/v2
  存档与导入载荷天然兼容，无迁移转换可做。
- **设置入口**：`EditorSettingsDialog` 增「实时语法诊断」开关（三语
  i18n，含提示文案）。

### 跨文件大纲与公式固定预览卡（editor v3 阶段四 / 决策 Q10 + Q11）

**跨文件大纲**：大纲面板增加「当前文件 / 本项目」双视图切换（视图状态
提升到 EditorPage，左栏 tab 切换卸载面板时不丢失选择）；当前文件视图
行为完全不变。

- **后端数据源**：`GET /api/project/outline` 全项目 `.tex` 分节扫描
  （见后端接口表：正则口径与前端 `outlineScan.js` 同源、`asyncio.to_thread()`
  调度、响应只含相对路径）。项目级缓存以项目根为键、封顶逐出，save-file
  落盘成功后失效——沿袭 latex-symbols 引用补全「项目为键 + 保存失效」
  的缓存模式；绕过 save-file 的磁盘直改在下次保存前不重扫（同一取舍）。
- **前端数据层**：`useEditorOutline` 按 `view` 参数分流——当前文件视图
  沿用本地缓冲区解析（防抖 300ms 重扫、换档立即重扫）；本项目视图切入
  时拉取、任一 tab 保存基线（`savedBuffer`）变化后防抖 300ms 重拉
  （订阅命令式、快照字符串比对，保存风暴合并为一次重拉），拉取失败
  静默保持旧分组。分组展示结构由纯模块 `outlineProject.js` 的
  `groupProjectOutline(files, activePath)` 计算：按文件平铺分组
  （`\input`/`\include` 树状排序明确不做）、激活文件组置顶、其余组按
  相对路径字典序、空条目组剔除。
- **交互**：激活文件组默认展开，其余组折叠、点击文件头（basename +
  条目数角标）切换展开，数据重拉后回落默认；点击条目复用编译错误跳转
  的「开 tab + 行定位」通路（`pendingErrorJumpRef` 换档延迟机制），
  未打开的文件经 `doOpenFile` 打开后定位到行首并高亮。
- **验收对齐**：保存后大纲缓存失效重扫；当前文件视图行为不变；端点
  响应无绝对路径（`tests/test_api/test_editor_outline.py` 覆盖）。

**公式固定预览卡**（`FormulaPreviewCard`）：编辑区底部、状态栏上方悬浮
卡片（不进右栏 tab——右栏已被编译/助手占用）。订阅 stats hub 光标通道
（监听器无参调用、快照回调内 `getSnapshot()` 回读，快照只进 ref 不触发
页面重渲染），尾随防抖约 400ms 后取激活缓冲区光标所在公式渲染：
`lineColToOffset`（`formulaHoverScan.js` 纯函数）把 1 基行/列快照换算为
文档偏移，`findFormulaAt` 识别公式区域（行内/行间/`\(\)`/`\[\]`/显示
环境同一口径），KaTeX 经 `formulaHover.js` 导出的 `loadKatex` 单例与
`KATEX_RENDER_OPTIONS`（`trust: false` XSS 约束单一来源）渲染，递增
序号丢弃过期渲染。与悬停浮层的策略差异：固定卡常驻编辑区，无公式/
KaTeX 渲染失败时**静默隐藏**（不显示错误文案——常驻场景下错误提示反而
构成噪声）。

### 检查结果保鲜（§8 / 决策 Q13）

`useParagraphCheck` 为每条检查结果打上检查时的文件 MD5 版本戳
（`check_md5`）；结果就绪或从 persist 恢复后逐文件比对当前 MD5
（`/api/project/file-md5`，各文件并行请求；版本戳集合未变化的重复
触发直接跳过），不一致的文件渲染通用 `StaleCheckBanner`
横幅。只提示不销毁既有结果；MD5 获取失败不误报。

## 配置（§5.3）

```json
"editor": {
  "enabled": true,
  "assistant_enabled": true
}
```

`enabled` 控制编辑器整体（工作台入口隐藏 + `/editor` 路由守卫提示）；
`assistant_enabled` 为独立子开关，管理员可在保留编辑器的同时单独关闭
AI 面板。四处配置同步规范照 AGENTS.md 执行；`/api/config/status` 的
`features` 同时透出 `editor` 与 `editor_assistant` 两个布尔值。

## 测试

- 后端 `tests/test_api/test_editor_file_io.py`：整文件读写 / 409 结构 /
  字节保真 / invalid_encoding / 事件循环保护（无 LLM）
- 后端 `tests/test_api/test_editor_file_ops.py` 与
  `tests/test_multiuser/test_editor_file_ops.py`：文件树新建/上传/删除/
  移动/新建目录——
  创建落盘与响应携带重扫 file_tree（根/子目录）/ 重复 409 `file_exists` /
  白名单外后缀与路径穿越拒绝 / 父目录缺失 `parent_not_found` /
  超长 413 / 损坏内容 400 / 删除与树同步 / 主文档保护
  `main_file_protected`（换主文件后放行）/ 目录递归删除与空目录删除 /
  含主文件目录删除 400 `main_file_protected` / 隐藏目录删除拒绝 /
  项目根本身删除 400 `invalid_path` / GET /file-tree 缓存刷新；
  上传任意后缀成功（树含新文件，根/子目录）/ 超限 413（落盘前拦截）/
  冲突 409 / 隐藏目录·点开头·控制字符·空文件名·反斜杠分隔符拒绝 /
  产物黑名单不进树（含 `.synctex.gz` 双后缀命中，资源文件保留）/
  任意非隐藏后缀删除成功 / raw-file 对非白名单后缀仍拒绝；
  移动：文件 → 子目录 / 根 / 文件夹整棵子树随迁（嵌套内容随迁）/
  同名文件与同名目录 409 `target_exists` / 自身与子孙 400 `into_self` /
  父目录缺失 400 `parent_not_found` / 源缺失 404 `file_not_found` /
  隐藏路径 from·to 拒绝 / 移动主文件后记录跟随（含 `./` 前缀规范化）/
  移动含主文件的文件夹后前缀改写 / 无关路径记录不动；
  新建目录：根 / 子目录成功 / 目录名被文件占用 409 `target_exists` /
  父缺失 400 / 非法名与隐藏名拒绝；
  多用户侧 owner 创建/上传/删除/移动/建目录成功且**不产生
  workspace_edit_ops 记账**（move 另无配额变化）、viewer 写保护 403
  （含上传/移动/建目录）、上传配额预检 413（落盘前拦截）、
  CSRF/未登录 401、路径脱敏
- 后端 `tests/test_api/test_editor_assistant.py`：上下文注入纯函数、
  建议提取、SSE 端点（Mock LLM，严禁真实网络）、开关把关、apply 标记、
  工具白名单执行前硬校验（幻觉/越权工具名被拒不执行）、`_get_editor_tools`
  三源汇聚与按源独立容错及开关矩阵（关闭时工具不可达）、
  DDGChallengeError 分支终流（captcha_challenge + done(captcha_pending)、
  本轮工具消息不持久化）
- 后端 `tests/test_api/test_editor_format_latex.py`：format-latex 纯转换
  （Mock `format_latex_source`，无真实 latexindent 子进程）/ changed 标记 /
  413 上限 / invalid_workspace / 500 format_failed / 零写盘验证
- 后端 `tests/test_api/test_compile_synctex.py`：SyncTeX 正反向端点
  （Mock 沙箱执行，无真实 synctex 二进制 / docker / 网络）与
  `-synctex=1` 追加规则（synctex 默认 false 时编译命令零变化）/
  pdfa 组合忽略 / view·edit 输出解析 / file_path 越界拒绝 /
  多用户输出脱敏 / 响应无绝对路径
- 后端 `tests/test_api/test_editor_outline.py`：跨文件大纲端点
  （无 LLM 无沙箱纯文件操作）——多文件分组 / 相对路径与响应无绝对路径 /
  分节口径（星号变体、可选参数、注释与 verbatim 剔除、标题净化）/
  项目级缓存命中与 save-file 保存失效（绕过 save-file 的磁盘直改
  保持旧扫描）/ 非 .tex 排除 / 隐藏目录跳过 / 符号链接逃逸拒绝 /
  空项目 / invalid_workspace
- 前端 node --test：editorPreferences（含导入导出校验与自定义片段条目级
  清洗 / v1·v2 载荷兼容）、customCommands（片段清洗 / 归一化 / 合并 /
  面板分组 / 网格标记端到端）、editorApplyDiff、
  editorTabs（tab 状态机归约与会话选择器）、editorFileMove（拖拽移动
  路径重映射：文件精确命中 / 目录前缀覆盖子树且 `/` 边界防误伤 /
  buffer·baseMd5·脏标记原样保留 / 激活位与预览归属路径同口径 /
  无命中空转，`getBaseName`·`joinProjectPath` 落位拼接，
  `validateNewDirName` 目录名消毒，移动与新建目录错误码三语条目
  一一对应）、editorSessionPersistence
  （序列化清洗 / LRU 配额淘汰 / 项目键隔离 / localStorage 遗留收养迁移）、
  editorChangeHistoryDiff（行级 diff 边界与护栏）、editorChangeHistory
  （色条字段基线注入 / 橙绿集合转移 / 冻结恢复语义）、editorStats
  （字数统计口径与统计 hub）、editorTabViews（登记表）、
  editorFiles / editorMatcher / editorSnippetInsert / editorPreambleScan /
  editorWrap（包裹纯逻辑与直包键位一致性）/ editorBeginClose（\begin
  配对纯决策与事务行为）/ editorSearchPanel（面板短语映射与语言热更新）/
  editorOutlineScan（大纲扫描边界）/ editorOutlineProject（本项目视图
  分组置顶排序与光标快照 → 文档偏移换算）/ editorFormulaHoverScan（公式识别边界）/
  editorDiagnostics（实时诊断：环境栈匹配 / 花括号计数、注释·\verb·
  verbatim·转义·URL 字面量豁免、200 条截断标注、消息格式化注入）/
  editorFormatApply（格式化应用决策、
  快捷键判定与 CM6 history 单步撤销）/ editorSynctexMath（SyncTeX 点→像素
  换算与左上原点翻转，含双击取点闭环）/ editorLatexGrammar（自研 Lezer
  语法解析树断言：注释 / 命令 / 数学区 / 环境 / verbatim 与 \verb 吞掉 /
  分节 heading / 畸形输入错误恢复，含 classHighlighter 高亮断言与
  stex 回归对照清单头注、2MB 全量解析耗时数据点）/
  editorLatexGrammarFeatures（六b 折叠区间 / 环境体缩进 / begin↔end
  配对与占位符摘要纯函数断言、openedBy/closedBy 配对 prop 与语言数据
  组装层断言、畸形输入收敛与 2MB 抽样查询回归）/ latexCommands（含
  wrap 类目 schema）/ i18nLocales 键集合一致性等
