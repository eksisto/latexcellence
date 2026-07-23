# LateXcellence — 前端开发文档

## 入口与路由

**`main.jsx`** → 将 `<App />` 渲染到 `#root`。

**`App.jsx`** → 使用 React Router 定义路由：

| 路径              | 组件              | 状态   |
| ----------------- | ----------------- | ------ |
| `/`               | WelcomePage       | ✅ 完成 |
| `/workspace`      | WorkspacePage     | ✅ 完成 |
| `/typo-check`     | TypoCheckPage     | ✅ 完成 |
| `/expression-opt` | ExpressionOptPage | ✅ 完成 |
| `/terminology-check` | TerminologyCheckPage | ✅ 完成 |
| `/formula-ocr`    | FormulaOCRPage    | ✅ 完成 |
| `/table-ocr`      | TableOCRPage      | ✅ 完成 |
| `/template-gen`   | TemplateGenPage   | ✅ 完成 |
| `/formatting`     | FormattingPage    | ✅ 完成 |
| `/style-modify`   | StyleModifyPage   | ✅ 完成 |
| `/markdown-convert` | MarkdownConvertPage | ✅ 完成 |
| `/git-diff`       | GitDiffPage       | ✅ 完成 |
| `/writing-chat`   | WritingChatPage   | ✅ 完成 |
| `/bib-manager`    | BibManagerPage    | ✅ 完成 |

全局 header 包含 **ThemeSwitcher**（调色板图标，切换配色主题）、**ThemeToggle**（太阳/月亮图标，切换亮暗模式）和 **SettingsDialog**（齿轮图标）。

## 状态管理 `useStore.js`

使用 Zustand 5 + `persist` 中间件管理全局状态，持久化键为 `"latexcellence-project"`。
setter 支持 function updater 模式（`typeof value === "function"` 检测），兼容 `set(prev => ...)` 写法。

持久化水合是异步的。store 通过 `onRehydrateStorage` 回调设置 `hydrated: true`，组件可订阅该状态以确保在水合完成后再读取持久化值（如 `writingChatCurrentSessionId`）。

