# 编辑器生产力改造设计（editor v2）

> **状态：已实施。** 本文记录「编辑器从可用到生产力工具」改造的设计与决策依据，
> 供维护与查询；当前完整实现形态见 [modules/editor.md](modules/editor.md)。
> v1 设计见 [editor-design.md](editor-design.md)，v3 收口设计（已实施）见
> [editor-experience-design.md](editor-experience-design.md)。
> 代码注释中的「editor-productivity-design §N」与「决策 QN」均指向本文编号。
> 如行为与本文冲突，先修订本文再动代码。

## 1. 背景与目标

编辑器 v1 完成了"打开-编辑-保存-冲突"闭环与命令快速输入、AI 助手，但存在两个结构性限制，使其停留在"能用"而非"好用"：

1. **单文件缓冲区**：一次只能打开一个文件，与 LaTeX 写作天然多文件（主文件 + 章节 + bib）的工作流冲突；
2. **写作反馈缺失**：编辑过程中没有公式预览、错误行定位等反馈，一切都要等"编译一次看右栏"。

v2 以**多缓冲区基座**为先导，补齐编辑基本功与写作反馈闭环，把 `/editor` 页提升为可日常写作的生产力工具。

## 2. 范围与非目标

### 范围

- 多缓冲区（多标签页）基座：tab 条、per-tab 缓冲区状态、陈旧检测、关闭三选、刷新恢复、自动保存与编译/助手联动的多缓冲区语义；
- 编辑基本功：包裹命令、多光标、`\begin`/`\end` 自动配对、查找替换面板主题化与本地化；
- 大纲导航面板（当前缓冲区）；
- `\ref` / `\label` / `\cite` 引用补全（配套后端符号扫描端点）；
- 一键格式化（配套后端格式化端点，零写盘）；
- KaTeX 公式悬停预览；
- 编译错误列表与跨文件跳转（配套日志文件栈解析）；
- Notepad++ 借鉴包：修改行色条、底部状态栏、选中高亮匹配、会话持久化升级 localStorage。

### 非目标（本轮明确排除，v3 候选）

- stex StreamLanguage → MIT 许可的 Lezer LaTeX 语法（`language.js` 抽象层已预留唯一入口）；
- SyncTeX 正反向同步（需编译链路加 `-synctex=1`）；
- 用户自定义片段库；
- 实时语法诊断（与 Lezer 语法升级一并评估）；
- 实时协同编辑、冲突 diff 合并视图（沿袭 v1 非目标）。

> 勾销记录：「Lezer 语法」「SyncTeX 正反向同步」「用户自定义片段库」「实时语法
> 诊断」已由 v3 收口（[editor-experience-design.md](editor-experience-design.md)，已实施）；
> 「实时协同编辑、冲突 diff 合并视图」仍为非目标。

## 3. 拷问决策记录（grilling Q1–Q19）

> 本表是代码注释与 modules/editor.md 中「决策 QN」引用的锚点，保留作为设计初衷对照基线。

