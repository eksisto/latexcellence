# 编辑器写作体验收口设计方案（editor v3）

> **状态：分阶段实施中（阶段一 SyncTeX 正反向同步、阶段二用户自定义片段库、阶段三实时语法诊断、阶段四跨文件大纲与公式固定预览卡、阶段五a 助手接入联网与学术检索已实施，其余阶段未实施）。** 本文是编辑器生产力改造（[editor-productivity-design.md](editor-productivity-design.md)，
> 八阶段已全部实施）完成后，对其"非目标（记入 roadmap）"清单的收口方案，作为分阶段实施的
> 依据与变更对照基线。阶段五（助手联网）的确认流时序已过专项设计评审（见 Q12），按评审
> 结论拆为"5a 工具接入（含 DDG 挑战）"与"5b 确认流"两个子阶段，5a 先行（工具直通执行，
> 与 Writing Chat 主对话注册表行为一致；DDG 挑战是唯一的人机交互）。已实施形态见
> [modules/editor.md](modules/editor.md)。
> 如行为与本文冲突，先修订本文再动代码。

## 1. 背景与动机

编辑器 v2 完成多缓冲区基座与写作反馈闭环后，`/editor` 页已可日常写作，但与
Overleaf 级体验相比仍有明确落差，全部对应 v2 计划记入 roadmap 的非目标：

1. **PDF 与源码互不认识**：右栏预览是浏览器原生 iframe（`PDFPreview.jsx`），
   编译报错只能"错误列表 → 源码"单向跳转；写作者定位"这段在 PDF 第几页"、
   "PDF 里这个公式在源码哪一行"仍靠肉眼。
2. **片段不可自定义**：命令数据库（`data/latexCommands`）是构建期冻结数据，
   个人高频模板（如实验室宏包专属环境）无处安放。
3. **写错没有即时反馈**：环境不闭合、花括号不配平要等编译失败才发现。
4. **大纲只看当前文件**：跨文件论文结构（主文件 + 章节）无法总览。
5. **助手只能看项目**：编辑器助手工具白名单
   `_EDITOR_TOOL_NAMES`（`editor_chat.py`）只有项目只读浏览工具，联网搜索与
   学术检索仍要去 Writing Chat 页面两头上跑。

本方案将上述各项收口，并纳入经选型评审后定案的**自研 MIT Lezer 子集语法**
升级（§4.6，选型证据见附录）。

## 2. 范围与非目标

### 范围

- SyncTeX 正反向同步（PDF ↔ 源码互跳；配套编译链路 `-synctex=1`、
  synctex 查询端点、pdf.js 预览替换 iframe）；
- 用户自定义片段库（偏好持久化扩展 + 管理界面 + 补全/命令面板/插入入口联动）；
- 实时语法诊断（自研纯模块诊断器 + CM6 lint；chktex 镜像集成列为可选增强）；
- 跨文件大纲 + 当前公式固定预览卡；
- 编辑器 AI 助手接入联网搜索与学术检索工具（工具白名单扩展 + 确认流适配）；
- 自研 MIT Lezer LaTeX 子集语法（`language.js` 唯一入口切换：精确高亮
  先行，折叠与环境缩进随后）。

### 非目标（本轮明确排除）

- 实时协同编辑、冲突 diff 合并视图（沿袭 v1/v2 非目标）；
- 移动端深度适配；
- PDF 文本层（选择/复制/搜索）与 PDF 内编辑、批注；
- 编译错误行内波浪线（沿袭 v2 Q10 结论，诊断面板与错误列表各司其职）；
- 跨文件大纲的 `\input`/`\include` 树状排序（首轮按文件平铺分组）；
- forward sync 的行号映射补偿（脏缓冲区行号偏差容忍，见 Q4）。

## 3. 关键决策记录