```javascript
{
  // 水合状态（不持久化）
  hydrated: false,                // persist 水合是否完成

  // 功能开关（从 config 读取）
  enabledFeatures: null,         // { workspace, typo_check, expression_opt, formula_ocr, table_ocr, template_gen, formatting, compile, git_diff, markdown_converter, style_modify, terminology_check, writing_chat, bib_manager }

  // 系统状态
  currentTheme: "youminghei",    // 当前配色主题 ID（持久化）

  // 工作区标识（持久化）
  workspaceId: "<UUID>",         // 当前浏览器的工作区唯一 ID

  // 项目状态
  projectPath: null,             // 当前打开的项目路径（持久化）
  rootPath: null,                // 项目根目录（持久化）
  mainFile: null,                // 主文档路径（持久化）
  fileTree: [],                  // 文件树节点
  activeFile: null,              // 当前选中的 .tex 文件（持久化）

  // 写作助手 Chat（仅 session_id 持久化，消息由后端磁盘保存）
  writingChatSessions: [],            // 会话列表（非持久化）
  writingChatCurrentSessionId: null,  // 当前会话 ID（持久化）
  writingChatMessages: [],            // 当前会话消息（非持久化，加载自后端）
  writingChatCompressed: false,       // 是否已压缩（非持久化）

  // 错字检查
  typoParagraphs: [],            // 预览段落（持久化）
  typoResults: [],               // LLM 检查结果（持久化）

  // 表达优化
  expressionParagraphs: [],      // 预览段落（持久化）
  expressionResults: [],         // LLM 优化结果（持久化）

  // 术语一致性检查
  terminologyParagraphs: [],     // 预览段落（持久化）
  terminologyResults: [],        // LLM 检查结果（持久化）

  // 自动排版
  formattingParagraphs: [],     // 预览段落（持久化）
  formattingResults: [],        // LLM 格式化结果（持久化）

  // 自动编译
  compileResult: null,          // 编译结果（不持久化）
  compileLoading: false,        // 编译加载状态（不持久化）

  // 其他
  pdfUrl: null,                  // PDF 预览地址
  checkProgress: null,           // 逐段检查进度 { current, total, file_path }
  loadingStates: {               // 各模块加载状态
    typoCheck: false,
    expressionOpt: false,
    formatting: false,
    terminologyCheck: false,
    writingChat: false,
    bibManager: false,
  },

  // 操作
  clearProject: () => {},        // 清空项目状态 + 移除 localStorage
  setEnabledFeatures: (features) => {},
  setCurrentTheme: (theme) => {},
  setWorkspaceId: (id) => {},
  setProjectPath: (path) => {},
  setRootPath: (path) => {},
  setMainFile: (file) => {},
  setFileTree: (tree) => {},
  setActiveFile: (file) => {},
  setTypoParagraphs: (paras) => {},     // 支持 function updater
  setTypoResults: (results) => {},      // 支持 function updater
  setExpressionParagraphs: (paras) => {},
  setExpressionResults: (results) => {},
  setTerminologyParagraphs: (paras) => {},  // 支持 function updater
  setTerminologyResults: (results) => {},   // 支持 function updater
  setFormattingParagraphs: (paras) => {},
  setFormattingResults: (results) => {},
  setLoading: (key, value) => {},
  setCompileResult: (result) => {},
  setCompileLoading: (loading) => {},
  setPdfUrl: (url) => {},
  setCheckProgress: (progress) => {},
  setWritingChatSessions: (sessions) => {},  // 支持 function updater
  setWritingChatCurrentSessionId: (id) => {},
  setWritingChatMessages: (msgs) => {},    // 支持 function updater
  setWritingChatCompressed: (val) => {},
}
```

## API 层 `api/index.js`

所有请求通过 `request(url, options)` 函数，自动处理：
- 添加 `Content-Type: application/json` 头
- `BASE_URL = "/api"` 前缀
- 非 OK 响应 → 提取 `detail` 字段 → `translateError()` 翻译为中文 → 抛出 Error

FormData 请求（文件上传）通过 `requestForm(url, formData, method)` 函数处理：
- 不设置 `Content-Type`（由浏览器自动设置 multipart boundary）
- 同样经过 `translateError()` 翻译错误消息

需要工作区隔离的端点通过 `appendWs(url, workspaceId)` 拼接 `workspace_id` 查询参数。

按功能分组暴露：

```javascript
api.health()
api.configStatus()
api.config.get() / .update(data)
api.project.open(path, workspaceId) / .browse(path) / .fileTree(workspaceId) / .paragraphs(file, workspaceId) / .applyChange(filePath, paragraphIndex, newText, workspaceId) / .undo(file, workspaceId) / .redo(file, workspaceId) / .canUndo(file, workspaceId) / .canRedo(file, workspaceId) / .setMainFile(file, workspaceId) / .closeWorkspace(workspaceId)
api.typoCheck.preview(file, workspaceId) / .check(data, workspaceId) / .apply(data, workspaceId) / .stream(workspaceId, file)
api.expressionOpt.preview(file, workspaceId) / .optimize(data, workspaceId) / .apply(data, workspaceId) / .stream(workspaceId, file) / .prompts.*
api.terminologyCheck.preview(file, workspaceId) / .check(data, workspaceId) / .apply(data, workspaceId) / .stream(workspaceId, file)
api.formulaOCR.recognize(formData)  // FormData 上传，不走 JSON
api.tableOCR.recognize(formData)   // FormData 上传，不走 JSON
api.templateGen.generate(data) / .save(data)
api.formatting.preview(file, workspaceId) / .check(data, workspaceId) / .apply(data, workspaceId) / .stream(workspaceId, file)
api.compile.run(mainFile, workspaceId) / .pdfUrl(mainFile, workspaceId)
api.gitDiff.refs(workspaceId) / .generate(data, workspaceId)
api.styleModify.modify(data)
api.markdownConverter.convert(data)
api.bibManager.analyzeFormat(data) / .extract(formData) / .generate(data) / .save(data, workspaceId) / .listBibFiles(workspaceId) / .readBib(file, workspaceId)
api.writingChat.send({...data, workspaceId}) / .generateEdit(data, workspaceId) / .applyEdit(data, workspaceId) / .deleteMessage(data) / .undoMessage(data) / .undoCompression(id)
api.writingChat.listSessions(workspaceId) / .loadSession(id) / .deleteSession(id) / .renameSession(id, title)
api.writingChat.assistants.list() / .load(name) / .save(name, content) / .delete(name) / .setActive(name)
```

