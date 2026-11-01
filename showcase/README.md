# LaTeXcellence - 展示页

单页产品展示网页，采用**滚动叙述式（Scrollytelling）**落地页设计：全屏固定场景 + Parallax 视差，零依赖，仅需 Node.js 18+。

## 启动

```bash
cd showcase
npm start          # 或 node server.js
```

打开 http://127.0.0.1:4173 （可用环境变量 `PORT` / `HOST` 覆盖）。

## 目录结构

```
showcase/
├── server.js          # 零依赖静态文件服务器（Node.js 内置 http）
├── package.json
├── index.html         # 静态骨架：头部 / 图标精灵 / 进度条·行号栏·导轨·工具栏容器 / 尾声页脚
├── css/
│   ├── fonts.css      # 又拍云 Zstatic 网络字体（STIX Two Text / Noto Sans SC / JetBrains Mono）
│   ├── themes.css     # 主题 token —— 由 tools/gen-themes.mjs 从 frontend 生成，勿手改
│   ├── showcase.css   # 主题工具栏 / mock 组件 / 主题色卡等基础样式
│   └── scrolly.css    # 场景系统：固定场景、视差层、行号栏、章节导轨、进度条
├── js/
│   ├── themes.js      # 主题单一事实来源：顺序 / 名称 / 色卡元数据（改主题先改这里）
│   ├── content.js     # 页面内容单一来源：导航 / 场景 / 代码块 / 打字机文案（改文案先改这里）
│   ├── constants.js   # 滚动引擎与布局的可调常量（魔法数字收口）
│   ├── render.js      # 渲染器：按 content.js + themes.js 生成导轨 / 主题选项 / 全部场景
│   ├── showcase.js    # 主题切换 / 明暗切换 / 弹层交互 / 代码块注入
│   └── scrolly.js     # 滚动引擎：场景进度 --p、Parallax --py、行号、导轨、Hero 打字机
├── tools/
│   ├── gen-themes.mjs    # 从 frontend/src/theme/*.css 生成 css/themes.css
│   ├── check-themes.mjs  # 漂移检查：themes.css 与 frontend 主题源不一致时退出码 1
│   └── deploy.mjs        # 部署构建：拷贝网站资源到 public/ 并压缩 HTML/CSS/JS
└── assets/
    ├── favicon.svg / favicon-dark.svg
    └── formula_ocr.svg  # 公式 OCR 示例图（CSS mask 引用）
```

## 架构：内容数据驱动 + 主题单一来源

- **index.html 只是骨架**：场景、章节导轨、主题弹层选项全部由
  `js/render.js` 按 `js/content.js`（内容）与 `js/themes.js`（主题）生成。
- **改文案 / 加场景**：只改 `js/content.js`（`SCENES` / `NAV`），无需碰 HTML。
- **改主题**：改 `frontend/src/theme/*.css` 后运行 `npm run themes:gen`；
  `css/themes.css` 由生成脚本维护，禁止手改。CI / 提交前可跑
  `npm run themes:check` 校验是否漂移。
- 脚本为 ES Module，加载顺序 `render.js → showcase.js → scrolly.js`（`index.html` 底部已按序引用）。

## 页面结构与动效

整页是一份“自己写自己”的 `intro.tex`：左侧固定行号栏跟随滚动计数，右侧章节导轨
高亮当前章节，顶部编译进度条在滚动到底部时显示 `intro.pdf 已生成`。

- **序章（Hero）**：字标 + 标语 + `slogan.tex → slogan.pdf` 编辑器与纸面对照（打字机效果）
- **第一幕 · 摩擦**：编译失败的终端 + 只会“聊天”的助手，引出问题
- **第二幕 · 解法**：marker 保护示意 + Agent 执行闭环
- **功能章节（§1 内容导入 → §8 AI 辅助）**：每章一个全屏固定场景，主 mock 上台，
  其余功能以副卡片呈现
- **设计理念 / 技术栈 / 主题**：理念与技术栈均为三行横排卡片（依次入场）；主题区
  横向滚动展示多套色卡（点击切换本页主题）
- **尾声**：收束语 + 回到顶部

### 动效实现（js/scrolly.js）

- 每个 `[data-pin]` 场景的滚动进度写入 `--p`（0→1），CSS 用 `clamp()` 做分段编排
  （文本先入、mock 后入、尾声淡出）
- 滚轮采用槽位式跳转：一格滚轮平滑跳到相邻分区的锚点（内容完整呈现的位置），
  不再需要滚过整段动画行程；`主题` 分区内部仍为连续横向滑动色卡，滑到最右色卡后
  再滚一格才进入下一分区，从页面最底部上滚会先跳回最右色卡再连续滑回
- `[data-parallax]` 元素按视口中心偏移 × 速率写入 `--py`，形成前后景视差
- 小屏（<1024px）/ 短屏（<620px）/ `prefers-reduced-motion` 时自动退化：
  固定场景取消、全部内容立即可见，仅保留普通堆叠布局