| # | 决策点 | 结论 |
| --- | --- | --- |
| Q1 | SyncTeX 解析位置 | 后端 CLI（`synctex view` / `synctex edit`，TeX Live 自带）经 SandboxRunner 执行；不用前端 JS 解析 `.synctex.gz`（无维护良好的纯 JS 实现，Overleaf 亦走服务端 CLI） |
| Q2 | PDF 渲染方案 | iframe 原生预览整体替换为 pdf.js（`pdfjs-dist`，Apache-2.0）动态分块加载——正向定位高亮与反向点击取坐标都必须渲染层可控，iframe 无法程序化滚动/取点击位置；替代后需补最小缩放/页码控件（原为浏览器自带） |
| Q3 | `-synctex=1` 开关 | `CompileRequest` 新增可选 `synctex` 字段（默认 false，编译页/git_diff 等既有调用方零影响）；编辑器恒传 true；`pdfa=true` 时后端忽略 synctex（PDF/A 在临时目录编译后回拷，`.synctex.gz` 不同步回项目目录）。产物清理白名单已预置 `.synctex.gz` 条目 |
| Q4 | forward 行号偏差 | 按缓冲区行号直发 `.synctex.gz` 查询（该文件来自"最近一次编译时点"的磁盘版本）；编译联动已保证编译前全量脏保存，偏差仅出现在编译后的连续编辑内，容忍不补偿 |
| Q5 | reverse 触发方式 | PDF 内**双击**跳转源码（单击保留给滚动/选择习惯，避免误触）；坐标经 pdf.js `viewport.convertToPdfPoint` 后按 synctex 左上原点约定做 `y = pageHeight − y` 换算 |
| Q6 | forward 触发方式 | 光标位置变化自动跟随（复用 stats hub 光标通道，防抖约 800ms），预览可见时才发查询；不做偏好开关（行为无侵入） |
| Q7 | 自定义片段存储 | 并入编辑器偏好 persist（`"latexcellence-editor"`），version 1 → 2 增 `customSnippets` 数组；导入导出走既有偏好双通道（`parsePreferencesImport` 兼容 v1 无片段载荷）；上限 100 条、单条 insert ≤ 4000 字符 |
| Q8 | 片段数据通道 | `data/latexCommands` JSON 保持构建期冻结；新增消费侧纯模块（如 `customCommands.js`）把自定义条目合并进补全 source、命令面板匹配与插入入口；条目 schema 沿用 `{ name, insert, zh, category: "custom" }`，机制层（`expandEntryInsert` / `insertSnippet` / `$n` 占位符）零改动 |
| Q9 | 诊断实现路线 | 自研纯模块 `diagnostics.js`（node --test 可测）+ `@codemirror/lint`（新增显式依赖，MIT）；首轮只报未闭合环境（栈匹配，verbatim 类环境内豁免）与花括号不配平（复用 `outlineScan.stripComment` 同源规则豁免注释/`\verb`）；上限 200 条；chktex 镜像集成为可选增强，首轮不做（镜像体积与噪声治理成本，触发条件：纯模块误报率实测不达标） |
| Q10 | 跨文件大纲数据源 | 后端新增 `GET /api/project/outline` 全项目 `.tex` 分节扫描（沿袭 `latex-symbols` 先例：只读扫描 + `asyncio.to_thread()` + 项目级缓存保存失效）；UI 在左栏大纲面板增加"本项目"视图，按文件分组、激活文件置顶展开，点击联动开 tab（复用错误跳转的 `pendingErrorJumpRef` 换档机制） |
| Q11 | KaTeX 固定预览卡形态 | 不新增右栏 tab（已被编译/助手占用）；编辑区底部、状态栏上方悬浮卡片，防抖约 400ms 跟随光标所在公式渲染，空态隐藏；识别复用 `formulaHoverScan.findFormulaAt`，KaTeX 复用 `formulaHover.loadKatex` 单例 |
| Q12 | 助手工具范围与确认流 | `_EDITOR_TOOL_NAMES` 扩入联网搜索与学术检索工具集（以 Writing Chat 主对话注册表为准）；编辑器 SSE 流新增 `tool_pending` 事件，确认/取消复用 Writing Chat 的 pending_interactions 基础设施与端点；DDG 挑战交互复用 `hooks/writing-chat/captcha` 通道（本阶段改动面最大，动工前需单独过一轮确认流时序的设计评审——既定门槛） |
| Q13 | 阶段顺序 | 一 SyncTeX → 二 自定义片段 → 三 实时诊断 → 四 跨文件大纲 + 预览卡 → 五 助手联网（一至四相互无依赖，三与四可并行）；六 Lezer 语法与一至五无依赖，可在任意时点插入（内部再分 6a 高亮 / 6b 折叠缩进两批交付） |
| Q14 | Lezer 自研路线 | 自研 MIT 子集语法（维护者已确认）：grammar 源置于 `frontend/src/lib/editor/latexGrammar/`，经 `lezer-generator`（MIT，devDependency）在 dev/build 前编译，生成物不入库；`.tex`/`.sty`/`.cls` 走 Lezer 语法、`.bib` 暂保留 StreamLanguage（自研最小 bib 语法列可选尾项）；`language.js` 契约改为 `latexLanguageExtension(suffix)`；子集范围 P0 = 注释/命令及参数/行内行间数学/环境/分组/上下标/verbatim 原样，P1 = 环境与分节折叠 + 环境体缩进；不追求 catcode 级完全正确，依赖 Lezer 错误恢复保证极端输入不崩 |