| # | 决策点 | 结论 |
| --- | --- | --- |
| Q1 | 改造范围 | P0（多缓冲区/大纲/引用补全）+ P1（基本功）全做，P2 中纳入 KaTeX 预览与编译错误跳转；P3 排除 |
| Q2 | 交付节奏 | 分阶段交付，每阶段独立可发布 |
| Q3 | 多缓冲区 UI | 中栏顶部 tab 条（标签 + 脏标记圆点 + 关闭按钮）；文件树点击 = 打开/激活 |
| Q4 | 陈旧缓冲区 | 激活 tab 时惰性比对 `file-md5`，不一致显示"磁盘已变更"标记，保存前提示；只提示不销毁；保存时 409 仍兜底 |
| Q5 | 关脏 tab | 三选弹窗：保存并关闭 / 放弃改动 / 取消；自动保存开启时默认"保存并关闭" |
| Q6 | 大纲面板 | 左栏"文件 / 大纲"双 tab；仅解析当前激活缓冲区；编辑防抖增量重扫；点击滚动定位；跨文件大纲留 v3（已由 v3 落地为"本项目"跨文件视图） |
| Q7 | 引用补全数据源 | 新增后端只读符号扫描端点，一次返回全项目 label/cite key；bib key 解析复用 `bib_parser.py` |
| Q8 | 格式化交互 | 直接全文替换缓冲区 + toast 提示 Ctrl+Z 可撤销；不做 diff 预览；后端薄端点包裹 `latex_formatter` |
| Q9 | KaTeX 形态 | 悬停浮层（约 500ms 延迟），katex 动态分块 import；右栏固定预览卡留 v3（已由 v3 落地为编辑区底部固定预览卡） |
| Q10 | 错误定位 | 右栏可点击错误列表 + 跳转定位（行首高亮）；不做行内波浪线（LaTeX 错误行号噪声大） |
| Q11 | 阶段划分 | 基座 → 基本功 → 大纲 → 补全 → 格式化 → KaTeX → 错误跳转 → Notepad++ 借鉴包 |
| Q12 | `\begin` 配对 | Overleaf 式打字机补 `\end`；该 `\begin` 曾配对过则不再补 |
| Q13 | 包裹命令 | Ctrl+B/I/U/K 直包 `\textbf`/`\emph`/`\underline`/`\texttt` + Ctrl+L 浮层菜单覆盖全集；数据进 `latexCommands` 的 wrap 类目，命令面板可搜 |
| Q14 | 刷新恢复 | 所有打开 tab（含脏缓冲区全文）写持久化存储，刷新整体恢复 + toast 告知；超配额 LRU 淘汰非脏 tab |
| Q15 | 自动保存作用域 | 所有脏 tab 各自停改 2.5s 落盘，per-tab 防抖器 |
| Q16 | 编译联动 | 编译前依次自动保存**所有**脏 tab，任一 409 即中断并进入该 tab 冲突流程 |
| Q17 | 助手 tab 守卫 | 建议卡片记录来源 filePath，活跃 tab 不匹配时禁用应用并提示切换；不自动切 tab |
| Q18 | 偏好开关 | 仅 `formulaHover` 与 `autoCloseEnv` 两个开关（默认开）；多光标、包裹快捷键不给开关（阶段八追加 `selectionMatches`） |
| Q19 | 错误跨文件深度 | 完整解析日志 `(./` 文件栈 + `l.N` 归属栈顶文件，支持跨文件跳转；归属歧义回落当前缓冲区并标注低置信 |

## 4. 实现层既定决策

- **多缓冲区模型**：单 CM6 实例 + 每文件独立 `EditorState` 交换（天然获得 per-tab 撤销历史与滚动位置；外部整缓冲区替换仍经 store 单向流入）。
- **多光标**：放开 `allowMultipleSelections`，默认开启，无偏好开关。
- **大纲条目**：`\section` / `\subsection` / `\subsubsection` / `\paragraph` 四级缩进展示。
- **KaTeX 支持范围**：行内 `$...$` / `\(...\)`，display `\[...\]` 与 equation/align 系环境。
- **格式化语义**：端点为纯转换（内容进出、零写盘、零配额影响），前端应用后只置脏，落盘仍走 save-file 单一路径；viewer 禁用入口。
- **URL `?file=`**：跟随激活 tab；刷新时与会话恢复结果合并（恢复全部 tab 集合，`?file=` 指定者激活）。
- **后端撤销/重做**：工具栏按钮绑定活跃 tab 的文件；"脏改动时禁用后端历史"的既有规则不变。
- **tab 数量**：不设硬上限，存储配额由 LRU 淘汰策略兜底。
- **全部保存**：tab 条在多于一个脏 tab 时提供"全部保存"入口。

## 5. 总体设计

### 5.1 多缓冲区基座

`editorStore` 的 `editorSession` 切片为多缓冲区模型：per-tab 维护 `{ filePath, buffer, baseMd5, dirty, stale, cursor/scroll }`，激活 tab 的 content 经单点订阅流入 CM6；tab 间切换 = `EditorState` 交换，撤销历史与滚动位置随 `EditorState` 天然隔离。

