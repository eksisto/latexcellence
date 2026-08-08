# LateXcellence — 前端开发文档

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
| `/style-modify` | StyleModifyPage | JSON request |
| `/format-convert` | FormatConvertPage | JSON request |
| `/git-diff` | GitDiffPage | JSON request |
| `/writing-chat` | WritingChatPage | SSE（useWritingChat） |
| `/bib-manager` | BibManagerPage | JSON request |
| `/plot-gen` | PlotGenPage | **SSE Agent 流**（手写 fetch reader，无 hook） |

全局右上角工具栏（AppToolbar）包含 ThemeSwitcher、ThemeToggle、工作区入口和 SettingsDialog；`App.jsx` 内联了一份全站共享的右下角 `\LaTeX` 字标背景装饰（`bgSvg`，含 `--primary` 圆形 + 双色字标路径），所有路由共享。

`TemplateGenPage` 不使用 `useParagraphCheck` 或 `useWritingChat` hook，而是手写 `fetch` reader 对接 `/api/template-gen/generate` SSE 流，在本地 `steps` state 中累积 `plan`/`tool_call`/`tool_result`/`compile`/`warning`/`done` 事件并逐条渲染时间线。文件结果在 `done` 事件后通过 `/files` 和 `/file` 端点按需加载，支持通过 `/download` 下载整个沙箱为 ZIP 文件。生成完成后提供 Agent 日志卡片，可查看（弹窗内逐条展示 `trace.jsonl` 事件）或导出（下载 JSON），对应 `/trace` 与 `/trace/download` 端点。

`PlotGenPage` 与 TemplateGenPage 同模式：`api.plotGen.upload` 先上传数据，`generate`/`refine` 通过手写 `fetch` reader 消费 `/plot-gen/agent/generate` 等 SSE 流，在本地 `steps` state 累积 Agent 事件并渲染时间线；会话文件与 trace 通过 `/files`、`/file`、`/trace` 端点加载。

WelcomePage 采用「标题页 + 点线目录」构图：字标、slogan、「进入工作区」入口；功能模块按写作流程以 § 编号分组，每条目以点线引导至图标。模块分组与编号定义在 `pages/WelcomePage.jsx` 的 `featureGroups` 与 `SECTION_NUMBER`（已导出供复用）。

## 状态管理 `useStore.js`

使用 Zustand 5 + `persist` 中间件，持久化键 `"latexcellence-project"`。setter 支持 function updater 模式。持久化水合完成后设置 `hydrated: true`。

状态切片：水合状态、功能开关、系统状态（主题色、workspaceId）、项目状态（路径、文件树、主文件）、写作助手 Chat（会话列表、消息）、各检查模块段落/结果、编译结果/进度、加载状态。

## API 层 `api/index.js`

所有请求通过 `request(url, options)`，自动处理 Content-Type、BASE_URL 前缀、错误翻译。FormData 请求通过 `requestForm(url, formData, method)`。需要工作区隔离的端点通过 `appendWs(url, workspaceId)` 拼接 `workspace_id` 参数。

API 按功能分组暴露（health、config、project、typoCheck、expressionOpt、terminologyCheck、paragraphTranslate、formulaOCR、tableOCR、templateGen、codeReview、compile、gitDiff、styleModify、formatConverter、bibManager、writingChat），LLM 相关接口自动注入 `language` 参数。`templateGen.generate` 通过 `fetch` reader 解析 SSE 流（与 `writingChat.send` 同模式），实时推送 `plan`/`tool_call`/`tool_result`/`compile`/`warning`/`done`/`error` 事件；`templateGen.download` 支持下载整个沙箱为 ZIP 文件；`templateGen.trace` / `templateGen.downloadTrace` 支持查看与导出 Agent 运行追踪日志。

## 工具模块 `utils/errorMessages.js`

后端英文错误消息 → 当前语言翻译。精确匹配 + 正则模式匹配，翻译资源位于 `errors` 命名空间。调用点包括 `request()`、`requestForm()`、`useParagraphCheck.js`、`useWritingChat.js`。