## 工具模块 `utils/errorMessages.js`

后端英文错误消息 → 当前语言翻译模块，通过 `i18n.t()` 实现多语言支持。

**设计**：
- `EXACT_MAP`：静态精确匹配，值为 i18n 键（如 `"No API key configured"` → `"noApiKeyConfigured"`）
- `PATTERN_MAP`：正则模式匹配，支持捕获组参数提取（如 `/^File not found:\s*(.+)/` → `"fileNotFound"` + `{ path: m[1] }`）
- `translateError(msg)` 函数：先精确匹配，再模式匹配，均无匹配则原样返回
- 翻译资源位于 `errors` 命名空间（`zh.json` / `en.json`），切换语言后错误消息自动跟随切换

**调用点**：
- `request()` — 通用 JSON 请求的错误处理
- `requestForm()` — FormData 请求的错误处理
- `useParagraphCheck.js` — SSE 流式错误事件翻译
- `useWritingChat.js` — SSE 流式错误事件翻译

## 国际化 `i18n/`

基于 `i18next` + `react-i18next` + `i18next-browser-languagedetector` 实现中/英双语。

**初始化**：`src/i18n/index.js` 在 `main.jsx` 中最先导入，确保组件树挂载前 i18next 已就绪。

**翻译资源**：按命名空间拆分为 5 个 JSON 文件（`zh.json` / `en.json` 内各含 5 个 key 层级）：

| 命名空间 | 覆盖范围 | 示例 key |
|----------|---------|----------|
| `common` | 跨页面复用的通用文案 | `loading`, `save`, `cancel`, `applyAll` |
| `pages` | 各页面专属文案 | `typoCheck.checkButton`, `workspace.compileBtn` |
| `components` | 各组件专属文案 | `settings.apiKey`, `markerText.math`, `themeNames.youminghei` |
| `hooks` | 各 hook 的默认消息 | `paragraphCheck.defaultCheckLoading` |
| `errors` | 后端错误消息翻译 | `noProjectOpened`, `requestTimedOut`, `compilationTimeout` |

**组件使用方式**：
- React 组件：`const { t } = useTranslation("namespace")` → `t("key")`
- Hook / 非 React 上下文：`import i18n from "@/i18n"` → `i18n.t("namespace:key")`

**语言切换**：`SettingsDialog.jsx` 提供语言下拉（中文 / English），调用 `i18n.changeLanguage(v)` 实时切换，持久化到 `localStorage` 键 `i18nLng`。

**Fallback 策略**：`fallbackLng: "zh"`，缺失 key 原样返回英文 key 路径。

**设计约束**：
- `useParagraphCheck` 的默认 `messages` 参数使用 getter 属性，确保语言切换后默认文案实时更新
- `MarkerText` 的 `MARKER_LABELS` 在组件内部构建（需 `t()` 函数），体积极小，无性能影响
- `errorMessages.js` 通过 `i18n.t()` 翻译后端英文错误消息，与 UI 文案统一使用同一套翻译资源

## 核心组件

