# 网页编辑器功能设计（editor v1）

> **状态：已实施。** 本文记录「网页编辑器 + LaTeX 命令快速输入 + 编辑器侧 AI 助手」的
> 设计与决策依据，供维护与查询；当前完整实现形态见
> [docs/modules/editor.md](modules/editor.md)，后续演进见
> [editor-productivity-design.md](editor-productivity-design.md)（v2，已实施）与
> [editor-experience-design.md](editor-experience-design.md)（v3，未实施）。
> 代码注释中的「editor-design §N」与「决策 QN」均指向本文编号。如行为与本文冲突，
> 先修订本文再动代码。

## 1. 背景与目标

编辑器之前的工作台以**段落只读视图**展示文件内容，AI 修改经逐段协议落盘，用户无法像 VS Code 那样手动编辑文件。v1 在同一前端内新增：

1. 全宽编辑器页，支持项目内文本文件的手动编辑、乐观锁保存与后端撤销回退；
2. 核心特色——**LaTeX 命令快速输入**：命令数据库支持中文、拼音全拼/首字母、英文模糊查找（如 `阿尔法` / `aerfa` / `alpha` 均可命中 `\alpha`），提供 `\` 行内补全与快捷键命令面板双入口，表格/矩阵类模板支持 m 行 n 列网格选择；
3. 编辑器侧 AI 助手面板（Copilot 式）：对话内核复用 Writing Chat 基础设施，修改建议应用到编辑器缓冲区而不直接落盘。

## 2. 范围与非目标

### 范围

- `/editor` 独立路由页（全宽三栏布局），工作台加"进入编辑器"入口；
- 可编辑文件白名单：`.tex` / `.bib` / `.sty` / `.cls`（常量便于扩展）；
- 手动保存为主（`Ctrl/Cmd+S` 与工具栏按钮），"自动保存"为个人偏好且默认关闭；
- MD5 乐观锁冲突处理：弹窗提示 + 一键放弃本地重载 + 本地内容复制到剪贴板逃生；
- 所有落盘走统一的 save-file 路径并接入多用户反向补丁撤销记账；
- 命令快速输入子系统（§6）；AI 助手侧栏（§7）；
- 桌面优先，不做移动端深度适配。

### 非目标（v1 明确排除）

实时协同编辑（Yjs）、用户自定义片段、冲突 diff 合并视图、助手接入联网搜索/学术检索。

> 「多标签页」「`\ref{}` / `\cite{}` 引用补全」「KaTeX 公式预览」已由 v2 改造
> （[editor-productivity-design.md](editor-productivity-design.md)）落地；「自定义片段」
> 「助手接入联网/学术检索」「Lezer 语法」在 [editor-experience-design.md](editor-experience-design.md)（v3）规划。

## 3. 技术选型

| 层 | 选型 | 许可证 | 说明 |
| --- | --- | --- | --- |
| 编辑器内核 | CodeMirror 6 + `@uiw/react-codemirror` | MIT | Overleaf 同款路线；模块化体积小；补全系统支持完全自定义来源与匹配函数 |
| LaTeX 高亮 | `@codemirror/legacy-modes` 的 `stex` 流式模式，经语言包抽象层引入 | MIT | 仅语法高亮，无智能缩进/折叠；抽象层（`lib/editor/language.js`）保证将来可整体替换 |
| 拼音匹配 | `pinyin-pro` | MIT | `match()` 一个函数同时覆盖中文子串 / 全拼 / 首字母查询 |

**许可红线（长期有效）**：社区另有基于 Overleaf Lezer 语法的 `codemirror-lang-latex`
（高亮 + 环境缩进 + 折叠更完整），但为 **AGPL-3.0**。本项目以 Web 服务形式提供多人使用，
引入 AGPL 前端依赖将产生开放源码义务，故采用 MIT 方案；若将来决定以 AGPL 兼容方式开源，
可通过语言包抽象层整体替换，不影响其他代码。

现有 `CodeEditorInput`（textarea 高亮叠加方案）继续服务于各功能模块的小段输入框，与新编辑器并存互不影响。新增前端依赖需同步 `THIRD_PARTY_NOTICES.md`。

## 4. 总体架构

```
┌─────────────────────────── /editor 页（全宽）────────────────────────────┐
│ 页头：返回 · 标题（共享 PageHeader）                                      │
│ 工具栏：保存 · 后端撤销/重做 · 编译 · 一键格式化 · 编辑器设置              │
├──────────┬──────────────────────────────────┬───────────────────────────┤
│ 文件树    │  多缓冲区 tab 条 + LaTeXEditor   │ 右栏 Tab 切换：            │
│ Project  │  (CodeMirror 6)：                │  · 编译结果 + PDF 预览     │
│ Explorer │  · \ 行内补全 · Ctrl+/ 命令面板  │  · 大纲 / 编译错误列表     │
│ （复用）  │  · 脏标记 / 冲突状态             │  · AI 助手面板（可折叠）   │
└──────────┴──────────────────────────────────┴───────────────────────────┘
```

- 入口：工作台"进入编辑器"按钮携带 activeFile 跳转 `/editor?file=...`；
- 权限：复用 `useCanWrite`；viewer 可进入但整体只读，AI 助手仅问答模式；
- 文件上限：超过约 2MB 拒绝打开并提示，防浏览器端卡顿；
- 软换行默认开启（LaTeX 源码长行常见），可在偏好中调整。

## 5. 后端 API

### 5.1 整文件读写（project_router 薄路由）

| 接口 | 语义 |
| --- | --- |
| `GET /api/project/file-content` | 参数 `file_path`、`workspace_id`；返回 `{content, md5}`。路径经 `resolve_project_path()` 白名单校验，逐组件符号链接检测 |
| `POST /api/project/save-file` | body `{file_path, workspace_id, content, base_md5}`；`base_md5` 不符返回 **409** 结构化错误；成功返回新 `{md5}` |

关键约束：

- 写路径走**统一记账**：多用户模式记入反向补丁快照，手动编辑可被 undo/redo 回退，与 AI 改动共享同一套历史；
- 服务端对内容做 `latex_json_guard` 的 `relaxed=True` 校验，仅拦截控制字符损坏、`[object Object]` 占位垃圾等**损坏特征**；不做括号/$ 配平检查（编辑中间态合法）。

### 5.2 编辑器助手端点（writing_chat 包内，前缀 `/api/writing-chat/editor`）

| 接口 | 语义 |
| --- | --- |
| `POST /api/writing-chat/editor/send` | SSE 流式对话；请求额外携带编辑器上下文 `{file_path, buffer, cursor_line, selection}`（§7.3） |
| `POST /api/writing-chat/editor/apply-suggestion` | 记录建议应用结果（消息级标记），供会话历史展示 |

AI 修改**不经后端落盘**：建议以结构化形式随 SSE 返回，由前端应用到 CM6 缓冲区，最终由用户保存时经 save-file 统一路径落盘。

### 5.3 配置

`FeaturesConfig` 的 `editor` 分区：`enabled`（编辑器总开关）与 `assistant_enabled`（AI 面板独立子开关，可在保留编辑器的同时单独关闭以控制 LLM 开销）。按配置同步规范四处联动（`AppConfig` → 设置分区 → `SettingsDialog` → `WelcomePage`）。

## 6. 命令快速输入子系统

### 6.1 命令数据库

纯前端 JSON 数据，按类目拆分存放于 `frontend/src/data/latexCommands/`，统一加载器导出（含 schema 校验与类目分组）。条目 schema：

```jsonc
{
  "name": "alpha",              // 英文名（\ 后输入的命令名）
  "insert": "\\alpha",          // 插入模板；$1/$2… 为占位符，$0 为退出位置
  "zh": "阿尔法",                // 中文名
  "category": "greek",          // 类目（面板分组展示）
  "pkg": null,                  // 必需宏包（null 表示无需）
  "grid": null                  // 参数化模板的网格选择器配置（表格/矩阵类）
}
```

### 6.2 模糊匹配（统一匹配器 `lib/editor/matcher.js`）

基于 `pinyin-pro` 四路匹配：中文子串（`阿尔法`）、全拼（`aerfa`）、首字母（`ae`）、英文前缀/子串（`alpha` / `al`）；拼音序列预计算缓存，行内补全与命令面板共用同一匹配器保证行为一致。

多音字修正：pinyin-pro 默认词典对数学专名会取错读音（「伽」默认 jiā，伽马/欧米伽应读 gā），以词级映射表（`PINYIN_WORD_FIXES`）经 `customPinyin()` 在索引构建前注册一次：只影响包含该词的条目、不污染同字其他读音，注册词可作用于包含它的更长条目。

### 6.3 双入口

1. **行内补全**（跟随光标）：`\` 触发命令补全；`\begin{` 触发环境名补全并自动配对 `\end{}`（CM6 `autocompletion` 自定义 source）；
2. **命令面板**（居中）：`Ctrl/Cmd+/` 或工具栏按钮唤起；搜索框 + 分类列表；选中后插入模板并进入占位符跳转模式（`Tab` 循环 `$1…$n`，`Esc`/完成退出）。

### 6.4 表格/矩阵网格选择器

面板中带 `grid` 配置的条目（tabular / matrix / pmatrix / bmatrix / vmatrix / cases 等）展开 m×n 拖选网格（Popover + CSS Grid），选定后按行列生成骨架再插入，占位符落在各单元格。

### 6.5 宏包缺失联动

前端内置极小「命令 → 必需宏包」映射表（如 matrix 系 / align → `amsmath`，三线表 → `booktabs`）。插入含 `pkg` 的条目时扫描当前缓冲区导言区（`preambleScan.js`），缺失则 toast 提示并提供"自动添加 `\usepackage{...}`"按钮（插入到已有 usepackage 声明之后或 `\documentclass` 之后）。

## 7. AI 助手侧栏

### 7.1 复用边界

| 处理 | 内容 |
| --- | --- |
| 直接复用 | `ConversationContext` 会话持久化、SSE 流式协议、`ToolRegistry` 只读项目工具、提示词集中管理、多用户 LLM 治理、会话保留期清理 |
| 新增 | 编辑器缓冲区协议端点（§5.2）、编辑器上下文注入、三种建议应用形态的前端交互、助手专用 system prompt |
| 不动 | Writing Chat 现有页面与段落双轮编辑协议（零回归） |

不复用段落编辑协议的原因：其编辑对象是**磁盘上的第 N 个段落**且应用即落盘；而 Copilot 式助手的本质是操作**编辑器缓冲区**（含未保存内容）。AI 若绕过缓冲区直改磁盘，未保存内容即与磁盘分叉，并与"所有落盘走 save-file 单一路径"的决策冲突。

### 7.2 建议应用形态（三种齐全）

1. **插入到光标处**：问答产出的片段一键插入（纯前端 CM6 transaction）；
2. **替换选区**：划词改写场景，替换当前选区；
3. **修改文件其余部分**：以 unified diff 预览卡片呈现，逐卡审阅、一键应用到缓冲区。

应用动作只改缓冲区并置脏标记，是否保存、何时保存始终由用户决定。

### 7.3 上下文注入策略

每条消息自动附带 `file_path` 与光标行号元信息。文件内容携带量自适应：

- 小文件（< 约 400 行）全文携带；
- 大文件携带"章节结构摘要 + 光标前后各约 50 行 + 当前选区"，并提供"把全文纳入本次提问"的手动开关。

### 7.4 会话模型

按**项目**隔离（复用 Writing Chat scope 哈希机制），切换编辑文件时会话延续，新消息自动声明当前讨论文件。viewer 可用问答，建议应用类操作不可用。

### 7.5 工具面

只开放只读项目工具；写入一律走缓冲区协议。联网搜索、学术检索等工具经 v3 规划接入（见 editor-experience-design.md）。

## 8. 与现有体系的集成点

| 集成点 | 设计 |
| --- | --- |
| 撤销体系 | save-file 统一记账（§5.1）；编辑器内未保存修改用 CM6 自身 history，已保存修改用后端 undo/redo，undo 后重拉文件内容刷新缓冲区 |
| 检查结果保鲜 | 逐段检查结果记录当时文件 MD5，检查页加载时比对，不一致渲染 `StaleCheckBanner` 提示重查而不销毁结果 |
| 编译联动 | 有脏改动时点击编译先自动保存（含乐观锁检查），失败（409）中断编译并进入冲突流程 |
| 错误消息 | 新增错误码全部录入 zh / zh-TW / en 三语 `errors` 命名空间，禁止硬编码兜底 |
| i18n | 命令中文名检索数据以简中为准，**展示层**按界面语言本地化：简中原样、繁体经 opencc-js 简→繁转换（懒加载 chunk）、英文界面隐藏中文名（`lib/editor/commandZh.js`） |

## 9. 前端结构

完整组件与模块清单（含 v2 增量）见 [docs/modules/editor.md](modules/editor.md)。v1 核心落点：

```
frontend/src/
├── pages/EditorPage.jsx              # /editor 三栏布局与全局快捷键
├── components/editor/                # LaTeXEditor / EditorToolbar / ConflictDialog /
│                                     # CommandPalette / GridSizePicker / AssistantPanel / ...
├── lib/editor/                       # 纯模块：language / matcher / latexCompletion /
│                                     # snippetInsert / preambleScan / preferences ...
└── data/latexCommands/               # 命令数据库 JSON（按类目拆分）
```

Zustand 切片：`editorPreferences`（persist 持久化偏好）与 `editorSession`（缓冲区状态）。个人偏好不进 `config.json`——那是部署级配置的地盘。

## 10. 键位表

| 快捷键 | 行为 |
| --- | --- |
| `Ctrl/Cmd+S` | 保存（乐观锁校验） |
| `Ctrl/Cmd+/` | 命令面板 |
| `\`（行内） | 命令补全 |
| `\begin{`（行内） | 环境名补全 + 自动配对 |
| `Tab` | 占位符间循环跳转 |
| `Ctrl/Cmd+Z` / `Shift+Ctrl/Cmd+Z` | 缓冲区内撤销/重做（CM6 history，未保存层） |

v2 追加快捷键（包裹 `Ctrl/Cmd+B/I/U/K`、包裹浮层 `Ctrl+L`、一键格式化 `Ctrl/Cmd+Shift+F`）见 [editor-productivity-design.md](editor-productivity-design.md) 与 modules/editor.md。

## 11. 偏好导入/导出

```jsonc
{
  "kind": "latexcellence-editor-preferences",
  "version": 1,
  "prefs": { "autoSave": false, "softWrap": true, "fontSize": 14 }
}
```

- 导出：pretty-print 下载 `.json`；导入：文件或粘贴皆可，按 `version` + 字段白名单严格校验，非法字段静默丢弃，合法字段**整体覆盖**当前切片（无合并选项）；
- 入口：编辑器设置弹层（不放全局设置对话框，避免与部署配置混淆）。

## 12. 决策记录

> 本表是代码注释中「决策 QN」引用的锚点。部分 v1 决策已被 v2 改造迭代
> （Q5 单文件 → 多缓冲区、Q10 KaTeX 延后 → 已落地、Q15 引用补全延后 → 已落地），
> 现行行为以 modules/editor.md 为准；本表保留作为设计初衷的对照基线。

| # | 决策 | 结论 |
| --- | --- | --- |
| Q1 | 编辑器内核 | CodeMirror 6 + @uiw/react-codemirror |
| Q2 | LaTeX 语言包 | MIT legacy stex 起步，抽象层保留 AGPL 包替换路径 |
| Q3 | 可编辑范围 | 白名单 .tex/.bib/.sty/.cls |
| Q4 | 入口布局 | 新增 /editor 路由页，工作台加跳转入口 |
| Q5 | 标签模型 | v1 单文件，数据结构预留多 tab 升级（v2 已落地多缓冲区） |
| Q6 | 保存与冲突 | 手动为主 + 自动保存偏好默认关；409 重载/剪贴板逃生 |
| Q7 | 并发模型 | 乐观锁 only，不做实时协同 |
| Q8 | 补全触发 | \ 行内补全 + 快捷键命令面板双入口 |
| Q9 | 模板插入 | 表格/矩阵 m×n 网格选择器 |
| Q10 | KaTeX 预览 | v1 不做（v2 已落地悬停预览） |
| Q11 | 撤销体系 | save-file 接入反向补丁统一记账 |
| Q12 | 命令数据库 | 首批精选百余条，自定义片段延后（v3 规划） |
| Q13 | 结果保鲜 | 文件版本戳比对 MD5 + 过期横幅 |
| Q14 | 宏包联动 | 内置最小映射，缺失 toast + 一键添加声明 |
| Q15 | 补全覆盖面 | 命令 + \begin{ 环境（\ref/\cite 已由 v2 落地） |
| Q16 | 偏好存放 | 前端 persist 本地存储 + 导出/导入跨浏览器迁移 |
| Q17 | 功能开关 | 新增 editor 开关，走四处配置同步规范 |
| Q18 | 编译联动 | 脏改动编译前自动保存，失败中断 |
| Q19 | 导出边界 | 仅编辑器偏好切片，version 字段预留扩展 |
| Q20 | 导入交互 | 设置弹层入口；文件/粘贴双通道；严格校验 |
| Q21 | 导入语义 | 整体覆盖，无合并选项 |
| Q22 | 助手路线 | 复用 ConversationContext/ToolRegistry/SSE + 新增编辑器缓冲区协议 |
| Q23 | 建议形态 | 插入光标 / 替换选区 / diff 卡片三种齐全 |
| Q24 | 上下文注入 | 自适应：小文件全文，大文件摘要+光标邻域+选区 |
| Q25 | 会话模型 | 按项目隔离，延续 Writing Chat scope 机制 |
| Q26 | 工具与开关 | 只读三件套工具；assistant_enabled 独立子开关 |