## 工具模块 `utils/formulaWrap.js`

公式行内/行间包裹重写工具（与后端 `formula_ocr/service.py` 的 `_strip_math_wrappers` / `_wrap_latex_for_paste` 语义一致）：剥离 `$$` / `\[ \]` / `\( \)` / `$` / 空壳显示环境包裹，并按 `inline` 标志重写为 `$...$` / `\[...\]`；独立显示环境原样保留，宏包注释前缀（`% \usepackage{...}`）在重写时保留。由 `FormulaOCRPage.jsx` 的行内/行间切换使用；`npm test`（`node --test`）覆盖回归用例。

## 国际化 `i18n/`

基于 `i18next` + `react-i18next` + `i18next-browser-languagedetector`。翻译按 5 个命名空间拆分：`common`、`pages`、`components`、`hooks`、`errors`。Fallback 语言为中文。

核心接口：`getUiLanguage()` 返回完整语言名称供后端 LLM 输出语言控制；`i18n.language` 返回语言代码用于前端 UI 判断。

## 核心组件

| 组件 | 功能 |
| --- | --- |
| `DiffViewer.jsx` | 显示 LLM 检测到的修改（type 标签、原文/修正文、解释） |
| `MarkerText.jsx` | 将 `__CMD_NNNN__` 等 marker 解析为彩色内联标签。仅用于段落预览等含 marker 的展示场景。**结果卡片中**的 `change.original`/`change.corrected` 等字段因后端已还原 marker，直接以文本渲染，无需 `MarkerText` 包裹 |
| `ParagraphContext.jsx` | 展开显示当前段落前后上下文 |
| `ProjectExplorer.jsx` | 递归文件树：目录展开/折叠，文件选择 |
| `PDFPreview.jsx` | iframe 嵌入 PDF 预览 |
| `PromptEditor.jsx` | 自定义提示词编辑框 |
| `SettingsDialog.jsx` | 完整设置对话框，实时读写 config.json，包括联网搜索和学术研究工具开关 |
| `FolderPickerDialog.jsx` | 文件夹选择对话框，逐级进入子文件夹 |
| `MarkdownRenderer.jsx` | 基于 react-markdown + remark + rehype-katex |
| `ThemeToggle.jsx` | 亮/暗模式切换 |
| `ThemeSwitcher.jsx` | 配色主题切换下拉，自动发现主题文件 |
| `ConfirmDialog.jsx` | 通用确认对话框（destructive 样式 + 禁止遮罩关闭） |
| `ImageOCRPage.jsx` | 通用图片 OCR 页面组件（拖拽/粘贴/识别/复制；`renderResultMeta` 可接收 `(result, setResult)` 第二参数更新结果，FormulaOCR 未判定切换控件依赖此透传） |
| `Wordmark.jsx` | STIX Two Text 排 "LaTeXcellence" |
| `PageHeader.jsx` | 全站共享页头：返回链接 + `groupKey` 自动生成 "§N · 分组" kicker + 页面标题 + 右侧操作位（所有页面共用） |
| `EmptyState.jsx` | 共享空状态：细环图标 + 标题 + 描述 + 可选 CTA（无项目 / 恢复中 / 空结果） |
| `ProofDiff.jsx` | 原文→修改后对照行：原文衬线删除线、改后加粗（`modified` 可传字符串或 ReactNode，术语检查传自定义 Input） |
| `ResultFilterBar.jsx` | 结果过滤工具栏（搜索框 + 类型下拉 + 段落跳转 + 重置） |
| `ProgressBar.jsx` | 检查进度条组件 |
| `ColorField.jsx` | 颜色选择器组件 |
| `BibManagerLibrary.jsx` | 参考文献库管理面板（文件列表、条目 CRUD、重复检测、统计） |
| `FormatResultCard` | 格式识别结果展示卡片（`React.memo` 组件，展示格式名称、置信度、字段列表、LaTeX 样式推荐） |

## 核心 Hook

