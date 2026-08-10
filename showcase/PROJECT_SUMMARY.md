# LateXcellence Showcase — Project Summary

> 项目概要文件，供 AI 助手快速了解本项目。**项目结构变更（新增/删除文件、模块，或调整架构）时必须同步更新本文件。**

## 是什么

- `showcase/` 是 `latexcellence` 仓库内的**独立单页产品展示页**，用于对外演示 LateXcellence 的功能与设计。
- **零依赖**：纯静态 HTML/CSS/JS + Node.js 内置 `http` 静态服务器，无构建步骤、无 npm 运行时依赖（Node.js 18+）。
- 采用**滚动叙述式（Scrollytelling）**设计：全屏固定场景 + Parallax 视差，整页隐喻为一份“自己写自己”的 `intro.tex`。

## 快速开始

```bash
cd showcase
npm start        # 或 node server.js
# 打开 http://127.0.0.1:4173 （可用环境变量 PORT / HOST 覆盖）
```

## 目录结构

```
showcase/
├── server.js          # 零依赖静态文件服务器（Node.js 内置 http）
├── package.json       # scripts: start / dev / themes:gen / themes:check
├── index.html         # 静态骨架：头部、图标精灵、进度条/行号栏/导轨/工具栏容器、页脚
├── AGENTS.md          # AI 维护规范（本子项目）
├── PROJECT_SUMMARY.md # ← 本文件
├── README.md          # 项目介绍 + 维护手册
├── css/
│   ├── fonts.css      # cdnjs 网络字体（STIX Two Text / Noto Sans SC / JetBrains Mono）
│   ├── themes.css     # 主题 token —— 由 tools/gen-themes.mjs 生成，禁止手改
│   ├── showcase.css   # 主题工具栏 / mock 组件 / 主题色卡等基础样式
│   └── scrolly.css    # 场景系统：固定场景、视差层、行号栏、章节导轨、进度条
├── js/
│   ├── themes.js      # 主题单一事实来源：顺序 / 名称 / 色卡元数据（THEMES）
│   ├── content.js     # 内容单一来源：导航 NAV / 场景 SCENES / 代码块 CODE_BLOCKS / 打字机 TYPE_LINES
│   ├── constants.js   # 滚动引擎与布局的可调常量（魔法数字收口）
│   ├── render.js      # 渲染器：按 content.js + themes.js 生成导轨 / 主题选项 / 全部场景
│   ├── showcase.js    # 主题切换 / 明暗切换 / 弹层交互 / 代码块注入
│   └── scrolly.js     # 滚动引擎：--p 场景进度、--py 视差、行号、导轨、Hero 打字机
├── tools/
│   ├── gen-themes.mjs    # 从 frontend/src/theme/*.css 生成 css/themes.css
│   ├── check-themes.mjs  # 漂移检查（不一致时退出码 1）
│   └── deploy.mjs        # 部署构建：拷贝网站资源到 public/ 并压缩 HTML/CSS/JS
└── assets/
    ├── favicon.svg / favicon-dark.svg
    └── formula_ocr.svg  # 公式 OCR 示例图（CSS mask 引用）
```

## 架构

- **内容数据驱动**：`index.html` 只是骨架；`js/render.js` 在 DOM 解析后按 `js/content.js`（内容）与 `js/themes.js`（主题）生成导轨、主题弹层选项与全部场景。
- **主题单一来源**：`js/themes.js`（元数据）→ `tools/gen-themes.mjs`（生成 `css/themes.css`）→ `tools/check-themes.mjs`（漂移校验）。
- **常量收口**：`js/constants.js`（滚轮灵敏度、行号栏行数、锚点比例、布局断点、打字机节奏等）。
- **脚本加载顺序**：`render.js → showcase.js → scrolly.js`（均为 ES Module，`index.html` 底部按序引用）。

## 场景数据模型（js/content.js 的 SCENES）

- 每个场景对象带 `tpl` 字段，决定 `js/render.js` 使用的渲染模板：
  `hero`（序章）/ `act`（第一幕·摩擦、第二幕·解法）/ `chapter`（功能章节）/ `principles`（设计理念）/ `stack`（技术栈）/ `themes`（主题）/ `outro`（尾声）。