| 组件                   | 功能                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------ |
| `DiffViewer.jsx`       | 显示 LLM 检测到的修改：type 标签、原文（删除线）、修正文（高亮）、解释               |
| `MarkerText.jsx`       | Marker 渲染组件：将 `__CMD_NNNN__` 等标记解析为彩色内联标签（蓝/紫/橙/灰）           |
| `ParagraphContext.jsx` | 段落上下文组件：展开 `<details>` 显示当前段落前后上下文，使用 MarkerText 渲染        |
| `ProjectExplorer.jsx`  | 递归文件树：目录展开/折叠，`.tex` 文件高亮，点击选择                                 |
| `PDFPreview.jsx`       | 通过 `<iframe>` 嵌入 PDF                                                             |
| `PromptEditor.jsx`     | 自定义提示词编辑框（Card 包裹的 textarea + Save 按钮）                               |
| `SettingsDialog.jsx`   | 完整设置对话框：API Key、Base URL、Model、Dev Mode、各功能开关，实时读写 config.json |
| `FolderPickerDialog.jsx` | 文件夹选择对话框：通过后端 /api/project/browse 浏览本地目录，逐级进入子文件夹 |
| `MarkdownRenderer.jsx` | Markdown 渲染组件：react-markdown + remark-gfm + remark-math + rehype-katex，支持 GitHub 风格 Markdown + KaTeX 数学公式 |
| `ThemeToggle.jsx`      | 亮/暗模式切换，带 tooltip                                                            |
| `ThemeSwitcher.jsx`    | 配色主题切换下拉，自动发现 `theme/` 目录下所有 CSS 主题文件，显示名通过 i18n 翻译 |
| `ConfirmDialog.jsx`    | 通用确认对话框：基于 shadcn/ui Dialog，支持 destructive 样式 + 禁止遮罩关闭           |
| `ImageOCRPage.jsx`     | 通用图片 OCR 页面组件：拖拽上传、Ctrl+V 粘贴、识别、复制，FormulaOCR/TableOCR 共享    |
| `ResultFilterBar.jsx`  | 结果过滤工具栏：搜索框 + 类型下拉 + 段落跳转 + 重置按钮                              |
| `ProgressBar.jsx`      | 检查进度条组件：显示当前处理进度                                                      |

## 暗色模式与多主题

`index.html` 内联脚本在 React 加载前读取 `localStorage` 设置 `dark` class，防止闪烁。
`useTheme.js` hook 管理亮/暗模式切换（Sun/Moon 图标）。`.dark` class 挂载在 `<html>`，配合 View Transitions API 平滑过渡。

**多主题配色** 通过 `theme/` 目录下的 CSS 文件实现，与亮/暗模式完全独立：

- `theme/youminghei.css` — 幽冥黑配色（黑白极简基调）
- `theme/jingtailan.css` — 景泰蓝配色（蓝调优雅）
- `theme/hupohuang.css` — 琥珀黄配色（暖金基调）
- `theme/ningyezi.css` — 凝夜紫配色（深紫调）
- `theme/tingwulv.css` — 庭芜绿配色（自然绿调）
- `theme/hailuocheng.css` — 海螺橙配色（暖橙调）
- `theme/yingtaohong.css` — 樱桃红配色（亮红调）
- 颜色变量通过 `[data-theme="xxx"]:root`（浅色）和 `[data-theme="xxx"].dark`（深色）选择器作用
- `main.jsx` 使用 `import.meta.glob("./theme/*.css", { eager: true })` 自动发现并加载所有主题 CSS 文件
- `theme/useThemeManager.js` 导出 `getAvailableThemes()` 和 `useThemeManager()` hook，管理 `data-theme` 属性与 Zustand store 中的 `currentTheme` 状态
- `ThemeSwitcher.jsx` 提供调色板图标下拉菜单，用户可选择切换配色
- 当前主题持久化在 Zustand store 的 `partialize` 中（键 `currentTheme`）

**主题切换过渡优化**：