## 4. 总体设计

### 4.1 阶段一：SyncTeX 正反向同步

**后端**（`backend/modules/compile/` 与 `backend/utils/tex_compile.py`）：

- `run_tex_compile()` 的 latexmk 基础参数在 `synctex` 生效时追加
  `-synctex=1`（挂 `extra_args` 同级，不污染其他调用方）；
- `CompileRequest` / `CompileResult` 新增 `synctex` 字段（请求可选默认
  false；响应回传实际是否生效，pdfa 组合时为 false）；
- 新端点（挂 compile 路由，`main_file` 校验复用既有依赖）：

| 接口 | 语义 |
| --- | --- |
| `POST /api/compile/synctex/forward` | `{workspace_id, main_file, file_path, line, col}` → 执行 `synctex view -i "line:col:file" -o pdf`，返回 `{records: [{page, x, y, w, h}]}`（72dpi 点单位） |
| `POST /api/compile/synctex/backward` | `{workspace_id, main_file, page, x, y}` → 执行 `synctex edit -o "page:x:y:pdf"`，返回 `{file_path, line, col}`（相对路径）或 `not_found` |

- 两个端点均为零写盘只读查询：`ExecSpec(image_kind="tex", cwd=项目根)`，
  `file_path` 经 `resolve_project_path()` 校验，synctex 输出中的绝对路径经
  `sanitize_output_paths()` 脱敏后仅返回相对路径（多用户脱敏）；
- `docker/tex/Dockerfile` 构建期 `command -v` 自检清单补 `synctex`
  （TeX Live 自带，显式自检防镜像瘦身误删）。

**前端**：

- 新组件 `components/editor/PdfJsPreview.jsx` 替换 `PDFPreview.jsx` 的
  iframe 实现（对外 props 兼容 `{url}`，新增 `forwardTarget` 订阅与
  `onBackwardSync` 回调；`FullscreenPDFPreview` 自动受益）：
  - `pdfjs-dist` 动态 import 独立分块（沿用 katex / pinyin-pro 懒加载先例，
    worker 资产经 Vite `?url` 引入），首屏零加载；
  - 最小工具条：缩放（含 Ctrl+滚轮）、适宽、页码；无文本层；
  - 正向：收到 forward 记录后滚动到对应页并绘制高亮矩形浮层（点单位 →
    CSS 像素换算），下一次替换；
  - 反向：双击 → `convertToPdfPoint` + 原点换算 → `onBackwardSync`；
- `EditorPage` 接线：forward 由 stats hub 光标通道防抖驱动（预览可见 &&
  有编译产物时）；backward 复用 `handleJumpToError` 的开 tab + 行定位
  通路（文件未打开时经 `pendingErrorJumpRef` 延迟到换档后）；
