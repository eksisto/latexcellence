# 网页编辑器功能设计方案（editor）

> **状态：已实施。** 本文记录「网页编辑器 + LaTeX 命令快速输入 + 编辑器侧
> AI 助手」的完整设计共识，作为实施依据与后续变更对照基线；实施细节与最终
> 形态见 [docs/modules/editor.md](modules/editor.md)。如行为与本文冲突，
> 先修订本文再动代码。

## 1. 背景与目标

LaTeXcellence 当前的工作台以**段落只读视图**展示文件内容，AI 修改经逐段协议落盘，用户无法像 VS Code 那样手动编辑文件。本方案在同一前端内新增：

1. 全宽编辑器页，支持项目内文本文件的手动编辑、乐观锁保存与后端撤销回退；
2. 核心特色——**LaTeX 命令快速输入**：命令数据库支持中文、拼音全拼/首字母、英文三路模糊查找（如 `阿尔法` / `aerfa` / `alpha` 均可命中 `\alpha`），提供 `\` 行内补全与快捷键命令面板双入口，表格/矩阵类模板支持 m 行 n 列网格选择；
3. 编辑器侧 AI 助手面板（Copilot 式）：对话内核复用 Writing Chat 基础设施，修改建议应用到编辑器缓冲区而不直接落盘。

## 2. v1 范围与非目标

### 范围

- 新增 `/editor` 独立路由页（全宽三栏布局），工作台加"进入编辑器"入口；
- 可编辑文件白名单：`.tex` / `.bib` / `.sty` / `.cls`（白名单做成常量便于扩展）；
- 单文件编辑模型：文件树点击即打开，无多标签页；
- 手动保存为主（`Ctrl/Cmd+S` 与工具栏按钮），"自动保存"为个人偏好且默认关闭；
- MD5 乐观锁冲突处理：弹窗提示 + 一键放弃本地重载 + 本地内容复制到剪贴板逃生；
- 所有落盘走统一的 save-file 路径并接入多用户反向补丁撤销记账；
- 命令快速输入子系统（见 §6）；
- AI 助手侧栏（见 §7）；
- 桌面优先，不做移动端深度适配。

### 非目标（留作二期候选）

实时协同编辑（Yjs）、KaTeX 公式预览、`\ref{}` / `\cite{}` 引用补全、多标签页、用户自定义片段、冲突 diff 合并视图、助手接入联网搜索/学术检索工具。

## 3. 技术选型

| 层 | 选型 | 许可证 | 说明 |
| --- | --- | --- | --- |
| 编辑器内核 | CodeMirror 6 + `@uiw/react-codemirror` | MIT | Overleaf 同款路线；模块化体积小；补全系统支持完全自定义来源与匹配函数 |
| LaTeX 高亮 | `@codemirror/legacy-modes` 的 `stex` 流式模式，经语言包抽象层引入 | MIT | 仅语法高亮，无智能缩进/折叠；抽象层保证将来可整体替换 |
| 拼音匹配 | `pinyin-pro` | MIT | `match()` 一个函数同时覆盖中文子串 / 全拼 / 首字母查询 |

**许可证决策**：社区另有基于 Overleaf Lezer 语法的 `codemirror-lang-latex`（高亮 + 环境缩进 + 折叠更完整），但为 **AGPL-3.0**。本项目以 Web 服务形式提供多人使用，引入 AGPL 前端依赖将产生开放源码义务，故 v1 采用 MIT 方案起步；若将来决定以 AGPL 兼容方式开源，可通过语言包抽象层整体替换，不影响其他代码。

现有 `CodeEditorInput`（textarea 高亮叠加方案）继续服务于各功能模块的小段输入框，与新编辑器并存互不影响。

实施时新增前端依赖需同步更新 `THIRD_PARTY_NOTICES.md`（名称、版本、许可证、用途）。

## 4. 总体架构

```
┌─────────────────────────── /editor 页（全宽）────────────────────────────┐
│ 页头：返回 · 标题（与其他功能页一致的共享 PageHeader）                     │
│ 工具栏：保存 · 后端撤销/重做 · 编译 · 编辑器设置                          │
├──────────┬──────────────────────────────────┬───────────────────────────┤
│ 文件树    │  LaTeXEditor (CodeMirror 6)      │ 右栏 Tab 切换：            │
│ Project  │  · \ 行内补全                    │  · 编译结果 + PDF 预览     │
│ Explorer │  · Ctrl+/ 命令面板               │    （复用 PDFPreview）     │
│ （复用）  │  · 脏标记 / 冲突状态             │  · AI 助手面板             │
│          │                                  │   （可折叠，Copilot 式）   │
└──────────┴──────────────────────────────────┴───────────────────────────┘
```

- 入口：WorkspacePage 增加"进入编辑器"按钮，携带当前 activeFile 跳转 `/editor?file=...`；不改动工作台既有逻辑。
- 页头：与其他功能页一致复用共享 `PageHeader`（返回 + §分组 kicker + 标题）；返回入口由页头提供，工具栏不重复放置返回按钮。
- 权限：复用 `useCanWrite`；viewer 角色可进入但整体只读（禁用保存与插入类操作），AI 助手仅问答模式。
- 文件上限：超过约 2MB 的文件拒绝在编辑器打开并提示，防止浏览器端卡顿。
- 软换行默认开启（LaTeX 源码长行常见），可在偏好中调整。

## 5. 后端新增 API

### 5.1 整文件读写（project_router 新增薄路由）

| 接口 | 语义 |
| --- | --- |
| `GET /api/project/file-content` | 参数 `file_path`、`workspace_id`；返回 `{content, md5}`。路径经 `resolve_project_path()` 白名单校验（扩展名白名单同前端），逐组件符号链接检测 |
| `POST /api/project/save-file` | body `{file_path, workspace_id, content, base_md5}`；`base_md5` 与当前文件不符返回 **409** 结构化错误（前端 `translateError` 接管）；成功返回新 `{md5}` |

关键约束：

- 写路径走**统一记账**：多用户模式记入 `workspace_edit_ops` 反向补丁快照，手动编辑因此可被现有 undo/redo 回退，与 AI 改动共享同一套历史（单用户沿用现有撤销栈）；写盘后刷新项目用量记账，超限回滚语义与现有一致。
- 服务端对内容做 `latex_json_guard` 的 `relaxed=True` 校验，仅拦截控制字符损坏、`[object Object]` 占位垃圾等**损坏特征**；不做括号/$ 配平检查（编辑中间态合法）。
- 路由层零业务逻辑；单用户/多用户双模式均开放。
- 同步新增 pytest 用例（纯文件操作，Mock 无 LLM 参与）。

### 5.2 编辑器助手端点（writing_chat 包内新增，前缀 `/api/writing-chat/editor`）

复用现有 SSE 协议与会话持久化，新增：

| 接口 | 语义 |
| --- | --- |
| `POST /api/writing-chat/editor/send` | SSE 流式对话；请求额外携带编辑器上下文 `{file_path, buffer, cursor_line, selection}`（见 §7.3 注入策略） |
| `POST /api/writing-chat/editor/apply-suggestion` | 记录建议应用结果（消息级标记），供会话历史展示 |

AI 修改**不经后端落盘**：建议以结构化形式随 SSE 返回，由前端应用到 CM6 缓冲区，最终由用户保存时经 save-file 统一路径落盘。

### 5.3 配置

`FeaturesConfig` 新增 `editor` 分区（同步更新 `config.example.json`）：

```json
"editor": {
  "enabled": true,
  "assistant_enabled": true
}
```

按配置同步规范四处联动：`AppConfig`/`FeaturesConfig` → 设置对话框分区组件与 `handleSave` 的 `setEnabledFeatures` → 首页点线目录 `featureGroups`。`assistant_enabled` 为独立子开关，管理员可在保留编辑器的同时单独关闭 AI 面板以控制 LLM 开销。

## 6. 命令快速输入子系统

### 6.1 命令数据库

首批收录百余条精选条目（希腊字母全套、常用运算/箭头/关系符号、章节与图表环境、数学环境、表格/矩阵/行列式参数化模板等），以后按真实使用反馈增补。纯前端 JSON 数据，按类目拆分文件存放于 `frontend/src/data/latexCommands/`，统一加载器导出。

条目 schema：

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

### 6.2 三路模糊匹配

统一匹配器基于 `pinyin-pro`：

- 中文子串：query 含于 `zh`（`阿尔法` → 命中）；
- 全拼：对 `zh` 预计算全拼序列（`aerfa`）；
- 首字母：预计算首字母序列（`ae`）；
- 英文前缀/子串：对 `name` 匹配（`alpha` / `al`）。

四路得分归一排序，行内补全与命令面板共用同一匹配器，保证两处行为一致。

### 6.3 双入口

1. **行内补全**（跟随光标）：`\` 触发命令补全；`\begin{` 触发环境名补全并自动配对 `\end{}`。CM6 `autocompletion` 自定义 source 实现。
2. **命令面板**（居中）：`Ctrl/Cmd+/` 或工具栏按钮唤起；搜索框 + 分类列表；选中后插入模板并进入占位符跳转模式（`Tab` 循环 `$1…$n`，`Esc`/完成退出）。

### 6.4 表格/矩阵网格选择器

面板中带 `grid` 配置的条目（tabular / matrix / pmatrix / bmatrix / vmatrix / cases 等）展开 m×n 拖选网格（Popover + CSS Grid，类似 Word 插入表格），选定后按行列生成骨架再插入，占位符落在各单元格。

### 6.5 宏包缺失联动

前端内置极小「命令 → 必需宏包」映射表（初始：`matrix/pmatrix/bmatrix/vmatrix/cases/align` 等 → `amsmath`；三线表模板 → `booktabs`）。插入含 `pkg` 的条目时扫描当前缓冲区导言区，缺失则 toast 提示并提供"自动添加 `\usepackage{...}` 到导言区"按钮（插入到已有 usepackage 声明之后或 `\documentclass` 之后）。映射表后续可改由后端宏包规则表接口供给，前端先硬编码最小集。

## 7. AI 助手侧栏

### 7.1 复用边界

| 处理 | 内容 |
| --- | --- |
| 直接复用 | `ConversationContext` 会话持久化（磁盘存储、token 计数、上下文压缩）、SSE 流式协议、`ToolRegistry` 只读项目工具（list_files / read_file_content / search_across_files）、提示词集中管理、多用户 LLM 治理（同一 `llm_client` 自动限流）、会话保留期清理 |
| 新增 | 编辑器缓冲区协议端点（§5.2）、编辑器上下文注入、三种建议应用形态的前端交互、助手专用 system prompt |
| 不动 | Writing Chat 现有页面与段落双轮编辑协议（零回归） |

不复用段落编辑协议的原因：其编辑对象是**磁盘上的第 N 个段落**且应用即落盘；而 Copilot 式助手的本质是操作**编辑器缓冲区**（含未保存内容）。AI 若绕过缓冲区直改磁盘，未保存内容即与磁盘分叉，并与"所有落盘走 save-file 单一路径"的决策冲突。

### 7.2 建议应用形态（三种齐全）

1. **插入到光标处**：问答产出的片段一键插入（纯前端 CM6 transaction）；
2. **替换选区**：划词改写场景，替换当前选区；
3. **修改文件其余部分**：以 unified diff 预览卡片呈现，逐卡审阅、一键应用到缓冲区（交互沿用熟悉的 EditOpCard 模式）。

应用动作只改缓冲区并置脏标记，是否保存、何时保存始终由用户决定。

### 7.3 上下文注入策略

每条消息自动附带 `file_path` 与光标行号元信息。文件内容的携带量自适应：

- 小文件（阈值以内，如 < 400 行）全文携带；
- 大文件携带"章节结构摘要 + 光标前后各约 50 行 + 当前选区"，并提供"把全文纳入本次提问"的手动开关。

### 7.4 会话模型

按**项目**隔离（复用现有 scope 哈希机制），切换编辑文件时会话延续，新消息自动声明当前讨论文件；跨文件问题（如"统一第三章与绪论的口径"）自然承接。viewer 角色可用问答，但建议应用类操作不可用。

### 7.5 工具面

v1 仅开放三个只读项目工具；写入一律走缓冲区协议。联网搜索、学术检索等工具暂不接入（其确认流面向 Writing Chat 场景，留二期评估）。

## 8. 与现有体系的集成点

| 集成点 | 设计 |
| --- | --- |
| 撤销体系 | save-file 统一记账（§5.1）；编辑器内未保存修改用 CM6 自身 history，已保存修改用后端 undo/redo，工具栏按钮复用现有语义，undo 后重拉文件内容刷新缓冲区 |
| 检查结果保鲜 | 各逐段检查结果记录当时的文件 MD5；检查页加载时比对当前文件 MD5（`/api/project/file-md5` 已有），不一致渲染通用 `StaleCheckBanner`："文件已在编辑器中修改，建议重新检查"。不销毁既有结果 |
| 编译联动 | 有脏改动时点击编译先自动保存（含乐观锁检查），失败（如 409）中断编译并进入冲突流程 |
| 错误消息 | 新增错误码全部录入 zh / zh-TW / en 三语 `errors` 命名空间，禁止硬编码兜底 |
| i18n | 新增界面文案三语全覆盖；命令中文名检索数据以简中为准（拼音匹配天然兼容），**展示层**按界面语言本地化：简中原样、繁体经 opencc-js 简→繁转换（懒加载 chunk）、英文界面隐藏中文名（`lib/editor/commandZh.js`） |

## 9. 前端结构（计划形态）

```
frontend/src/
├── pages/EditorPage.jsx              # /editor 三栏布局与全局快捷键
├── components/editor/
│   ├── LaTeXEditor.jsx               # CM6 封装（受控 value/onChange）
│   ├── EditorToolbar.jsx             # 保存 · 撤销/重做 · 编译 · 设置
│   ├── EditorSettingsPopover.jsx     # 偏好开关 + 偏好导入/导出
│   ├── ConflictDialog.jsx            # 409 弹窗（重载 / 本地内容逃生）
│   ├── CommandPalette.jsx            # 命令面板（搜索 + 分类 + 选择器挂点）
│   ├── GridSizePicker.jsx            # m×n 网格选择器
│   ├── AssistantPanel.jsx            # AI 助手侧栏（SSE 对话 + 建议卡）
│   └── StaleCheckBanner.jsx          # 检查结果过期横幅（供各检查页复用）
├── lib/editor/
│   ├── language.js                   # 语言包抽象层（stex StreamLanguage）
│   ├── latexCompletion.js            # \ 与 \begin{ 补全 source
│   ├── snippetInsert.js              # 模板插入 + 占位符 Tab 循环
│   ├── matcher.js                    # pinyin-pro 三路模糊匹配
│   ├── preambleScan.js               # 导言区扫描 + 宏包缺失检测
│   └── preferences.js                # 偏好 slice + 导出导入校验
└── data/latexCommands/               # 命令数据库 JSON（按类目拆分）
```

Zustand 切片：`editorPreferences`（persist 持久化：自动保存、软换行、字号等）与 `editorSession`（当前文件、缓冲区内容、baseMd5、脏标记）。个人偏好不进 `config.json`——那是部署级配置的地盘。

## 10. 键位表

| 快捷键 | 行为 |
| --- | --- |
| `Ctrl/Cmd+S` | 保存（乐观锁校验） |
| `Ctrl/Cmd+/` | 命令面板 |
| `\`（行内） | 命令补全 |
| `\begin{`（行内） | 环境名补全 + 自动配对 |
| `Tab` | 占位符间循环跳转 |
| `Ctrl/Cmd+Z` / `Shift+Ctrl/Cmd+Z` | 缓冲区内撤销/重做（CM6 history，未保存层） |

## 11. 偏好导入/导出

```jsonc
{
  "kind": "latexcellence-editor-preferences",
  "version": 1,
  "prefs": {
    "autoSave": false,
    "softWrap": true,
    "fontSize": 14
  }
}
```

- 导出：pretty-print 下载 `.json`；
- 导入：选文件或粘贴 JSON 文本皆可；按 `version` + 字段白名单严格校验，非法字段静默丢弃，合法字段立即生效并**整体覆盖**当前切片（不提供合并选项），toast 反馈结果；
- 入口：编辑器工具栏"编辑器设置"弹层（不放全局设置对话框，避免与部署配置混淆）。

## 12. 实施顺序

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| 一 | 后端 file-content / save-file + 统一记账 + pytest 用例 | 双模式下打开/保存/409/undo 回退通过测试 |
| 二 | 前端骨架：依赖接入、/editor 路由、CM6 打开-编辑-保存-冲突全流程 | 基础编辑闭环可用 |
| 三 | 命令子系统：数据库 → matcher → 行内补全 → 命令面板 → 网格选择器 → 宏包联动 | 中/拼/英三路查找与模板插入可用 |
| 四 | 集成打磨：AI 助手侧栏、功能开关四处同步、StaleCheckBanner、编译联动、偏好导入导出、三语文案、文档更新（DEVELOPMENT.md / PROJECT_SUMMARY.md / docs/modules/、README、THIRD_PARTY_NOTICES） | 功能完整 + 文档同步 |

## 13. 决策记录

拷问式评审（grilling）共确定以下决策，作为方案变更时的对照基线：

| # | 决策 | 结论 |
| --- | --- | --- |
| Q1 | 编辑器内核 | CodeMirror 6 + @uiw/react-codemirror |
| Q2 | LaTeX 语言包 | MIT legacy stex 起步，抽象层保留 AGPL 包替换路径 |
| Q3 | 可编辑范围 | 白名单 .tex/.bib/.sty/.cls |
| Q4 | 入口布局 | 新增 /editor 路由页，工作台加跳转入口 |
| Q5 | 标签模型 | v1 单文件，数据结构预留多 tab 升级 |
| Q6 | 保存与冲突 | 手动为主 + 自动保存偏好默认关；409 重载/剪贴板逃生 |
| Q7 | 并发模型 | 乐观锁 only，不做实时协同 |
| Q8 | 补全触发 | \ 行内补全 + 快捷键命令面板双入口 |
| Q9 | 模板插入 | 表格/矩阵 m×n 网格选择器 |
| Q10 | KaTeX 预览 | v1 不做，列二期 |
| Q11 | 撤销体系 | save-file 接入反向补丁统一记账 |
| Q12 | 命令数据库 | 首批精选百余条，自定义片段延后 |
| Q13 | 结果保鲜 | 文件版本戳比对 MD5 + 过期横幅 |
| Q14 | 宏包联动 | 内置最小映射，缺失 toast + 一键添加声明 |
| Q15 | 补全覆盖面 | 命令 + \begin{ 环境；\ref/\cite 留二期 |
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