切回亮/暗色时，若用 `*, *::before, *::after { transition: background-color 300ms }` 让每个元素独立过渡，浏览器需为 DOM 树上所有元素分别计算和启动过渡——深层元素（如 button）启动时机天然晚于浅层元素（如 body），导致视觉上深层组件"慢半拍"。

解决方案分为两层：

1. **`:root` 层面过渡 CSS 自定义属性**（`index.css`）：将 `transition-property` 从逐元素的 `background-color, color` 提升到 `:root` 上所有颜色相关的 CSS 自定义属性（`--background`, `--foreground`, `--primary` 等 ~30 个）。切换 `.dark` class 时，这些自定义属性在 `:root` 上以 300ms 平滑插值，所有子元素通过 `var()` 继承插值后的值，不需要各自的 transition。

2. **主题切换期间抑制逐元素 transition**（`useTheme.js` + `index.css`）：`applyTheme()` 在切换 class 前给 `document.documentElement` 设 `data-theme-transitioning` 属性，CSS 规则 `[data-theme-transitioning] * { transition-duration: 0s !important }` 将逐元素过渡时长清零。350ms 后 JS 移除该属性，逐元素过渡（如 button hover）恢复正常。

这样主题切换时仅运行 **1 条**（`:root` 的自定义属性）而非 **数百条** 过渡，所有元素严格同步。

## 核心 Hook

| Hook                   | 功能                                                                                                                                                                        |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `useParagraphCheck.js` | 封装逐段 LLM 检查功能的公共逻辑（项目恢复、文件选择、预览、检查/优化、排序、暂停/继续/停止、应用、refresh 持久化）。支持 `resolveInput` 多文件模式（展开 `\input`）。供 `TypoCheckPage`、`ExpressionOptPage`、`FormattingPage` 和 `TerminologyCheckPage` 共享调用。SSE 结果采用 `requestAnimationFrame` 微批次合并：同一帧内到达的多条结果合并为一次 `setResults`，全量排序只执行一次，将大文档场景下的排序开销从 O(n² log n) 降至 O(n log n)。 |
| `useEnabledFeatures.js` | 从后端 `/api/config/status` 加载功能开关（`enabledFeatures`）到 Zustand store。幂等：已加载则跳过请求。供 `WelcomePage` 和 `WorkspacePage` 共享调用，确保直接刷新任一页面时功能开关均可用。 |
| `useWritingChat.js`    | 封装写作助手 Chat 全部逻辑：会话管理（CRUD + 重命名）、SSE 流式发送、深度思考解析（`parseThinking` + `processLoadedMessages`）、生成编辑、应用编辑、助手提示词管理。页面刷新时通过 store 的 `hydrated` 状态感知水合完成，再恢复上次会话。内部使用子 hooks 结构：`writing-chat/sessions.js`（会话管理）、`writing-chat/assistants.js`（助手管理）、`writing-chat/captcha.js`（验证码处理）、`writing-chat/sse.js`（SSE 事件处理）、`writing-chat/utils.js`（工具函数）。供 `WritingChatPage` 调用。 |
| `useFavicon.js`        | 动态 favicon 切换：通过 `MutationObserver` 监听 `document.documentElement` 的 `class` 变化，在亮/暗模式间切换 `/favicon.svg` 和 `/favicon-dark.svg`。                       |
| `useThemeManager.js`   | 多主题自发现 hook：通过 `import.meta.glob` 自动发现 `theme/` 目录下 CSS 主题文件，管理 `data-theme` 属性，提供 `getAvailableThemes()` 和 `useThemeManager()`。主题显示名通过 i18n 翻译。位于 `src/theme/` 目录。 |
| `useTheme.js`          | 暗/亮主题切换 hook：读取 localStorage + 系统偏好，使用 View Transitions API 平滑切换 `<html>` 上的 `.dark` class。 |
| `useUndoRedo.js`       | 撤销/重做状态管理 hook：轮询 `/api/project/can-undo`、`/can-redo` 端点维护 `canUndo`/`canRedo` 状态；暴露 `handleUndo`/`handleRedo`，操作后自动刷新状态并回调通知调用方重新加载段落。 |
| `useResultFilter.js`   | 段落检查结果搜索/过滤/跳转 hook：提供 `searchQuery`、`typeFilter`、`jumpToParagraph`、`availableTypes`、`reset`、`hasFilters`。与 `ResultFilterBar` 组件配套使用。 |