| Hook | 功能 |
| --- | --- |
| `useParagraphCheck.js` | 封装逐段 LLM 检查公共逻辑（预览、检查/优化、排序、暂停/继续/停止、应用、refresh 持久化）。支持 `resolveInput` 多文件模式。内置乐观锁：读取段落时缓存文件 MD5，应用修改时传回，冲突（409）后自动刷新 MD5 并提示重试。SSE 结果通过 rAF 微批次合并；`done` 事件到达时先排空待 flush 缓冲再收尾，避免一次性推送全部结果的模块（如术语一致性检查）在流结束时丢失结果。 |
| `useEnabledFeatures.js` | 从后端加载功能开关到 Zustand store，幂等。 |
| `useWritingChat.js` | 封装写作助手 Chat 全部逻辑（会话管理、SSE 流式、搜索/学术结果、深度思考解析、生成编辑、应用编辑、助手管理）。内部使用子 hooks。 |
| `useThemeManager.js` | 多主题自发现 hook，自动发现 `theme/` 目录下 CSS 文件。 |
| `useTheme.js` | 暗/亮主题切换，支持 View Transitions API 平滑过渡。 |
| `useUndoRedo.js` | 撤销/重做状态管理，轮询后端端点，支持键盘快捷键。 |
| `useResultFilter.js` | 段落检查结果搜索/过滤/跳转，与 ResultFilterBar 配套使用。 |
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
| `filePath` | string | 必填 | 当前文件路径 |
| `workspaceId` | string | 必填 | 工作区 ID |
| `onUndoRedoComplete` | function | `null` | 撤销/重做完成后的回调 |
| `pollInterval` | number | 1000 | 轮询间隔（ms） |

**返回值**：`canUndo`、`canRedo`、`handleUndo`、`handleRedo`。键盘快捷键：`Ctrl+Z`（撤销）、`Ctrl+Shift+Z` / `Ctrl+Y`（重做），输入框/文本域中不拦截。

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
| 字体 | 学术衬线显示字体 STIX Two Text（字标 / § 编号 / 英文 small-caps 标签）+ 霞鹜文楷正文 + Noto Sans SC 标题 + JetBrains Mono 等宽，配完整中文回退栈 |
| 图标 | lucide-react |
| 类合并 | `cn()`（clsx + tailwind-merge） |

## CSS 文件 `src/index.css` + 主题目录 `src/theme/`

- `index.css`：Tailwind 导入、自定义字体、深色变体定义、主题内联令牌、基础样式。另含设计体系工具：`--font-display`（STIX Two Text）显示字体 token、`.text-small-caps` 英文小大写标签、`.tex-e` / `.tex-x` 字标 "TeX" 排字姿态、`.reveal` 入场动效（错落 + 尊重 `prefers-reduced-motion`）
- `theme/` 下主题 CSS 文件：youminghei（幽冥黑）、jingtailan（景泰蓝）、hupohuang（琥珀黄）、ningyezi（凝夜紫）、tingwulv（庭芜绿）、hailuocheng（海螺橙）、yingtaohong（樱桃红）
- `useThemeManager.js`：自动发现主题文件，管理 `data-theme` 属性
- 配色架构：所有主题共用同一套中性灰阶（背景/卡片/边框/文字，亮暗各一档），主题身份由单一色相承载，统一派生 `--primary` / `--accent` / `--ring` / `--selection-bg`；语义色（`--destructive`、`--brand-mark`）跨主题一致，深色模式下字标统一反白
- 新增主题只需添加 CSS 文件 + 添加 i18n 翻译键，自动发现

## 主题切换机制

亮/暗模式：读取 localStorage → 回退到系统偏好 → `document.startViewTransition()` → 切换 `<html>` 的 `.dark` class。`:root` 层面过渡 CSS 自定义属性，避免逐元素过渡的性能问题。

配色主题：`theme/` 目录 CSS 文件自动发现 → Zustand store 持久化 `currentTheme` → `data-theme` 属性 → CSS 选择器生效。与亮暗模式完全独立。
