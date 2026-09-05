# 网页编辑器模块 `editor/` + `/editor`

> 设计依据：[docs/editor-design.md](../editor-design.md)（含 grilling 决策记录
> Q1–Q26）、[docs/editor-productivity-design.md](../editor-productivity-design.md)
> （editor v2，八个阶段均已实施：阶段一多缓冲区基座、阶段二编辑基本功包、
> 阶段三大纲面板、阶段四引用补全、阶段五一键格式化、阶段六 KaTeX 公式
> 悬停预览、阶段七编译错误列表与跨文件跳转，以及阶段八修改行色条 /
> 状态栏 / 选中高亮匹配 / 会话持久化升级 localStorage，借鉴 Notepad++）
> 与 [docs/editor-experience-design.md](../editor-experience-design.md)
> （editor v3 写作体验收口；阶段一 SyncTeX 正反向同步、阶段二用户自定义
> 片段库已实施）。

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
│   ├── EditorToolbar.jsx             # 后端撤销重做 · 面板 · 格式化 · 编译 · 保存 · 设置
│   ├── EditorCloseTabDialog.jsx      # 关闭脏 tab 三选弹窗（保存并关闭 / 放弃改动 / 取消）
│   ├── EditorSettingsDialog.jsx      # 偏好开关 + 自定义片段管理（增删改 + 实时预览）+ 偏好导入/导出双通道入口
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
│   ├── formulaHoverScan.js           # KaTeX 悬停公式区域识别纯模块（行内/显示定界符 + equation/align 系环境，注释与转义边界）
│   ├── formulaHover.js               # CM6 hoverTooltip 公式悬停预览（katex 动态分块 import / 错误提示 / 主题化）
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

Zustand：`editorStore` 含三个切片——`editorPreferences`（persist v2：
autoSave / softWrap / autoCloseEnv / formulaHover / selectionMatches /
fontSize / customSnippets；v1 存档经 migrate 透传后按白名单清洗补默认
空数组）、`editorSession`（多缓冲区会话，
不持久化）、`editorAssistant`（助手会话 id 与消息视图，不持久化；
后端会话按项目 scope 隔离，切换文件延续会话）。

### 多缓冲区基座（editor-productivity-design §5.1，阶段一）

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
  （换档收起的存档 EditorState 保留建档时的语言，可接受边界）。

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

### 编译联动（§8 / 决策 Q18 + v2 Q16）

工具栏「编译」：先解析编译目标（优先项目主文件，无主文件时回退当前
激活的 `.tex`；解析失败直接提示并跳过，不先行落盘）；多缓冲区下编译前
依次自动保存**所有**脏 tab（tab 条顺序，任一 409 即中断、切换到归属
tab 进入冲突弹窗，陈旧确认弹窗同理），全部成功后继续。
编译全程持在途序号守卫：双击竞态下过期响应整体丢弃并自回收其 blob
URL。编辑器发起的编译恒传 `synctex=true`（editor v3 决策 Q3，供
SyncTeX 正反向查询；编译页 / git_diff 等调用方默认 false 零变化）。
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
- 后端 `tests/test_api/test_compile_synctex.py`：SyncTeX 正反向端点
  （Mock 沙箱执行，无真实 synctex 二进制 / docker / 网络）与
  `-synctex=1` 追加规则（synctex 默认 false 时编译命令零变化）/
  pdfa 组合忽略 / view·edit 输出解析 / file_path 越界拒绝 /
  多用户输出脱敏 / 响应无绝对路径
- 前端 node --test：editorPreferences（含导入导出校验与自定义片段条目级
  清洗 / v1·v2 载荷兼容）、customCommands（片段清洗 / 归一化 / 合并 /
  面板分组 / 网格标记端到端）、editorApplyDiff、
  editorTabs（tab 状态机归约与会话选择器）、editorSessionPersistence
  （序列化清洗 / LRU 配额淘汰 / 项目键隔离 / localStorage 遗留收养迁移）、
  editorChangeHistoryDiff（行级 diff 边界与护栏）、editorChangeHistory
  （色条字段基线注入 / 橙绿集合转移 / 冻结恢复语义）、editorStats
  （字数统计口径与统计 hub）、editorTabViews（登记表）、
  editorFiles / editorMatcher / editorSnippetInsert / editorPreambleScan /
  editorWrap（包裹纯逻辑与直包键位一致性）/ editorBeginClose（\begin
  配对纯决策与事务行为）/ editorSearchPanel（面板短语映射与语言热更新）/
  editorOutlineScan（大纲扫描边界）/ editorFormulaHoverScan（公式识别边界）/
  editorDiagnostics（实时诊断：环境栈匹配 / 花括号计数、注释·\verb·
  verbatim·转义·URL 字面量豁免、200 条截断标注、消息格式化注入）/
  editorFormatApply（格式化应用决策、
  快捷键判定与 CM6 history 单步撤销）/ editorSynctexMath（SyncTeX 点→像素
  换算与左上原点翻转，含双击取点闭环）/ latexCommands（含 wrap 类目 schema）/
  i18nLocales 键集合一致性等