**`useParagraphCheck` 配置接口**：

```javascript
useParagraphCheck({
  apiModule,          // 必填：API 模块（api.typoCheck / api.expressionOpt / api.formatting / api.terminologyCheck）
  checkMethodName,    // 默认 "check"：调用 apiModule 的方法名
  correctedTextField, // 默认 "corrected"：结果中修正文本的字段名（expressionOpt 用 "optimized"）
  storeSlice,         // Zustand store 切片键（paragraphs, results, loading）
  messages,           // 自定义提示文案（加载中/成功等）
})
```

**返回值**：

```javascript
{
  projectPath,        // 当前项目路径
  activeFile,         // 当前选中文件
  workspaceId,        // 工作区唯一标识符
  texFiles,           // 文件树中所有 .tex 文件的扁平列表
  paragraphs,         // 预览段落
  results,            // LLM 检查/优化结果
  isLoading,          // 加载状态
  progress,           // 流式进度 { current, total, file_path }
  restoring,          // 项目恢复状态
  applyingAll,        // 批量应用状态
  isPaused,           // 流式暂停状态
  error,              // 错误信息
  message,            // 提示信息
  resolveInput,       // 是否展开 \input（boolean）
  setResolveInput,    // 设置展开 \input 开关
  activeFiles,        // 当前生效文件列表（resolveInput 时为所有解析到的文件）
  operationId,        // 当前检查操作的 ID（ref 对象）
  handlePreview,      // 加载预览
  handleCheck,        // 执行检查/优化（SSE 结果通过 rAF 微批次合并，自动排序：有修改的在前，同段落按 index 升序）
  handlePauseResume,  // 暂停/继续流式检查
  handleStop,         // 停止流式检查
  handleApplyChange,  // 应用单段修改 (idx, newText, filePath?)
  handleApplyAll,     // 批量应用所有有修改的段落
  handleIgnoreChange, // 忽略单段修改并从结果列表移除
  handleFileChange,   // 切换文件并清空结果/段落
}
```

### `useUndoRedo.js` — 撤销/重做状态管理

**功能**：轮询 `/api/project/can-undo`、`/can-redo` 端点维护 `canUndo`/`canRedo` 状态；暴露 `handleUndo`/`handleRedo`，操作后自动刷新状态并回调通知调用方重新加载段落。

**配置接口**：

```javascript
useUndoRedo({
  filePath,              // 必填：当前文件路径
  workspaceId,           // 必填：工作区 ID
  onUndoRedoComplete,    // 可选：撤销/重做完成后的回调（用于触发段落重新加载）
  pollInterval,          // 可选：轮询间隔，默认 1000ms
})
```

**返回值**：

```javascript
{
  canUndo,               // boolean：是否可撤销
  canRedo,               // boolean：是否可重做
  handleUndo,            // () => Promise<void>：执行撤销
  handleRedo,            // () => Promise<void>：执行重做
}
```

**键盘快捷键**：`WorkspacePage.jsx` 中处理 `Ctrl+Z`（撤销）、`Ctrl+Shift+Z` / `Ctrl+Y`（重做）；输入框/文本域中不拦截，保留原生 undo。

---

### `useResultFilter.js` — 段落检查结果搜索/过滤/跳转

**功能**：提供文本搜索、类型过滤、段落跳转能力。

**返回值**：

