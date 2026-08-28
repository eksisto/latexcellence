# LateXcellence — 前端开发文档

## 目录

- [入口与路由](#入口与路由)
- [状态管理 useStore.js](#状态管理-usestorejs)
- [API 层 api/index.js](#api-层-apiindexjs)
- [工具模块（errorMessages / formulaWrap / highlight）](#工具模块)
- [国际化（i18n）](#国际化i18n)
- [核心组件](#核心组件)
- [核心 Hook](#核心-hook)
- [样式架构](#样式架构)
- [代码展示与语法高亮](#代码展示与语法高亮)
- [CSS 文件 src/index.css + 主题目录 src/theme](#css-文件-srcindexcss--主题目录-srctheme)
- [主题切换机制](#主题切换机制)

## 入口与路由

**`main.jsx`** → 将 `<App />` 渲染到 `#root`。

**`App.jsx`** → 使用 React Router 定义路由：

| 路径 | 组件 | 发送模式 |
| --- | --- | --- |
| `/` | WelcomePage | — |
| `/workspace` | WorkspacePage | — |
| `/typo-check` | TypoCheckPage | SSE（useParagraphCheck） |
| `/expression-opt` | ExpressionOptPage | SSE（useParagraphCheck） |
| `/terminology-check` | TerminologyCheckPage | SSE（useParagraphCheck） |
| `/paragraph-translate` | ParagraphTranslatePage | SSE（useParagraphCheck） |
| `/formula-ocr` | FormulaOCRPage | JSON request |
| `/table-ocr` | TableOCRPage | JSON request |
| `/template-gen` | TemplateGenPage | **SSE Agent 流**（手写 fetch reader，无 hook） |
| `/code-review` | CodeReviewPage | SSE（useParagraphCheck） |
| `/style-modify` | StyleModifyPage | JSON request（粘贴代码）/ SSE Agent 流（项目 Agent） |
| `/format-convert` | FormatConvertPage | JSON request |
| `/git-diff` | GitDiffPage | JSON request |
| `/versions` | VersionManagePage | JSON request |
| `/writing-chat` | WritingChatPage | SSE（useWritingChat） |
| `/bib-manager` | BibManagerPage | JSON request |
| `/plot-gen` | PlotGenPage | **SSE Agent 流**（手写 fetch reader，无 hook） |

全局右上角工具栏（AppToolbar）包含 LanguageSwitcher、ThemeSwitcher、ThemeToggle、工作区入口、SettingsDialog，以及认证页改绑后端入口（AuthConnectionButton：未登录/待审批时显示）；`App.jsx` 内联了一份全站共享的右下角 `\LaTeX` 字标背景装饰（`bgSvg`，含 `--primary` 圆形 + 双色字标路径），所有路由共享。

`TemplateGenPage` 不使用 `useParagraphCheck` 或 `useWritingChat` hook，而是手写 `fetch` reader 对接 `/api/template-gen/generate` SSE 流，在本地 `steps` state 中累积 `plan`/`tool_call`/`tool_result`/`compile`/`warning`/`done` 事件并逐条渲染时间线。文件结果在 `done` 事件后通过 `/files` 和 `/file` 端点按需加载，支持通过 `/download` 下载整个沙箱为 ZIP 文件。生成完成后提供 Agent 日志卡片，可查看（弹窗内逐条展示 `trace.jsonl` 事件）或导出（下载 JSON），对应 `/trace` 与 `/trace/download` 端点。

`PlotGenPage` 与 TemplateGenPage 同模式：`api.plotGen.upload` 先上传数据，`generate`/`refine` 通过手写 `fetch` reader 消费 `/plot-gen/agent/generate` 等 SSE 流，在本地 `steps` state 累积 Agent 事件并渲染时间线；会话文件与 trace 通过 `/files`、`/file`、`/trace` 端点加载。

`StyleModifyPage` 顶部使用 segmented control 切换项目 Agent 与粘贴代码。项目 Agent 模式通过 `useStyleModifyAgent` 消费 `/style-modify/agent/run` 和 refine SSE，展示工具步骤、逐文件 diff、编译状态、proposal 操作、refinement 输入和 trace 查看/导出；粘贴代码模式保留旧交互。

WelcomePage 采用「标题页 + 点线目录」构图：字标、slogan、「进入工作区」入口；功能模块按写作流程以 § 编号分组，每条目以点线引导至图标。模块分组定义在 `pages/WelcomePage.jsx` 的 `featureGroups`，§ 号映射 `SECTION_BY_GROUP` 定义在 `lib/writingFlow.js`（WelcomePage / PageHeader 共享）。

## 状态管理 `useStore.js`

使用 Zustand 5 + `persist` 中间件，持久化键 `"latexcellence-project"`。setter 支持 function updater 模式。持久化水合完成后设置 `hydrated: true`。

状态切片：水合状态、功能开关、系统状态（主题色、workspaceId）、项目状态（路径、文件树、主文件）、style_modify Agent 恢复标识（session_id / revision / operation_id / mode）、写作助手（会话列表、消息）、各检查模块段落/结果、编译结果/进度、加载状态。`partialize` 只持久化 Agent 恢复所需标识，不持久化完整源码、diff 和 trace。

## API 层 `api/index.js`

所有请求通过 `request(url, options)`，自动处理 Content-Type、BASE_URL 前缀、错误翻译。FormData 请求通过 `requestForm(url, formData, method)`。需要工作区隔离的端点通过 `appendWs(url, workspaceId)` 拼接 `workspace_id` 参数。

后端连接配置（API 基地址 / 访问令牌的运行时读写、认证请求头、EventSource 令牌追加、连接测试）集中在 `api/connection.js`，`api/index.js` 从该模块导入并保持兼容导出；纯 URL 计算（`normalizeApiBase` / `appendTokenToUrl` / `resolveUrlPath`）仍位于 `utils/apiUrl.js`，可被 `node --test` 直接单测。

默认后端地址可由维护者构建时配置：在 `frontend/.env`（模板见 `.env.example`）中设置 `VITE_API_BASE_URL` 后重新 `npm run build`，产物默认即连接该地址，用户打开前端无需手动填写。运行时优先级：设置页（localStorage）→ `VITE_API_BASE_URL` → 内置默认 `http://localhost:8000/api`；dev 模式下 Vite 代理目标也从该变量派生（去掉末尾 `/api`）。

API 按功能分组暴露（health、config、project、typoCheck、expressionOpt、terminologyCheck、paragraphTranslate、formulaOCR、tableOCR、templateGen、codeReview、compile、gitDiff、styleModify、formatConverter、bibManager、writingChat），LLM 相关接口自动注入 `language` 参数。`templateGen.generate` 通过 `fetch` reader 解析 SSE 流（与 `writingChat.send` 同模式），实时推送 `plan`/`tool_call`/`tool_result`/`compile`/`warning`/`done`/`error` 事件；`templateGen.download` 支持下载整个沙箱为 ZIP 文件；`templateGen.trace` / `templateGen.downloadTrace` 支持查看与导出 Agent 运行追踪日志。`styleModify.agent` 分组提供 run、refine、session、trace、apply、undo、delete 接口，run/refine 使用 SSE，其余使用 JSON。

## 工具模块

### utils/projectAccess.js

多用户/单用户双模式的打开与写权限判定纯函数（无副作用，`node --test` 直接单测）：`isProjectOpened`（模式感知的项目已打开判定）、`hasWriteRole`（写角色白名单 owner/editor，角色未知按只读兜底）、`emptyStateCta`（空态按钮按模式指向 `/workspace` 或 `/projects`）。由 `useIsOpened` / `useCanWrite` 及各模块页引用；回归测试见 `test/multiUserGuards.test.mjs`。

### utils/errorMessages.js

后端英文错误消息 → 当前语言翻译。精确匹配 + 正则模式匹配，翻译资源位于 `errors` 命名空间。调用点包括 `request()`、`requestForm()`、`useParagraphCheck.js`、`useWritingChat.js`。

### utils/format.js

展示格式化共享工具：`formatBytes`（字节数人性化展示，B/KB/MB/GB 1024 进制）、`formatDate`（ISO 时间 → 本地化字符串，无法解析返回 "—"）。项目工作台、管理面板、上传对话框等处共用，避免各页维护逐行等价的副本。

### utils/formulaWrap.js

公式行内/行间包裹重写工具（与后端 `formula_ocr/service.py` 的 `_strip_math_wrappers` / `_wrap_latex_for_paste` 语义一致）：剥离 `$$` / `\[ \]` / `\( \)` / `$` / 空壳显示环境包裹，并按 `inline` 标志重写为 `$...$` / `\[...\]`；独立显示环境原样保留，宏包注释前缀（`% \usepackage{...}`）在重写时保留。由 `FormulaOCRPage.jsx` 的行内/行间切换使用；`npm test`（`node --test`）覆盖回归用例。

### utils/highlight.js

highlight.js 的按需注册与统一入口：

- `styles/code-highlight.css` 由 `CodeBlock` / `MarkerText` / `CodeEditorInput` 组件引入：高亮颜色独立成文件，仅在使用到高亮的模块随 chunk 加载，未用到高亮的页面不会引入；`utils/highlight.js` 保持为无样式副作用的纯逻辑模块，可直接被 `node --test` 测试
- 仅注册当前用到的语言（latex / tex / bibtex / markdown / python / json / xml / html / diff / plaintext），新增语言在这里 `registerLanguage` 即可，控制构建体积
- `highlightHtml(text, language)`：将源码高亮为已转义的 HTML 字符串（渲染期派生），供非行号模式的 `CodeBlock`、`MarkerText` 行内片段与 `CodeEditorInput` 高亮层直接注入，不依赖 effect 就地改 DOM
- `highlightEditorHtml(text, language)`：`CodeEditorInput` 专用入口——在 `highlightHtml` 基础上，源码以换行结尾时补一个零宽空格，保证高亮层与 textarea 的末尾空行行数一致（浏览器可能丢弃 `<pre>` 内容的尾部换行）
- `highlightToHtmlLines(code, language)`：将代码高亮后按行拆分为自闭合的 HTML 片段数组（跨行 span 会按行重新闭合），供行号布局直接注入
- `languageFromFilename(filename)`：按扩展名推断语言（`.tex/.sty/.cls/.ltx` → latex，`.bib` → bibtex 等）
- BibTeX 语法为本地轻量实现（highlight.js v11 已移除内置 bibtex）：覆盖 `@entry`、字段名、引号字符串与注释

## 国际化（i18n）

基于 `i18next` + `react-i18next` + `i18next-browser-languagedetector`。支持简体中文（`zh`）、繁體中文（`zh-TW`）、英文（`en`），翻译按命名空间拆分：`common`、`pages`、`components`、`hooks`、`errors`。Fallback 语言为简体中文。

语言检测会将浏览器返回的完整语言标签归一为受支持代码（`en-US` → `en`，`zh-CN` → `zh`，`zh-TW` / `zh-HK` / `zh-Hant` → `zh-TW`），避免切换器显示未匹配的原始标签；归一化逻辑与后端语言名映射集中在 `src/i18n/language.js`。

核心接口：`getUiLanguage()` 返回完整语言名称（`Chinese` / `Traditional Chinese` / `English`）供后端 LLM 输出语言控制；`i18n.language` 返回归一后的语言代码用于前端 UI 判断。

界面语言是纯前端设置：由工具栏的 `LanguageSwitcher.jsx` 切换（与主题控件同款下拉交互），选择结果经 i18next LanguageDetector 缓存到 localStorage。它不属于后端 config.json，因此后端未连接、未登录（含多用户非管理员）时同样可以切换。

新增中文文案时，需同步更新 `zh.json` 与 `zh-TW.json`。

## 核心组件

| 组件 | 功能 |
| --- | --- |
| `CodeBlock.jsx` | 统一代码块展示组件（`code` / `language` / `variant` / `highlight` / `lineNumbers` props），所有只读 LaTeX / TikZ / BibTeX / 日志展示统一走此组件；默认显示左侧行号；同时导出 `latexEditorClass` 共享代码编辑输入样式 |
| `CodeEditorInput.jsx` | 可编辑代码输入框（高亮版）：底层 `<pre>` 渲染 highlight.js 高亮结果、上层透明 textarea 保留光标/选区/滚动，两者共用同一套排版规则保证逐字对齐；供粘贴 LaTeX 源码等需要边输入边高亮的场景使用 |
| `MarkerText.jsx` | 将 `__CMD_NNNN__` 等 marker 解析为彩色内联标签；`variant="code"`（默认）将纯文本片段渲染为 `.code-inline` 并做 LaTeX 高亮（渲染期派生 HTML，按 token 经 `useMemo` 缓存），`variant="text"` 保持原样（用于 diff 等已有语义配色的场景）。仅用于段落预览等含 marker 的展示场景。**结果卡片中**的 `change.original`/`change.corrected` 等字段因后端已还原 marker，直接以文本渲染，无需 `MarkerText` 包裹 |
| `ParagraphContext.jsx` | 展开显示当前段落前后上下文 |
| `ProjectExplorer.jsx` | 递归文件树：目录展开/折叠，文件选择 |
| `PDFPreview.jsx` | iframe 嵌入 PDF 预览 |
| `components/editor/BinaryFilePreview.jsx` | 二进制只读预览唯一渲染分支：位图 `<img>` 居中 contain / PDF 走 `PDFPreview`，加载文案经 `loadingKey` 定制；填满父容器高度的纯渲染契约（blob URL 拉取与释放由调用方负责），编辑器覆盖层 `FilePreviewOverlay` 与工作台文件树预览共用 |
| `PromptEditor.jsx` | 自定义提示词编辑框 |
| `ConnectionPanel.jsx` | 后端连接面板：编辑 API 基地址 / 访问令牌并测试连接，打开对话框时自动探测一次连接状态 |
| `settings/` 分区组件 | 设置对话框各配置分区（Server / Security / LLM / Features / Function Calling / HTTP / Logging / MultiUser / Sandbox / Smtp）与共享原语（`ConfigSection` / `CheckboxField` / `NumberField`） |
| `settings/NumberField.jsx` | 设置分区共享数字输入（草稿态）：清空/删除等非法中间值不回弹，失焦时归一化到 `[min, max]`，输入中仅合法整数即时提交。MultiUserSection 与 SmtpSection（端口/限速/重试）共用 |
| `SettingsDialog.jsx` | 设置对话框外壳：加载/保存 config.json、后端连接面板与各配置分区组装；功能开关保存后同步全局 Store |
| `FolderPickerDialog.jsx` | 文件夹选择对话框，逐级进入子文件夹 |
| `MarkdownRenderer.jsx` | 基于 react-markdown + remark + rehype-katex |
| `ThemeToggle.jsx` | 亮/暗模式切换 |
| `ThemeSwitcher.jsx` | 配色主题切换下拉，自动发现主题文件 |
| `LanguageSwitcher.jsx` | 界面语言切换下拉（简中 / 繁中 / 英语）：纯前端设置，常驻右上角工具栏，不依赖后端连接与登录状态；选择经 i18next LanguageDetector 持久化到 localStorage |
| `components/ui/sonner.jsx` | 全局提示入口：使用内联 SVG 面图标、主题感知的 Toast classNames 与统一间距 |
| `ConfirmDialog.jsx` | 通用确认对话框（destructive 样式 + 禁止遮罩关闭） |
| `ImageOCRPage.jsx` | 通用图片 OCR 页面组件（拖拽/粘贴/识别/复制；`renderResultMeta` 可接收 `(result, setResult)` 第二参数更新结果，FormulaOCR 未判定切换控件依赖此透传） |
| `Wordmark.jsx` | STIX Two Text 排 "LaTeXcellence" |
| `PageHeader.jsx` | 全站共享页头：返回链接 + `groupKey` 自动生成 "§N · 分组" kicker + 页面标题 + 右侧操作位（所有页面共用） |
| `EmptyState.jsx` | 共享空状态：细环图标 + 标题 + 描述 + 可选 CTA（无项目 / 恢复中 / 空结果） |
| `ProofDiff.jsx` | 原文→修改后对照行：原文衬线删除线、改后加粗（`modified` 可传字符串或 ReactNode，术语检查传自定义 Input）；`code` prop（默认开启）将两侧切换为等宽字体 |
| `ResultFilterBar.jsx` | 结果过滤工具栏（搜索框 + 类型下拉 + 段落跳转 + 重置） |
| `UndoRedoButtons.jsx` | 可复用撤销/重做按钮组：内部使用 `useUndoRedo`，轮询 undo-status（页面不可见时暂停），支持 Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y 快捷键与撤销确认弹窗；快捷键由组件级共享监听处理（仅最近挂载实例响应）；用于 workspace、逐段检查各页与参考文献管理（文献库） |
| `ProgressBar.jsx` | 检查进度条组件 |
| `UnlistedChanges.jsx` | 展示「模型修改了全文但未列出条目」（unlisted_changes）的琥珀色提示：`UnlistedChangesBadge`（卡片头标记）+ `UnlistedChangesBody`（全文预览 + 应用/忽略），typo_check / expression_opt / paragraph_translate 共用，全文经 MarkerText 渲染 |
| `ColorField.jsx` | 颜色选择器组件 |
| `BibManagerLibrary.jsx` | 参考文献库管理面板（文件列表、条目 CRUD、重复检测、统计）；写操作入口（撤销/重做、条目编辑/删除）经 `useCanWrite` 门控，viewer 只读 |
| `FormatResultCard` | 格式识别结果展示卡片（`React.memo` 组件，展示格式名称、置信度、字段列表、LaTeX 样式推荐） |

## 核心 Hook

| Hook | 功能 |
| --- | --- |
| `useParagraphCheck.js` | 封装逐段 LLM 检查公共逻辑（预览、检查/优化、排序、暂停/继续/停止、应用、refresh 持久化）。支持 `resolveInput` 多文件模式。内置乐观锁：读取段落时缓存文件 MD5，应用修改时传回，冲突（409）后自动刷新 MD5 并提示重试。SSE 结果通过 rAF 微批次合并；`done` 事件到达时先排空待 flush 缓冲再收尾，避免一次性推送全部结果的模块（如术语一致性检查）在流结束时丢失结果。 |
| `useEnabledFeatures.js` | 从后端加载功能开关到 Zustand store，幂等。 |
| `useIsOpened.js` | 模式感知「项目已打开」判定（`utils/projectAccess.js` 的 `isProjectOpened`）：单用户看 `projectPath`，多用户看 `projectId`（多用户下 projectPath 恒为 null）。模块页空态守卫与检查启动守卫统一引用，避免各页复制同形条件漏改。 |
| `useCanWrite.js` | 多用户写权限单一事实源：白名单 owner / editor 可写，viewer 及角色未知一律只读；单用户恒可写。内部复用 `utils/projectAccess.js` 的 `hasWriteRole`。 |
| `useConfigStatus.js` | 多用户公开配置探测（`/config/status`）共享层：模块级 in-flight Promise 去重 + 短 TTL 结果缓存，多个派生 hook 同时挂载（如登录页）只发一次请求。返回 status 对象，null 表示未知（单用户/请求中/探测失败）。 |
| `useRegistrationEnabled.js` | 基于 `useConfigStatus` 派生 `registration_enabled`；仅明确 false 时调用方隐藏注册入口（登录页隐藏链接、注册页展示未开放提示），未知时保持入口由后端 400 兜底。 |
| `useEmailAvailable.js` | 基于 `useConfigStatus` 派生 `email_available`；仅明确 false 时隐藏「忘记密码」入口（登录页），未知时不隐藏，后端 forgot-password 统一降级响应兜底。 |
| `useFavicon.js` | 按亮/暗模式切换浏览器标签页图标（MutationObserver 监听 `<html>` class）。 |
| `useWritingChat.js` | 封装写作助手 Chat 全部逻辑（会话管理、SSE 流式、搜索/学术结果、深度思考解析、生成编辑、应用编辑、助手管理）。内部使用子 hooks。 |
| `useStyleModifyAgent.js` | 封装 style_modify 项目 Agent：创建/恢复/删除 session、SSE 生命周期、refinement、proposal、apply/undo、trace 查看导出和 `translateError()` 处理。 |
| `useThemeManager.js` | 多主题自发现 hook，自动发现 `theme/` 目录下 CSS 文件。 |
| `useTheme.js` | 暗/亮主题切换，支持 View Transitions API 平滑过渡。 |
| `useUndoRedo.js` | 撤销/重做状态管理：挂载、文件切换与操作后刷新后端 undo-status 状态；请求带 AbortController 取消（防止重叠与过期响应覆盖），撤销/重做执行期间跳过轮询。由 UndoRedoButtons 组件统一封装使用。 |
| `useResultFilter.js` | 段落检查结果搜索/过滤/跳转，与 ResultFilterBar 配套使用。 |
| `usePlotGen.js` | 封装科研绘图 Agent：数据上传、生成/微调 SSE 流、会话管理、trace 查看导出。 |
| `useFormatAnalysis.js` | BibManager Step 1：格式识别状态与处理器 |
| `useMetadataExtraction.js` | BibManager Step 2：元数据提取状态与处理器（含 DOI 查询） |
| `useBibtexGeneration.js` | BibManager Step 3：BibTeX 生成与编辑状态 |
| `useBibFileSave.js` | BibManager Step 4：Bib 文件写入与条目管理 |
| `useLatexGeneration.js` | BibManager Step 5：LaTeX 代码生成 |

### `useParagraphCheck` 配置接口

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `apiModule` | object | 必填 | API 模块（`api.typoCheck` / `api.expressionOpt` 等） |
| `checkMethodName` | string | `"check"` | 调用 apiModule 的方法名 |
| `correctedTextField` | string | `"corrected"` | 结果中修正文本的字段名（expressionOpt 为 `"optimized"`） |
| `storeSlice` | object | 必填 | Zustand store 切片键（paragraphs, results, loading...） |
| `messages` | object | 见源码 | 自定义提示文案（加载中、成功等） |

**返回值**：`projectPath`、`activeFile`、`workspaceId`、`texFiles`、`paragraphs`、`results`、`isLoading`、`progress`、`restoring`、`applyingAll`、`isPaused`、`error`、`message`、`resolveInput`、`activeFiles`、`operationId`、`handlePreview`、`handleCheck`、`handlePauseResume`、`handleStop`、`handleApplyChange`、`handleApplyAll`、`handleIgnoreChange`、`handleFileChange`。

### `useUndoRedo` 配置接口

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `activeFile` | string | 必填 | 当前文件路径 |
| `workspaceId` | string | 必填 | 工作区 ID |
| `onAfterUndoRedo` | function | `null` | 撤销/重做成功后的回调（如刷新段落/条目） |

**返回值**：`canUndo`、`canRedo`、`busy`、`handleUndo`、`handleRedo`、`refresh`。页面级按钮推荐直接使用 `UndoRedoButtons` 组件（自带 2.5s 状态轮询，页面不可见时暂停；快捷键与确认弹窗）。

### `useResultFilter` 返回值

| 返回值 | 类型 | 说明 |
| --- | --- | --- |
| `searchQuery` / `setSearchQuery` | string / function | 搜索关键词 |
| `typeFilter` / `setTypeFilter` | string / function | 类型过滤（`"all"` 或具体类型） |
| `availableTypes` | string[] | 从 results 自动提取的类型列表 |
| `filteredResults` | array | 过滤后的结果数组 |
| `jumpToParagraph(idx)` | function | scrollIntoView 跳转到指定段落 |
| `reset()` | function | 重置所有过滤器 |
| `hasFilters` | boolean | 是否有活跃过滤器 |

## 样式架构

| 方面 | 选择 |
| --- | --- |
| CSS 框架 | Tailwind CSS v4（`@tailwindcss/vite` 插件） |
| 组件库 | shadcn/ui（`style: "base-nova"`） |
| 主题系统 | CSS 自定义属性（oklch 色彩空间），主题文件在 `theme/` 目录，`data-theme` 属性选择 |
| 深色模式 | `.dark` class 挂载在 `<html>`，配合 View Transitions API |
| 多主题 | `theme/*.css` 自动发现，与亮暗模式无耦合 |
| 字体 | 学术衬线显示字体 STIX Two Text（字标 / § 编号 / 英文 small-caps 标签）+ 霞鹜文楷正文 + Noto Sans SC 标题 + JetBrains Mono 等宽，配完整中文回退栈；dev 直接读本地 npm 字体包（可离线调试），build 不打包字体、改为在 `index.html` 注入 cdnjs 固定版本链接 |
| 图标 | lucide-react |
| 类合并 | `cn()`（clsx + tailwind-merge） |

## 代码展示与语法高亮

所有 LaTeX / TikZ / BibTeX 源码展示统一由两个组件 + 一套 CSS token 控制，后续调整外观只需改组件或变量，不需要逐页修改：

- **块级展示**：`CodeBlock`（`variant="block"` 带背景/边框/圆角，`variant="bare"` 仅排版，默认开启 `lineNumbers` 左侧行号；行号列不参与选中复制），OCR 结果、格式转换/样式修改结果、代码审查样张与段落预览、写作助手编辑建议、工作区段落预览、模板/绘图 Agent 的文件内容与日志均使用
- **行内/段落展示**：`MarkerText` 默认 `variant="code"`，段落预览、`ParagraphContext` 上下文、代码审查建议等含 marker 的 LaTeX 文本以 `.code-inline` 等宽渲染并高亮；diff 类场景（`AnnotatedSource` / `DiffView` / 代码审查建议对照）传 `variant="text"`，避免高亮 token 颜色覆盖红/绿语义色
- **编辑输入**：`latexEditorClass` 常量提供共享排版（格式转换/样式修改输入、代码审查定稿编辑、写作助手手动编辑、BibTeX 编辑共用）；需要边输入边高亮的输入框（如 style_modify 的 LaTeX 源码窗口）使用 `CodeEditorInput`——高亮层与透明 textarea 叠加，光标/选区/滚动保留在 textarea
- **CSS token**：`index.css` 定义 `--code-bg` / `--code-fg` / `--code-border` 与组件类 `.code-block` / `.code-block-bare` / `.code-inline` / `.code-lines` / `.code-line-num`（位于 `@layer components`）；`styles/code-highlight.css` 独立承载语法 token 变量（`--code-token-*`，亮/暗默认值）与 `.hljs-*` 映射，由 `CodeBlock` / `MarkerText` / `CodeEditorInput` 按需引入。各主题文件在亮/暗两档覆盖 `--code-*`，也可按主题覆盖 `--code-token-*`。utility 类可覆盖组件类默认值（如 `p-2`、`text-sm`）
- **高亮接入**：`utils/highlight.js` 是唯一注册与执行入口，token 颜色经 `.hljs-*` 类映射到 `--code-token-*`，不引入官方主题，自动适配现有主题与明暗模式

## CSS 文件 `src/index.css` + 主题目录 `src/theme/`

- `index.css`：Tailwind 导入、自定义字体、深色变体定义、主题内联令牌、基础样式。另含设计体系工具：`--font-display`（STIX Two Text）显示字体 token、`.text-small-caps` 英文小大写标签、`.tex-e` / `.tex-x` 字标 "TeX" 排字姿态、`.reveal` 入场动效（错落 + 尊重 `prefers-reduced-motion`）、代码块 chrome token（`--code-*`）与 `.code-block` / `.code-inline` 组件类；悬浮浮层圆角与阴影由 `--surface-radius` / `--surface-shadow` 统一管理（toast 与左下返回按钮共用）
- 全局提示：`components/ui/sonner.jsx` 提供共享 Sonner 实例与内联 SVG 面图标；`index.css` 中的 `[data-sonner-toaster]` 规则保持安静的主题卡面（左侧 1/3 极淡状态色渐变：成功绿 / 提示蓝 / 错误红，无底色状态小图标，主题/暗色模式自适应），并保留 action/cancel/close 的键盘焦点与移动端宽度规则。后续调整提示外观应集中在这两个文件，不要在业务调用处追加外壳 class
- `styles/code-highlight.css`：代码语法高亮颜色（`--code-token-*` 变量 + `.hljs-*` 映射），独立成文件、由 `CodeBlock` / `MarkerText` / `CodeEditorInput` 在用到高亮时引入
- `fonts/index.css`：dev 模式专用字体入口，由 Vite 插件按环境注入；生产构建不引用该文件，改由 `vite.config.js` 注入 cdnjs 字体样式链接，静态产物只保留 KaTeX 数学字体
- `theme/` 下主题 CSS 文件：youminghei（幽冥黑）、jingtailan（景泰蓝）、hupohuang（琥珀黄）、ningyezi（凝夜紫）、tingwulv（庭芜绿）、hailuocheng（海螺橙）、yingtaohong（樱桃红）
- `useThemeManager.js`：自动发现主题文件，管理 `data-theme` 属性
- 配色架构：所有主题共用同一套中性灰阶（背景/卡片/边框/文字，亮暗各一档），主题身份由单一色相承载，统一派生 `--primary` / `--accent` / `--ring` / `--selection-bg`；语义色（`--destructive`、`--brand-mark`）跨主题一致，深色模式下字标统一反白
- 新增主题只需添加 CSS 文件 + 添加 i18n 翻译键，自动发现

## 主题切换机制

亮/暗模式：读取 localStorage → 回退到系统偏好 → `document.startViewTransition()` → 切换 `<html>` 的 `.dark` class。`:root` 层面过渡 CSS 自定义属性，避免逐元素过渡的性能问题。

浏览器不支持 View Transitions API（或用户开启减少动画）时，回退为逐元素 CSS 过渡：由 `index.css` 的全局规则统一过渡 `color / background-color / border-color` 等属性。注意：元素若声明了自己的 Tailwind `transition-*` 工具类（如 Tabs 滑块指示条的 `transition-[left,width,...]`、Switch 滑块的 `transition-[transform,...]`），会覆盖全局 `transition-property`，因此这些工具类必须把会随主题变化的颜色属性一并列入，否则亮暗切换时该元素会瞬间跳变、与整页渐变不同步。

配色主题：`theme/` 目录 CSS 文件自动发现 → Zustand store 持久化 `currentTheme` → `data-theme` 属性 → CSS 选择器生效。与亮暗模式完全独立。

## 多用户模式前端（P5）

> 详细设计见 [multi-user-plan.md](multi-user-plan.md)。

多用户模式（`/api/config/status` 返回 `multi_user: true` 时）下，前端切换为"登录 + 项目工作台"形态；单用户模式保持原有行为（本地目录 + 共享令牌 + 可配置 API 基地址）。

### 请求层与认证（connection.js / api/index.js）

- `multi_user.enabled` 由 `useAuthBootstrap`（`hooks/useAuth.js`）启动时探测：优先同源 `/api/config/status`，失败回退按配置 API 基地址探测；
- 多用户模式下 `getApiBase()` 固定返回同源 `/api`（生产由 FastAPI 挂载 static/ 同源提供，dev 走 Vite `/api` 代理），`getApiToken()` 返回空串，`authHeaders()` 附加双提交 CSRF 头 `X-Latexcellence-CSRF`（值取自可读 Cookie `latexcellence_csrf`），`appendToken()` 不再追加 `?token=`（SSE 同源自动携带 Cookie）；
- `api/index.js` 新增 `auth`（register/login/logout/me）、`admin`（list/approve/reject/disable/enable/reset-password preview·confirm/quota/delete）、`projects`（list/recycle-bin/upload/rename/remove/restore/permanentDelete/download/permissions/transfer）分组；`project.openById` 供按 `project_id` 打开；
- 请求层 401 → `notifyUnauthorized()` → store `handleUnauthorized()` 清空认证与项目状态，路由守卫自动跳转登录页；
- `translateError()` 支持结构化错误 `{ error, message }`，并收录全部多用户错误码（unauthorized/no_permission/quota_exceeded/invalid_csrf 等）。

### 状态（useStore.js auth 切片）

- `authStatus`（loading/single/authenticated/pending/unauthenticated）、`authUser`、`quota`、`usage`（来自 `/api/auth/me`）；
- `projectId` / `projectRole`（owner/editor/viewer）持久化，刷新后按 `project_id` 恢复项目；
- `clearAuth` / `handleUnauthorized` / `logoutUser`（吊销会话 + 清空项目）。

### 路由守卫（App.jsx）

- `/login`、`/register`、`/waiting`：认证页守卫（已登录 → `/projects`）；
- `/projects`（项目工作台）、`/admin`（仅 admin）及所有业务页面：`RequireAuth`（未登录 → `/login`）；
- 单用户模式 `authStatus === "single"` 全部放行，行为不变；
- 工具栏：多用户模式以图标按钮 + 悬浮提示显示登录 / 登出（提示含用户名）/ 管理入口（admin 可见），样式与主题切换等图标一致；设置按钮仅 admin 可见（配置接口仅管理员可访问）；`ConnectionPanel` 在多用户模式下被注释说明替代。

### 页面

| 页面 | 功能 |
| --- | --- |
| `pages/LoginPage.jsx` | 用户名/密码/记住我，登录成功后经 `/api/auth/me` 恢复并跳转工作台 |
| `pages/RegisterPage.jsx` | 注册（用户名/密码/确认/邮箱），按后端 IP 策略结果分支展示：自动通过 →「直接登录」成功态，否则「待审批」提交态 |
| `pages/PendingApprovalPage.jsx` | 待管理员批准占位页 |
| `pages/ProjectWorkbenchPage.jsx` | 项目列表（我的/共享给我，含 owner 与 role）、配额用量条、上传、回收站、打开/下载/重命名/权限/删除；owner 专属操作 |
| `pages/AdminPage.jsx` | 用户列表（待审批筛选）、批准/拒绝/禁用/启用、重置密码（两步式：先生成临时密码预览，确认后才写入）、配额调整、删除用户；用户行展示注册 IP / 最近登录 IP 与「自动通过」审计标记 |

配套组件：`components/AuthShell.jsx`（认证页外壳）、`components/AuthConnectionButton.jsx`（认证页工具栏「后端连接设置」入口 + 对话框）、`components/UploadProjectDialog.jsx`（zip/7z/rar 文件或 `webkitdirectory` 目录 → 分片直传：并发 3 队列、逐片 `X-Part-MD5` 校验（自研增量 MD5 工具 `utils/md5.js`，WebCrypto 不提供 MD5）、指数退避重试、暂停/继续、localStorage 持久会话支持刷新后重新选择相同内容断点续传；会话建立后的致命错误进暂停态可续传，init/status 阶段失败回 idle 允许直接重试）、`components/ProjectPermissionsDialog.jsx`（按用户名添加协作者、改角色、移除、转交 owner）。

认证页改绑后端：前端实例绑定的后端不可用或用户没有账号时，未登录/待审批状态下 AppToolbar 右上角显示 `AuthConnectionButton`（样式与主题切换等工具栏图标一致），对话框复用 `ConnectionPanel` 的 `onSaved` 形态——显式「保存并刷新」按钮、不失焦静默持久化；保存到 localStorage 后刷新页面重新走启动探测。探测顺序为**显式绑定地址 → 同源 `/api/config/status` → 配置默认基地址**（`useAuth.js`），多用户模式下 API 基地址取「显式绑定 → 探测命中 → 同源 /api」（`connection.js` 的 `multiuserApiBase`）。注意：多用户会话依赖同源 Cookie + 双提交 CSRF，改绑的目标后端若为多用户模式需与前端同源部署；改绑单用户后端（令牌认证）无此限制。

### viewer 写保护（前端）

- 工作区：viewer 隐藏撤销/重做按钮、禁用主文件切换；
- 逐段检查：`useParagraphCheck` 暴露 `canWrite`，viewer 的 `handleApplyChange` / `handleApplyAll` 直接拒绝并提示；
- 写作助手：`useWritingChat.applyEdit` 对 viewer 拒绝；
- 后端 403 `no_permission` 始终兜底。