- 编译后 `pdfUrl` 更新时预览重渲染、缩放态保留。

### 4.2 阶段二：用户自定义片段库

- **数据与持久化**：`EDITOR_PREFERENCES_DEFAULTS` 增 `customSnippets: []`；
  `sanitizePreferences()` 增条目级清洗（schema 校验、去重名、超限截断）；
  persist `version` 1 → 2（`migrate` 补默认空数组）；`serializePreferences`
  载荷升 version 2，`parsePreferencesImport` 同时接受 v1（无片段）与 v2。
- **消费侧合并**：新纯模块 `lib/editor/customCommands.js` 导出
  `withCustomCommands(base, custom)`，补全 source、命令面板/包裹浮层数据、
  插入入口统一改经该合并层；`category: "custom"` 在命令面板归入独立分组
  （三语条目）。网格模板标记 `%COLS%`/`%CELLS%` 对自定义条目天然生效。
- **管理界面**：`EditorSettingsDialog.jsx` 新增"自定义片段"分区（列表 +
  增删改表单：名称 / 插入内容 / 中文名；插入内容实时预览占位符解析）；
  跟随既有偏好导入导出通道分发。
- **测试**：清洗/合并/迁移纯函数 node --test；设置分区手测验收。

### 4.3 阶段三：实时语法诊断

- **纯模块** `lib/editor/diagnostics.js`：
  `computeDiagnostics(docText) -> [{from, to, severity, message, rule}]`；
  规则两条——
  1. `unmatched-env`：`\begin`/`\end` 栈匹配（复用 `formulaHoverScan` 的
     环境名扫描思路与 `outlineScan.stripComment` 的注释豁免；verbatim 类
     环境整体豁免），severity error；
  2. `unbalanced-braces`：全文档花括号计数不配平（豁免注释、`\verb`、
     转义 `\{`/`\}`、字面量 URL），定位到失衡行，severity warning；
  诊断上限 200 条，超出截断并在首条标注。
- **CM6 接线**：`@codemirror/lint` 新增为显式依赖并登记
  `THIRD_PARTY_NOTICES.md`（MIT）；`linter(..., {delay: 500})` 随
  `liveDiagnostics` 偏好按需挂载（默认开，`EditorSettingsDialog` 增开关，
  三语 i18n）；诊断消息走三语文案表。
- **可选增强（不排期）**：chktex 经 tex 镜像 + `POST /api/project/lint-latex`
  纯转换端点（沿袭 format-latex 模式）；触发条件为纯模块方案实测误报率
  不可接受时再评估。

### 4.4 阶段四：跨文件大纲 + 当前公式固定预览卡

- **后端**：`GET /api/project/outline`（挂 project_router，editor service
  层实现）——全项目 `.tex` 分节扫描（复用 `outlineScan` 的命令正则口径，
  经 `asyncio.to_thread()`），响应 `{files: [{file_path, entries: [{level,
  title, line}]}]}`；项目级缓存 + 保存失效（沿袭 latex-symbols 缓存模式）；
  响应只含相对路径。
- **前端**：`useEditorOutline` 扩展"本项目"数据源；左栏大纲面板增加
  当前文件/本项目双视图（当前文件视图行为不变）；本项目视图按文件分组、
  激活文件置顶展开，点击条目联动打开对应 tab 并行首定位。
- **KaTeX 固定预览卡**：编辑区底部悬浮卡片（不进右栏），订阅 stats hub
  光标通道，防抖约 400ms 用 `findFormulaAt` 取光标所在公式、`loadKatex`
  渲染；无公式/渲染失败静默隐藏（与悬停浮层的失败文案策略不同：固定卡
  常驻场景下错误提示反而构成噪声）。

### 4.5 阶段五：编辑器助手接入联网搜索与学术检索（动工前过专项评审，见 Q12）

