# AGENTS.md — LaTeXcellence Showcase（展示页）

> 本文件约束 AI 助手在 `showcase/` 子项目中执行**维护任务**时的行为规范。
> 它不替代父仓库 `latexcellence/AGENTS.md`（后端/前端全局规范），两者叠加生效。

## 每次开始新对话

1. 先读 `PROJECT_SUMMARY.md`，了解项目定位、架构与目录职责。
2. 再读 `README.md` 的「维护手册」，确认改动的正确入口。
3. 涉及主题时，可能需要参考父仓库 `frontend/src/theme/*.css` 与 `docs/showcase.md`。

## 项目定位

- 零依赖单页产品展示页（Scrollytelling），Node.js 18+ 内置 http 静态服务器，**无构建步骤、无 npm 运行时依赖**。
- 是 `latexcellence` 仓库的子目录 `showcase/`；主题 token 依赖父仓库 `frontend/` 的样式源。

## 架构铁律（最高优先级）

1. **内容单一来源**：页面文案、场景结构、代码块内容只写在 `js/content.js`（`NAV` / `SCENES` / `CODE_BLOCKS` / `TYPE_LINES`）。**禁止**把内容直接写回 `index.html`。
2. **index.html 是静态骨架**：只保留头部、图标精灵、进度条/行号栏/导轨/工具栏容器、页脚。场景 DOM 由 `js/render.js` 按数据生成。
3. **主题单一来源**：
   - `css/themes.css` 由 `tools/gen-themes.mjs` 从 `frontend/src/theme/*.css` 拼接生成，**禁止手改**。
   - 主题顺序 / 名称 / 色卡元数据在 `js/themes.js` 的 `THEMES`。
   - 改主题流程：改 `frontend` 源 → `npm run themes:gen` 重新生成 → 若列表/名称变化同步 `js/themes.js` → `npm run themes:check` 确认无漂移。
4. **常量收口**：滚动 / 布局 / 动效参数集中在 `js/constants.js`，禁止在 `scrolly.js` 等文件里散落魔法数字。
5. **场景数据模型**（`js/content.js` 的 `SCENES`，`tpl` 决定渲染模板）：
   - 模板：`hero` / `act` / `chapter` / `principles` / `stack` / `themes` / `outro`。
   - `len` 控制场景高度（`--len × 100vh`），渲染时同时写入 `data-len` 与内联 `--len`。
   - 代码块内容放 `CODE_BLOCKS`，场景内用 `{ type: "code" | "file-tree" | "terminal", ref }` 引用；**未被引用的 key 请删除**。
   - 文本字段默认会被转义；需要输出 HTML 的字段（如 `desc` 的数组分行、`meta`、`diff` 的 `html`）需明确标注，避免双重转义。
6. **脚本为 ES Module**，加载顺序 `render.js → showcase.js → scrolly.js`（`index.html` 底部已按序引用），不要把模块改回经典脚本或调整顺序。

## 修改规范

- **代码风格**：与现有文件一致——Prettier 默认、`const/let`、ES Module、中文注释。
- **禁止事项**：
  - 新增运行时 npm 依赖或引入构建步骤（保持零依赖）。
  - 手改 `css/themes.css`。
  - 在 `index.html` 中直接写场景内容。
  - 修改场景/主题列表却不同步 `js/themes.js`、`PROJECT_SUMMARY.md`。
  - 用 `console.log` 之类调试输出替代验证。
- **修改后必须验证**：
  - `node --check` 检查改动的 JS（`.mjs` 可直接检查，`.js` 可临时复制为 `.mjs`）。
  - 涉及主题时运行 `npm run themes:check`。
  - 本地 `node server.js` 打开页面：浏览器控制台无报错；场景内容、布局、交互（主题切换 / 明暗切换 / 导轨高亮 / 编译角标 / 公式图片 / 代码注入）与改动前一致。
- **提交信息**：沿用父仓库规范 `<type>: <中文描述>`（如 `feat` / `fix` / `refactor` / `docs`）。

## 文档同步

- **结构变更**（新增/删除文件、模块、改动架构）必须同步更新 `PROJECT_SUMMARY.md`。
- **功能或维护方式变更**必须同步更新 `README.md` 的「维护手册」。
- 涉及父仓库文档时，同步 `latexcellence/docs/showcase.md`。
- **禁止在文档中硬编码数量**（如“X 个场景”“Y 个文件”“Z 个主题”），用模糊描述或列出目录/类型，避免随结构变化过时。