## 维护手册

### 新增 / 修改一个章节场景

1. 在 `js/content.js` 的 `SCENES` 中增删或修改一个场景对象：
   - `tpl: "chapter"` 为功能章节（`kicker / title / desc / subCards / feature`）；
   - `tpl: "act"` 为第一幕 / 第二幕；`hero / principles / stack / themes / outro`
     为对应专用模板。
   - `len` 控制场景高度（`--len × 100vh`）。
2. 如需出现在右侧导轨，在 `NAV` 中加一项（`id` 与场景一致）。
3. 代码块内容放 `CODE_BLOCKS`，场景里用
   `{ type: "code" | "file-tree" | "terminal", ref }` 引用；未被引用的 key 请删除。

### 新增 / 修改主题

1. 改 `frontend/src/theme/*.css`（主题 token 的真正来源）。
2. 运行 `npm run themes:gen` 重新生成 `css/themes.css`。
3. 若主题列表（顺序 / 名称 / 色卡）有变化，同步改 `js/themes.js` 的 `THEMES`。
4. 提交前运行 `npm run themes:check` 确认无漂移。

### 替换公式 OCR 示例图（§1 内容导入）

公式 OCR 示例不再使用 KaTeX 渲染：`js/render.js` 的 `formula-ocr` mock 渲染
`.formula-demo-img`，`css/showcase.css` 经 CSS mask 引用 `assets/formula_ocr.svg`，
并用 `--foreground` 着色，随明暗 / 主题自动切换颜色。需要换图时直接覆盖
`assets/formula_ocr.svg`（保持单色、形状即透明度即可），无需改 JS / CSS。

### 调滚动 / 动效参数

常量集中在 `js/constants.js`（滚轮灵敏度、行号栏行数、锚点比例、断点、打字机节奏等），
改这里即可，不要散落魔法数字。

锚点定位进度按场景模板配置在 `LAYOUT.RAIL_ANCHOR_P`（取该模板最后一个入场元素
完全显示的阈值，hero / themes / outro 为 0），`js/render.js` 渲染时写入各场景的
`data-anchor`，`js/scrolly.js` 跳转时读取；未标注的场景回退到 `RAIL_ANCHOR_RATIO`。

滚轮采用槽位式跳转：一格滚轮平滑跳到相邻分区的锚点（内容完整呈现的位置）；
`LAYOUT.SNAP_MS` 控制跳转动画时长、`LAYOUT.SNAP_TOL_PX` 控制“已停在槽位”的容差；
主题分区内部连续滑动色卡时复用 `LAYOUT.WHEEL_SENSITIVITY`。

### 生成部署产物（public/）

运行 `npm run deploy`（或 `node tools/deploy.mjs`）：

- 把网站资源（`index.html` / `css/` / `js/` / `assets/`）拷贝到 `public/`；
  README 等文档、`server.js`、`tools/` 等开发文件不会进入。
- 对 HTML / CSS / JS 做无损压缩（零依赖，仅用 Node 内置模块实现词法级压缩器）。
- 每次运行会先清空 `public/` 再重新生成，避免残留旧文件。
- JS 压缩后自动用 `node --check` 做语法自检，损坏时构建失败并退出码 1。

`public/` 内的内容可直接上传到任意静态服务器（Nginx / GitHub Pages / 对象存储），
该目录已加入 `.gitignore`，不入库。

## 设计来源

- 主题 token 与 `frontend/src/theme/*.css` 完全一致（樱桃红 / 海螺橙 / 琥珀黄 /
  庭芜绿 / 景泰蓝 / 凝夜紫 / 幽冥黑，含明暗双档），由 `tools/gen-themes.mjs` 生成。
- 字体家族与 `frontend/src/index.css` 一致：正文 LXGW WenKai、标题 Noto Sans SC、
  字标 STIX Two Text、代码 JetBrains Mono。

## 字体

字体统一通过又拍云 Zstatic（`s4.zstatic.net`，cdnjs 镜像）加载，仓库内不保留字体文件：

- 正文 LXGW WenKai：`index.html` 引入 Zstatic 上 `lxgw-wenkai-web` 官方包的
  `lxgwwenkai-regular/result.css`（按 unicode-range 分片，浏览器只按需下载用到的字形）。
- 标题 Noto Sans SC、字标 STIX Two Text、代码 JetBrains Mono：
  `css/fonts.css` 中 `@font-face` 直接引用 Zstatic 上 cdnjs fontsource 包的 woff2 文件。

页面需联网加载字体；离线时回退到系统字体（`font-display: swap`）。
若需更换 CDN，保持 `cdnjs.cloudflare.com/ajax/libs/` 路径结构不变，只换前缀
（如 `s4.zstatic.net/ajax/libs/`）。

`mirrors.sustech.edu.cn` 镜像未提供字体所需的 CORS 响应头（或跨域时会重定向），部署到跨源页面后，浏览器会以“CORS 请求未能成功”拦截 woff2 字体。