- **工具白名单**：`editor_chat.py` 的 `_EDITOR_TOOL_NAMES` 扩入 Writing Chat
  主对话注册的联网搜索与学术检索工具（`web_search`、`search_scholarly_works`
  等，以 `conversation.py` 注册表与 [function-calling-tools.md](function-calling-tools.md)
  为准）；工具 schema 获取路径不变（`_get_editor_tools` 过滤）。
- **确认流**：编辑器 SSE 流新增 `tool_pending` 事件；`AssistantPanel` 渲染
  确认/取消卡片，动作复用 Writing Chat 既有 confirm/cancel 端点与
  pending_interactions 基础设施（多用户表驱动 + 单用户 Event 路径不变）；
  DDG 挑战复用 `hooks/writing-chat/captcha` 的交互通道在助手侧栏内完成。
- **提示词**：`prompts.py` 的 `editor_assistant.chat_system` 增补工具使用
  约定（何时检索、引用如何给到缓冲区建议）；提示词改动遵循集中管理规范。
- **功能开关**：沿用网络访问既有灰度开关与 `editor.assistant_enabled` 双重
  把关，不新增配置项；SSE 事件归约扩展 `assistantStream.js` 并补单测。
- 本阶段为全部阶段中唯一跨"编辑器 ↔ Writing Chat 会话基础设施"边界的
  改动，动工前需针对确认流时序（SSE 中断恢复、pending TTL 过期）单独
  评审，必要时拆为"工具接入"与"确认流"两个子阶段交付。

### 4.6 阶段六：自研 MIT Lezer 子集语法

- **包结构**：`frontend/src/lib/editor/latexGrammar/`（`.grammar` 源 +
  highlight tags 映射 + LanguageSupport 组装）；`lezer-generator` 作为
  devDependency（MIT），npm script 在 dev/build 前编译生成 parser（生成物
  不入库，构建可重复）；`@lezer/highlight` 为已有依赖直接复用；
- **入口切换**：`language.js` 头注契约不变（语言包进入编辑器的唯一入口），
  导出改为 `latexLanguageExtension(suffix)`：`.tex` / `.sty` / `.cls` 返回
  Lezer LanguageSupport，`.bib` 暂沿用 stex StreamLanguage（自研最小 bib
  语法列为可选尾项）；调用点随 per-tab 文件后缀传参；
- **子集范围**：P0 覆盖行注释、命令（含 `*` 变体、`{}` / `[]` 参数）、
  `$...$` / `$$...$$` / `\(...\)` / `\[...\]` 数学区、`\begin`/`\end`
  环境、分组、上下标；verbatim 类环境与 `\verb` 参数原样吞掉（与
  `outlineScan` / `formulaHoverScan` 的豁免口径一致）；分节命令映射
  heading tag；
- **P1 增量**：环境与分节级折叠（foldable node props）、环境体自动缩进
  （indentNodeProp）、括号匹配精确化；
- **明确不追求**：catcode / `\makeatletter` 级完全正确、`$` 配对零误判
  ——依赖 Lezer 错误恢复（error-insensitive）保证极端输入不崩，局部
  高亮降级可接受；
- **验收**：与 stex 高亮做回归对照清单；2MB 上限大文档键入不卡（Lezer
  增量解析）；grammar 解析树断言进 node --test（lezer parser 可在 node
  环境直接运行）。

## 5. 阶段划分与验收

