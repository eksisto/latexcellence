# 网页编辑器模块 `editor/` + `/editor`

> 设计依据：[docs/editor-plan.md](../editor-plan.md)（含 grilling 决策记录
> Q1–Q26）与 [docs/editor-productivity-plan.md](../editor-productivity-plan.md)
> （editor v2，阶段一多缓冲区基座、阶段二编辑基本功包、阶段三大纲面板、
> 阶段四引用补全与阶段五一键格式化已实施）。

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
| `POST /api/project/format-latex` | 一键格式化（阶段五 / 决策 Q8）：**纯转换端点**——接收缓冲区全文 `{content}`，复用 `latex_formatter.format_latex_source()`（latexindent 优先、内置轻量格式化器兜底，外部命令执行沿既有 SandboxRunner 规则与超时语义）返回 `{content, changed}`；不接收路径参数、不落盘、不进入配额记账、零 LLM；内容上限与 save-file 同量级（超限 413 `content_too_long`）；写权限依赖使 viewer 服务端拒绝；两层格式化器按契约"失败返回原样"（`changed=false`），意外异常收敛为 500 `format_failed` |
| `POST /api/project/save-file` | 统一保存：`latex_json_guard` 宽松损坏特征校验（不做括号/$ 配平检查）→ MD5 乐观锁（不符返回 **409** 结构化错误 `file_conflict`）→ 单用户推入 BackupManager 撤销栈 / 多用户记入 workspace_edit_ops 反向补丁快照 → 刷新项目用量记账，超限回滚 |

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

## 前端

```
frontend/src/
├── pages/EditorPage.jsx              # /editor 三栏布局、tab 条接线、编译联动、右栏 Tab、URL ?file= 双向、路由守卫
├── components/editor/
│   ├── LaTeXEditor.jsx               # CM6 受控封装（value/onChange）+ 多缓冲区 per-file EditorState 换档
│   ├── EditorTabBar.jsx              # 标签条（脏标记圆点 + 磁盘已变更标记 + 关闭按钮 + 全部保存）
│   ├── OutlinePanel.jsx              # 左栏「大纲」tab 内容（四级缩进条目 + 空态，点击经回调定位）
│   ├── EditorToolbar.jsx             # 激活 tab 路径/脏标记 · 后端撤销重做 · 面板 · 格式化 · 编译 · 保存 · 设置
│   ├── EditorCloseTabDialog.jsx      # 关闭脏 tab 三选弹窗（保存并关闭 / 放弃改动 / 取消）
│   ├── EditorSettingsDialog.jsx      # 偏好开关 + 偏好导入/导出双通道入口
│   ├── ConflictDialog.jsx            # 409 弹窗（动作作用于冲突归属 tab：放弃重载 / 剪贴板逃生 / 覆盖保存）
│   ├── CommandPalette.jsx            # 命令面板 + m×n 网格选择器二级步骤
│   ├── GridSizePicker.jsx            # 表格/矩阵网格拖选
│   ├── AssistantPanel.jsx            # AI 助手侧栏（SSE 对话 + 建议卡 + 来源 tab 守卫）
│   ├── StaleCheckBanner.jsx          # 检查结果过期横幅（各检查页复用）
│   └── …（BinaryFilePreview / CompilePreviewPanel / EditorRightPanel / FilePreviewOverlay / FullscreenPDFPreview）
├── lib/editor/
│   ├── tabs.js                       # 多缓冲区 tab 状态机纯归约（打开/激活/键入/保存回写/陈旧/关闭）+ 会话选择器
│   ├── sessionPersistence.js         # 会话 sessionStorage 持久化纯函数（序列化 / 白名单清洗 / LRU 配额淘汰 / 项目键隔离）
│   ├── tabViews.js                   # per-tab CM6 EditorState / 滚动位置登记表（单实例换档的数据底座）
│   ├── language.js                   # 语言包抽象层（stex StreamLanguage）
│   ├── files.js                      # 可编辑/可预览白名单 + 大小上限纯函数
│   ├── preferences.js                # 偏好默认值/白名单清洗/导出导入校验纯函数
│   ├── matcher.js                    # pinyin-pro 四路模糊匹配
│   ├── commandZh.js                  # 命令中文名按界面语言本地化展示
│   ├── completionTheme.js            # 补全弹层主题（索引卡式浮层 + 引导点）
│   ├── latexCompletion.js            # \ 与 \begin{ 行内补全 source（+ 阶段四 \ref/\label/\cite 引用 source）
│   ├── latexSymbols.js               # 引用补全符号缓存（项目为键 / 在途共享 / 保存失效）+ 候选映射过滤
│   ├── snippetInsert.js              # 模板插入 + 占位符 Tab 循环
│   ├── wrap.js                       # 包裹命令纯逻辑（多选区包裹计算）+ 直包快捷键键位
│   ├── beginClose.js                 # \begin{env} 打字机自动补 \end（transactionFilter 纯决策分层）
│   ├── searchPanel.js                # CM6 查找替换面板主题化 + 三语短语（EditorState.phrases）
│   ├── outlineScan.js                # 大纲扫描纯模块（分节命令解析 / 注释与 verbatim 排除 / 标题净化）
│   ├── formatApply.js                # 一键格式化纯模块（快捷键判定 / 应用决策 view·state·store / 单事务整文档替换）
│   ├── preambleScan.js               # 导言区扫描 + 宏包缺失检测
│   ├── applyDiff.js                  # unified diff 解析与上下文锚定应用纯模块
│   ├── assistantStream.js            # 助手 SSE 事件归约纯函数（占位消息 / 工具状态 / 会话 id，hook 与单测共用）
│   └── （命令数据库 data/latexCommands/ 按类目拆分 JSON + index.js 统一加载器，含 wrap 包裹类目）
└── hooks/useEditorBuffer.js          # 多缓冲区编排（打开/激活/保存/冲突/陈旧/自动保存/刷新恢复/项目切换守卫）
└── hooks/useEditorAssistant.js       # 助手 SSE hook（归约委托 assistantStream 纯函数 + 项目切换守卫）
└── hooks/useEditorOutline.js         # 大纲数据源（仅激活缓冲区，防抖重扫 + tab 切换立即重扫）
└── hooks/useEditorFormat.js          # 一键格式化编排（在途守卫 / 发起 tab 定位 / 应用决策分派）
```