```javascript
{
  searchQuery,           // string：搜索关键词
  setSearchQuery,        // (v: string) => void
  typeFilter,            // string：当前类型过滤值（"all" 或具体类型）
  setTypeFilter,         // (v: string) => void
  availableTypes,        // string[]：从 results 自动提取去重的类型列表（含 "all"）
  filteredResults,       // 过滤后的结果数组
  jumpToParagraph,       // (idx: number) => void：scrollIntoView 跳转到指定段落
  reset,                 // () => void：重置所有过滤器
  hasFilters,            // boolean：是否有活跃过滤器
}
```

**配套组件**：`ResultFilterBar.jsx` — 搜索框 + 类型下拉 + 段落跳转输入 + 重置按钮，与 `useResultFilter` 配套使用。

---

## 样式架构

| 方面           | 选择                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------ |
| **CSS 框架**   | Tailwind CSS v4（通过 `@tailwindcss/vite` 插件，无需 PostCSS 配置）                        |
| **CSS 文件**   | `src/index.css` + `src/theme/*.css` 主题文件                                                |
| **预处理器**   | 无（无 SCSS/LESS）                                                                         |
| **CSS-in-JS**  | 无                                                                                         |
| **组件库**     | shadcn/ui（`style: "base-nova"`）                                                          |
| **类合并工具** | `cn()` 在 `src/lib/utils.js`（clsx + tailwind-merge）                                      |
| **组件变体**   | `cva`（class-variance-authority）                                                          |
| **主题系统**   | CSS 自定义属性（`oklch` 色彩空间），配色主题文件在 `theme/` 目录，通过 `data-theme` 属性选择；亮/暗通过 `.dark` class 独立控制 |
| **深色模式**   | `.dark` class 挂载在 `<html>`，通过 Zustand + localStorage 切换，配合 View Transitions API |
| **多主题**     | `theme/` 目录下 CSS 文件自动发现，每个文件定义完整配色（`[data-theme="xxx"]:root` + `[data-theme="xxx"].dark`），与亮暗模式无耦合 |
| **字体**       | 2 个 bundled 中文字体 + 完整中文回退栈                                                     |
| **动画**       | 通过 `tw-animate-css` 导入 + 300ms CSS transitions                                         |
| **图标**       | lucide-react                                                                               |
| **状态管理**   | Zustand（with persist middleware）                                                         |

## CSS 文件：`src/index.css` + 主题目录 `src/theme/`

`src/index.css` 包含所有结构样式；配色 CSS 自定义属性按主题拆分至 `src/theme/*.css`。

### 1. Tailwind v4 导入

```css
@import "tailwindcss";
@import "tw-animate-css";
@import "shadcn/tailwind.css";
```

### 2. 自定义字体声明

- `LXGW WenKai GB` — 正文字体（`--font-sans`）
- `Source Han Sans SC` — 标题字体（`--font-heading`）

### 3. 深色变体定义

```css
@custom-variant dark (&:is(.dark *));
```

### 4. 主题内联

使用 `@theme inline` 语法定义：

- **字体系列**：`--font-heading`、`--font-sans`
- **颜色令牌**：`--color-sidebar-*`、`--color-chart-*`、`--color-ring`、`--color-background` 等
- **圆角令牌**：`--radius-sm` 到 `--radius-4xl`

### 5. 浅色/深色基础样式

`:root` 和 `.dark` 仅保留 `color-scheme`、`scrollbar-color` 和 `--radius` 结构变量。所有颜色 CSS 自定义属性移至 `theme/` 目录下的主题文件。

### 6. 主题目录 `src/theme/`

| 文件                      | 功能                                                                 |
| ------------------------- | -------------------------------------------------------------------- |
| `youminghei.css`          | 幽冥黑配色：黑白极简基调                                              |
| `jingtailan.css`          | 景泰蓝配色：蓝调优雅                                                 |
| `hupohuang.css`           | 琥珀黄配色：暖金基调                                                 |
| `ningyezi.css`            | 凝夜紫配色：深紫调                                                   |
| `tingwulv.css`            | 庭芜绿配色：自然绿调                                                 |
| `hailuocheng.css`         | 海螺橙配色：暖橙调                                                   |
| `yingtaohong.css`         | 樱桃红配色：亮红调                                                   |
| `useThemeManager.js`      | 通过 `import.meta.glob` 自动发现主题文件，`getAvailableThemes()` 返回有序 ID 列表，`useThemeManager()` hook 管理 `data-theme` 属性 |


