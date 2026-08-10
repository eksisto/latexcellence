# showcase —— 展示页

## 功能与用途

`showcase/` 是一个独立的单页产品展示网页，用于对外演示 LateXcellence 的功能与设计。
它**不依赖**后端 FastAPI 与前端 React 构建产物，仅用静态 HTML/CSS/JS +
一个零依赖 Node.js 静态服务器即可运行。

页面采用**滚动叙述式（Scrollytelling）**设计：全屏固定场景 + Parallax 视差，
整页是一份“自己写自己”的 `intro.tex`，随滚动逐章展开。

页面内容：

- **序章（Hero）**：字标 + 标语 + `slogan.tex → slogan.pdf` 编辑器与纸面对照（打字机效果）
- **第一幕 · 摩擦**：编译失败的终端 + 只会“聊天”的助手，引出“排版不该是写作的阻力”
- **第二幕 · 解法**：marker 保护示意 + Agent 执行闭环
- **功能章节（§1 内容导入 → §6 AI 辅助）**：每章一个全屏固定场景，主 mock 上台
  （公式 OCR、模板生成 Agent、文本校对、样式修改、Git 差异、写作助手 Chat），
  其余功能以副卡片呈现
- **设计理念**：会自主干活 / 不搞坏文件 / 可靠可验证（三行横排卡片）
- **技术栈**：后端 / 前端 / 工具链（三行横排卡片）
- **主题**：横向滚动展示多套主题色卡，点击即可切换本页主题（与应用的
  ThemeSwitcher 同一套 token）
- **尾声**：收束语 + 回到顶部

## 架构：内容数据驱动 + 主题单一来源

- `showcase/index.html` 只是静态骨架（头部 / 图标精灵 / 进度条 / 行号栏 / 导轨 /
  工具栏 / 场景容器 / 页脚）。
- 页面内容集中在 `showcase/js/content.js`（导航 `NAV`、场景 `SCENES`、代码块
  `CODE_BLOCKS`、打字机文案 `TYPE_LINES`）；`showcase/js/render.js` 在启动时
  按数据生成导轨、主题弹层选项与全部场景。
- 主题元数据（顺序 / 名称 / 色卡）集中在 `showcase/js/themes.js`；
  `showcase/css/themes.css` 由 `showcase/tools/gen-themes.mjs` 从
  `frontend/src/theme/*.css` 拼接生成，**禁止手改**。
- 滚动引擎与布局的可调常量收口在 `showcase/js/constants.js`。
- 脚本为 ES Module，加载顺序 `render.js → showcase.js → scrolly.js`。

## 设计

- 主题 token 与 `frontend/src/theme/*.css` 逐字一致，明暗双档，由
  `tools/gen-themes.mjs` 生成。
- 字体家族与 `frontend/src/index.css` 一致；字体经 cdnjs 加载，仓库内不保留字体文件
  （LXGW WenKai 用 cdnjs 上官方 `lxgw-wenkai-web` 包的 `result.css`，其余字体在
  `showcase/css/fonts.css` 中直接引用 cdnjs fontsource 包的 woff2）。
- 动效实现：
  - `css/scrolly.css` + `js/scrolly.js`：每个 `[data-pin]` 固定场景的滚动进度写入
    `--p`，CSS 用 `clamp()` 分段编排文本与 mock 的入场/退场；`[data-parallax]`
    元素按视口中心偏移 × 速率写入 `--py`，形成前后景视差。
  - 左侧行号栏（编辑器隐喻）、右侧章节导轨、顶部编译进度条、底部编译状态角标
    （滚动到底部显示 `intro.pdf 已生成`）。
  - 小屏（<1024px）/ 短屏（<620px）/ `prefers-reduced-motion` 自动退化：
    取消固定场景与视差，全部内容立即可见，仅保留普通堆叠布局。
- 功能 mock（公式识别、模板生成、Git 差异、写作助手 Chat 等）与原有展示页一致，
  复刻自前端各功能页；背景网格与 LaTeX 字形碎片构成视差场。

## 运行

```bash
cd showcase
npm start        # 或 node server.js
# 打开 http://127.0.0.1:4173
```

## 维护

- **改文案 / 加场景**：改 `showcase/js/content.js`（`SCENES` / `NAV`），
  无需碰 HTML；场景高度在数据里用 `len` 控制（`--len × 100vh`）。
- **改主题**：改 `frontend/src/theme/*.css` 后运行 `npm run themes:gen`
  重新生成 `showcase/css/themes.css`；若主题列表 / 名称 / 色卡变化，同步改
  `showcase/js/themes.js` 的 `THEMES`。提交前运行 `npm run themes:check`
  校验无漂移（漂移时退出码 1，可在 CI / pre-commit 使用）。
- **代码块 / 文件树 / 终端内容**：空白敏感文本放在 `showcase/js/content.js` 的
  `CODE_BLOCKS`，场景数据里用 `data-code` 引用（HTML 只留占位，避免缩进被
  `white-space: pre` 原样显示）。
- **公式图片示例（KaTeX）**：KaTeX 的 CSS/JS 经 cdnjs 按需加载（版本 `0.17.0`，
  见 `index.html`），LaTeX 源文本写在 `showcase/js/content.js` 的场景数据
  `data-katex` 字段，`showcase/js/showcase.js` 启动时调用 `katex.render()` 注入；
  离线或 CDN 不可用时兜底显示源文本。
- **动效参数**：集中在 `showcase/js/constants.js`（滚轮灵敏度、行号栏行数、
  锚点比例、布局断点、打字机节奏等），调参只改这一处。
- **生成部署产物**：运行 `npm run deploy`（`showcase/tools/deploy.mjs`），
  把网站资源拷贝到 `showcase/public/` 并压缩 HTML / CSS / JS（零依赖，
  仅用 Node 内置模块），产物可直接部署到任意静态服务器；`public/` 已加入
  `.gitignore`，不入库。