- 常用字段：`id`、`len`（场景高度 `--len × 100vh`）、`label`（aria-label）、`glyphs`（视差字形）、`ghost`（幽灵章节号）、`kicker / title / desc`、`points`、`subCards`、`feature`（主功能卡片，含 `mock`）、`stage`（act 专属舞台）等。
- mock 内容块按 `type` 渲染：`col` / `formula-ocr` / `code` / `steps` / `row` / `file-tree` / `terminal` / `diff-del` / `diff-add` / `diff-hunk` / `badges` / `chat`。
- 右侧导轨 `NAV` 的 `id` 必须与场景 `id` 一一对应。
- 代码块内容在 `CODE_BLOCKS`，场景内用 `ref` 引用；未被引用的 key 应删除。

## 主题体系

- 主题（樱桃红 / 海螺橙 / 琥珀黄 / 庭芜绿 / 景泰蓝 / 凝夜紫 / 幽冥黑），明暗双档。
- `css/themes.css` 由 `tools/gen-themes.mjs` 按 `frontend/src/theme/*.css` 顺序拼接生成；顺序与 `frontend/src/theme/useThemeManager.js` 的 `THEME_ORDER` 一致。
- 修改主题：改 `frontend` 源 → `npm run themes:gen` → 同步 `js/themes.js` 的 `THEMES`（`swatch`/`accent` 取自 light 档 `--primary`/`--accent`）→ `npm run themes:check`。

## 动效与布局

- 每个 `[data-pin]` 场景的滚动进度写入 `--p`（0→1），CSS 用 `clamp()` 分段编排文本/mock 入场退场。
- `[data-parallax]` 元素按视口中心偏移 × 速率写入 `--py`，形成前后景视差。
- 主题区横向滑动距离写入 `--tw`。
- 滚轮采用槽位式跳转：一格滚轮平滑跳到相邻分区的锚点（内容完整呈现的位置）；
  主题分区内部连续滑动色卡，滑到最右后再滚一格才进入下一分区，从最底部上滚先跳回最右色卡再连续滑回。
- 左侧行号栏（编辑器隐喻）、右侧章节导轨高亮、顶部编译进度条、底部编译状态角标（滚动到底显示 `intro.pdf 已生成`）、Hero 打字机。
- 小屏（<1024px）/ 短屏（<620px）/ `prefers-reduced-motion` 时自动退化为普通堆叠布局（`no-pin`）；单场景内容超高时标记 `.unfit` 兜底。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `npm start` / `npm run dev` | 启动静态服务器（http://127.0.0.1:4173） |
| `npm run themes:gen` | 从 frontend 主题源重新生成 `css/themes.css` |
| `npm run themes:check` | 校验 `css/themes.css` 是否漂移（CI/pre-commit 可用） |
| `npm run deploy` | 部署构建：生成 `public/`（拷贝网站资源并压缩 HTML/CSS/JS） |

## 部署

- 运行 `npm run deploy`（`tools/deploy.mjs`）：把网站资源（`index.html` / `css/` / `js/` / `assets/`）拷贝到 `public/`，
  并对 HTML / CSS / JS 做无损压缩（零依赖，仅用 Node 内置模块），产物可直接部署到任意静态服务器。
- 每次运行会先清空 `public/` 再重新生成；`public/` 已加入 `.gitignore`，不入库。

## 依赖关系

- **主题 token**：依赖父仓库 `frontend/src/theme/*.css`（生成而非复制）。
- **字体**：经 cdnjs 加载（LXGW WenKai / Noto Sans SC / STIX Two Text / JetBrains Mono），仓库内不保留字体文件；离线回退系统字体。
- **无其他依赖**：保持零依赖是设计目标，不要引入运行时依赖或构建步骤。

## 文档

- `README.md`：项目介绍 + 维护手册（改文案/加场景/改主题/调参数的操作入口）。
- 父仓库 `latexcellence/docs/showcase.md`：展示页设计文档（与代码同步维护）。
- 结构或架构变更时：更新本文件 + `README.md`，必要时同步父仓库 `docs/showcase.md`。