- **陈旧检测**：激活 tab 时比对磁盘 MD5（复用"MD5 比对 + 只提示不销毁"保鲜模式），不一致时 tab 显示"磁盘已变更"标记，保存时 409 冲突流程兜底；
- **关闭脏 tab**：三选弹窗（保存并关闭 / 放弃改动 / 取消）；
- **刷新恢复**：tab 集合与脏缓冲区全文连同 `baseMd5` 持久化（阶段八升级为 localStorage v2 载荷，重启浏览器可恢复；超配额 LRU 淘汰最久不活跃的非脏 tab）；
- **自动保存**：per-tab 防抖器，所有脏 tab 停改 2.5s 落盘（`autoSave` 开启时）；
- **编译联动**：编译前依次自动保存所有脏 tab，任一 409 即中断并进入该 tab 的冲突流程；
- **AI 助手**：建议卡片携带来源 filePath，应用时活跃 tab 不匹配则禁用并提示切换。

### 5.2 后端新增

| 接口 | 语义 |
| --- | --- |
| `GET /api/project/latex-symbols` | 只读符号扫描：遍历项目内 `.tex` 提取 `\label`（含行号）与 `\cite` key，`.bib` 经 `bib_parser.py` 提取条目 key；路径校验复用 `resolve_project_path()`，扫描经 `asyncio.to_thread()`；响应只含相对路径（多用户脱敏）；零 LLM |
| `POST /api/project/format-latex` | 纯转换：接收缓冲区内容，内部复用 `latex_formatter`（latexindent 优先、内置兜底，外部命令经 SandboxRunner）返回格式化结果；不落盘、不涉路径、零配额影响 |

### 5.3 前端增量

- **编辑基本功**：包裹命令快捷键 + Ctrl+L 浮层菜单（数据源为 `latexCommands` wrap 类目，进四路匹配索引与命令面板）；放开多光标；`\begin{env}` 打字机补 `\end`（曾配对不再补）；CM6 搜索面板主题化 + 三语 i18n；
- **大纲面板**：`outlineScan.js` 纯模块解析分节命令，左栏双 tab，编辑防抖增量重扫，点击滚动定位；
- **引用补全**：`\ref{` / `\label{` / `\cite{` 触发的补全 source；符号缓存以项目为键，保存成功后失效重拉；
- **格式化**：工具栏按钮 + 快捷键，应用结果全文替换缓冲区并 toast 提示 Ctrl+Z 可撤销；
- **KaTeX 悬停**：CM6 hover tooltip 插件；katex 动态 import 分块（沿用 pinyin-pro / opencc-js 懒加载先例）；
- **编译错误跳转**：`logFileStack.js` 日志文件栈解析纯模块（`(./` 压栈、`)` 弹栈、`l.N` 归属栈顶文件、79 列 dewrap；歧义保守回落并标低置信），右栏错误列表可点击，跨文件跳转 = 联动打开对应 tab 并定位行首高亮；
- **Notepad++ 借鉴包**：行级 diff 纯模块（`changeHistoryDiff.js`，公共前后缀剪除 + Myers O(ND) + 防退化护栏）+ CM6 行装饰双色条（琥珀 = 未保存修改、翠绿 = 本会话已保存修改，基线复用 savedBuffer/openedBuffer，Ctrl+Z 到保存点精确清零）；底部状态栏（`stats.js` 中西文字数 + 发布订阅 hub，不经过页面 React 树）；`selectionMatches` 选中高亮全部匹配偏好；会话持久化升级 localStorage（脏 tab 携带 savedBuffer 的 v2 载荷 + sessionStorage 遗留收养迁移）。

### 5.4 偏好增量

`EDITOR_PREFERENCES_DEFAULTS` 新增 `formulaHover` / `autoCloseEnv` / `selectionMatches`（默认均开），随既有白名单清洗、导入导出校验、设置弹窗与三语 i18n 一并生效。**不新增 `config.json` 配置项**（无四处配置同步负担）；`editor.enabled` / `assistant_enabled` 服务端开关语义不变。