| 阶段 | 内容 | 验收要点 |
| --- | --- | --- |
| 一：SyncTeX | `-synctex=1`、forward/backward 端点、pdf.js 预览替换、双向联动 | 三引擎编译产物均可正向定位与反向跳转；既有编译页/git_diff 编译行为零变化（synctex 默认关）；pdf.js 分块懒加载首屏零请求；synctex 端点响应无绝对路径；`tests/test_api` 补端点用例（mock 沙箱执行，严禁真实网络） |
| 二：自定义片段 | 偏好 v2 迁移、customCommands 合并层、设置分区管理 | 自定义条目可在补全/命令面板/插入三入口命中；刷新后保留；v1 偏好导出文件可导入不丢数据；清洗函数纯模块单测覆盖 |
| 三：实时诊断 | diagnostics 纯模块 + lint 接线 + 偏好开关 | 未闭合环境/花括号失衡在编辑停顿后出现；verbatim/注释/`\verb` 内不误报；超 200 条截断；开关即时生效；纯模块单测覆盖正反例 |
| 四：跨文件大纲 + 预览卡 | outline 端点 + 大纲双视图 + KaTeX 固定卡 | 跨文件章节点击正确开 tab 定位；保存后大纲缓存失效；当前文件视图行为不变；固定卡跟随光标、空态隐藏；端点响应无绝对路径 |
| 五：助手联网 | 工具白名单扩展 + tool_pending 确认流 + DDG 挑战交互 | 助手可发起搜索/学术检索并经确认执行；拒绝后模型收到取消语义；挑战可在侧栏完成；开关关闭时工具不可达；assistantStream 归约单测覆盖新事件 |
| 六：Lezer 语法（6a/6b） | latexGrammar 包 + `language.js` 按后缀分流；6a 高亮子集，6b 折叠 + 环境缩进 | 四类后缀高亮不回归（`.bib` 保持现状）；回归对照清单通过；2MB 文档键入不卡；grammar 解析树 node --test 覆盖；`THIRD_PARTY_NOTICES.md` 登记 lezer-generator |

阶段一独立先行（改动面覆盖编译链路与预览层）；二至四无相互依赖；五压轴
且动工前过专项评审；六与一至五无依赖，可在任意时点以 6a/6b 两批插入。

## 6. 全局约束（每阶段适用）

- 落盘仍走 save-file 单一路径 + MD5 乐观锁；synctex/outline/lint/预览全部
  零写盘；新增端点响应不返回绝对路径，面向用户的错误消息同步三语
  `errors` 命名空间。
- 新增纯模块一律 node --test 单测；后端新增端点补 `tests/test_api/`
  集成测试（Mock 沙箱与 LLM，严禁真实网络）；同步阻塞与全项目扫描一律
  `asyncio.to_thread()`。
- 新增依赖（`pdfjs-dist`、`@codemirror/lint`、`lezer-generator` /
  `@lezer/generator`，均为 MIT / Apache-2.0 宽松许可）登记
  `THIRD_PARTY_NOTICES.md`；不新增 `config.json` 配置项（行为开关全部走
  编辑器偏好通道）。
- 每阶段同步更新 `docs/modules/editor.md` 与 `PROJECT_SUMMARY.md`；全部
  完成后将本文状态改为"已实施"，并勾销 [editor-productivity-design.md](editor-productivity-design.md)
  §2 非目标清单对应条目与 [editor-design.md](editor-design.md) 非目标中"助手接入
  联网搜索/学术检索"项；README 编辑器条目增量。

## 附录：Lezer 语法升级选型记录（自研 MIT 子集）

v2 计划将"stex StreamLanguage → **MIT 许可的 Lezer LaTeX 语法**"记入
roadmap，但选型调研（2026-09）确认现生态没有满足该前提的现成包：

| 候选 | 许可证 | 结论 |
| --- | --- | --- |
| `@overleaf/lezer-latex`（Overleaf 官方 grammar） | AGPL-3.0-only | 不可直接采用 |
| `codemirror-lang-latex`（texlyre，基于 Overleaf grammar，含折叠/缩进/括号匹配/内置 linter） | AGPL-3.0 | 功能最全，但许可证阻塞 |
| `lezer-tex` | 已废弃 | 不可用 |
| 官方 `@lezer/*` scope | 无 LaTeX grammar | 不存在 |

前端依赖会打包进构建产物（构成再分发，见 `THIRD_PARTY_NOTICES.md` 合规
说明），引入 AGPL 依赖将使前端产物承担 AGPL 义务**采用自研
MIT 子集语法**，路线见 Q14 与 §4.6；"项目整体接受 AGPL"与"维持
StreamLanguage 现状"两条路径不再考虑。