新增主题只需在 `theme/` 目录下添加 `<name>.css` 文件，按 `[data-theme="<name>"]:root` / `[data-theme="<name>"].dark` 格式定义颜色变量，并在 `zh.json` / `en.json` 的 `components.themeNames` 中添加对应翻译键。`ThemeSwitcher` 组件会自动发现并显示。

### 7. Base Layer

- 全局 `border-border`
- body `bg-background text-foreground`
- h1-h6 使用 heading 字体
- 300ms 平滑颜色过渡

### 8. View Transitions

支持 `document.startViewTransition()` 实现主题切换的平滑过渡。

## shadcn/ui 配置（`components.json`）

- **style**: `"base-nova"`
- **css 变量**: 启用
- **基础色**: `"neutral"`
- **别名**: `@/components/ui`、`@/lib/utils`、`@/hooks`

UI 组件使用 `@base-ui/react`（button）和 `@radix-ui/react-*`（dialog、select、tabs 等）作为无头组件。

## cn() 工具（`src/lib/utils.js`）

```js
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
```

用于智能合并 Tailwind 类名，标准 shadcn/ui 模式。

## 主题切换机制

### 亮/暗模式（`src/hooks/useTheme.js`）

1. 读取 `localStorage` 键 `"latexcellence-theme"`
2. 回退到 `prefers-color-scheme` 媒体查询
3. 使用 `document.startViewTransition()` 实现平滑过渡
4. 切换 `<html>` 上的 `.dark` class
5. `index.html` 内联脚本在 React 挂载前读取 localStorage，防止闪白

### 配色主题（`src/theme/useThemeManager.js`）

1. 通过 `import.meta.glob("./*.css")` 自动发现 `theme/` 目录下所有主题文件
2. 主题 ID 从文件名提取（如 `youminghei.css` → `youminghei`），显示名通过 `i18n.t('themeNames.${id}')` 从翻译资源获取
3. Zustand store 中 `currentTheme` 状态持久化到 localStorage
4. `useThemeManager()` hook 设置 `<html data-theme="...">` 属性
5. CSS 选择器 `[data-theme="youminghei"]:root` / `[data-theme="youminghei"].dark` 根据属性生效对应颜色变量
6. `ThemeSwitcher` 组件提供下拉菜单，列出所有可用主题的多语言名称，点击即可切换
7. 新增主题只需在 `theme/` 下添加 `<id>.css` 文件，并在 `zh.json` / `en.json` 的 `components.themeNames` 中添加对应翻译键

亮/暗模式（Sun/Moon 图标）与配色主题（Palette 图标）完全独立，互不干扰。

## 样式相关依赖

| 依赖                       | 版本    | 用途                  |
| -------------------------- | ------- | --------------------- |
| `tailwindcss`              | ^4.1.0  | Tailwind CSS v4 框架  |
| `@tailwindcss/vite`        | ^4.1.0  | Tailwind v4 Vite 插件 |
| `tw-animate-css`           | ^1.4.0  | 动画工具类            |
| `clsx`                     | ^2.1.1  | 条件类名字符串构建    |
| `tailwind-merge`           | ^3.6.0  | Tailwind 类名智能合并 |
| `class-variance-authority` | ^0.7.1  | 组件变体定义          |
| `lucide-react`             | ^1.23.0 | 图标库                |
| `@radix-ui/react-*`        | ~1.x    | 无头 UI 组件原语      |
| `@base-ui/react`           | ^1.6.0  | Button 原语           |