Zustand：`editorStore` 含三个切片——`editorPreferences`（persist：
autoSave / softWrap / autoCloseEnv / fontSize）、`editorSession`（多缓冲区会话，
不持久化）、`editorAssistant`（助手会话 id 与消息视图，不持久化；
后端会话按项目 scope 隔离，切换文件延续会话）。

### 多缓冲区基座（editor-productivity-plan §5.1，阶段一）

`editorSession` 切片为多缓冲区模型：`tabs: [{ filePath, buffer, baseMd5,
dirty, stale, lastActive }]` + `activeTabPath` + `conflict`。tab 转移逻辑
（打开 / 激活 / 键入回写 / 保存回写 / 陈旧回写 / 关闭）全部是
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

### 编辑基本功包（editor-productivity-plan §5.3，阶段二）

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
  （换档收起的存档 EditorState 保留建档时的语言，可接受边界）。

### 大纲面板（editor-productivity-plan §5.3，阶段三）

左栏「文件 / 大纲」双 tab（决策 Q6）：大纲 tab **仅解析当前激活缓冲区**
（跨文件大纲留二期），条目为 `\section` / `\subsection` /
`\subsubsection` / `\paragraph` 四级缩进展示（含 `*` 星号变体）。

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

### 引用补全（editor-productivity-plan §5.3，阶段四 / 决策 Q7）

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

### 一键格式化（editor-productivity-plan §5.3，阶段五 / 决策 Q8）

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

### 图片与 PDF 只读预览

文件树点击 `.pdf` 或常见位图（前端 `isPreviewableFilePath()` 与后端
`PREVIEWABLE_SUFFIXES` 双白名单把关）经 `GET raw-file` 拉取 blob URL，
在中栏编辑器区上方以只读覆盖层展示（标题栏显示相对路径 + 关闭按钮）：
PDF 走 iframe，位图走 `<img>` 居中 contain。位图/PDF 的渲染分支抽为共享
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

### 编译联动（§8 / 决策 Q18 + v2 Q16）

工具栏「编译」：先解析编译目标（优先项目主文件，无主文件时回退当前
激活的 `.tex`；解析失败直接提示并跳过，不先行落盘）；多缓冲区下编译前
依次自动保存**所有**脏 tab（tab 条顺序，任一 409 即中断、切换到归属
tab 进入冲突弹窗，陈旧确认弹窗同理），全部成功后继续。
编译全程持在途序号守卫：双击竞态下过期响应整体丢弃并自回收其 blob
URL。编译结果与 PDF 预览（复用 `PDFPreview` 与既有
compile API）在右栏「编译与预览」Tab 展示；右栏可在该 Tab 与 AI 助手
之间切换并整体折叠。PDF 预览左下角提供「全屏查看」按钮，全屏覆盖层
（标题栏 + 退出按钮）挂在页面级——右栏 aside 带 transform 裁切，
`fixed` 覆盖层须放在面板外才能铺满视口——交互对齐 Git 差异页，
Escape 亦可退出。收起态呈现为整条可点展开的「书脊」——卡片同源
外观、竖排文字标注当前隐藏面板名；展开/收起动效按壳层宽度 → 内容
淡入左滑 → 书脊浮现三层编排（`cubic-bezier(0.22,1,0.36,1)`），
焦点在头部折叠按钮与书脊间交接，`prefers-reduced-motion` 下全部直切。

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
- 后端 `tests/test_api/test_editor_assistant.py`：上下文注入纯函数、
  建议提取、SSE 端点（Mock LLM，严禁真实网络）、开关把关、apply 标记
- 后端 `tests/test_api/test_editor_format_latex.py`：format-latex 纯转换
  （Mock `format_latex_source`，无真实 latexindent 子进程）/ changed 标记 /
  413 上限 / invalid_workspace / 500 format_failed / 零写盘验证
- 前端 node --test：editorPreferences（含导入导出校验）、editorApplyDiff、
  editorTabs（tab 状态机归约与会话选择器）、editorSessionPersistence
  （序列化清洗 / LRU 配额淘汰 / 项目键隔离）、editorTabViews（登记表）、
  editorFiles / editorMatcher / editorSnippetInsert / editorPreambleScan /
  editorWrap（包裹纯逻辑与直包键位一致性）/ editorBeginClose（\begin
  配对纯决策与事务行为）/ editorSearchPanel（面板短语映射与语言热更新）/
  editorOutlineScan（大纲扫描边界）/ editorFormatApply（格式化应用决策、
  快捷键判定与 CM6 history 单步撤销）/ latexCommands（含 wrap 类目 schema）/
  i18nLocales 键集合一致性等
