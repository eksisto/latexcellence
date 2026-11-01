# 第三方开源项目声明

本项目 LaTeXcellence 使用了以下开源项目和资源。在此向所有贡献者表示感谢。

> **合规说明**
>
> 1. 下表列出本项目直接依赖的第三方项目，包含**版权信息**与**许可证全文引用**。
> 2. 许可证全文可通过下方「许可证全文索引」中的链接获取；依赖安装后，其许可证文本也随包保留在本地
>    （前端 `node_modules/<包名>/LICENSE`，后端 `site-packages/<包名>.dist-info/licenses/`），以本地文件为准。
> 3. 版权行以各项目发布 / 分发的许可证文件为准；如版权归属随版本变化，请以官方许可证文件为准。
> 4. 本项目经 npm 与 pip 引入依赖。前端依赖会打包进构建产物（构成再分发）；后端依赖主要用于服务端运行，
>    部分经子进程或进程内调用使用（详见第五节系统级外部依赖说明）。

---

## 许可证全文索引

| 许可证 | 全文链接 |
| --- | --- |
| MIT | https://opensource.org/license/mit/ |
| BSD-3-Clause | https://opensource.org/license/bsd-3-clause/ |
| Apache-2.0 | https://www.apache.org/licenses/LICENSE-2.0 |
| ISC | https://opensource.org/license/isc/ |
| OFL-1.1 (SIL Open Font License) | https://openfontlicense.org/ |
| CC-BY-4.0 (Creative Commons Attribution 4.0) | https://creativecommons.org/licenses/by/4.0/legalcode |
| PSF-based（matplotlib 许可证） | https://matplotlib.org/stable/project/license.html |
| Pillow license (MIT-CMU) | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| BSD（xlrd，含 BSD-2/3 与广告条款变体） | https://xlrd.readthedocs.io/en/latest/licenses.html |
| GPL-2.0-or-later | https://www.gnu.org/licenses/old-licenses/gpl-2.0.html |
| LGPL-2.1-or-later | https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html |

---

## 一、前端运行时依赖 (npm)

| 项目 | 版本 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [React](https://react.dev) | ^19.1.0 | MIT | Copyright (c) Meta Platforms, Inc. and affiliates. | 前端 UI 框架 |
| [React DOM](https://react.dev) | ^19.1.0 | MIT | Copyright (c) Meta Platforms, Inc. and affiliates. | DOM 渲染 |
| [React Router DOM](https://reactrouter.com) | ^7.5.0 | MIT | Copyright (c) React Training LLC (2015-2019), Remix Software Inc. (2020-2021), Shopify Inc. (2022-2023) | 客户端路由 |
| [React Markdown](https://github.com/remarkjs/react-markdown) | ^10.1.0 | MIT | Copyright (c) Espen Hovlandsdal | Markdown 渲染 |
| [rehype-katex](https://github.com/remarkjs/remark-math) | ^7.0.1 | MIT | Copyright (c) Junyoung Choi | KaTeX rehype 插件 |
| [remark-gfm](https://github.com/remarkjs/remark-gfm) | ^4.0.1 | MIT | Copyright (c) Titus Wormer | GFM Markdown 插件 |
| [remark-math](https://github.com/remarkjs/remark-math) | ^6.0.0 | MIT | Copyright (c) Junyoung Choi | 数学公式解析 |
| [zustand](https://github.com/pmndrs/zustand) | ^5.0.0 | MIT | Copyright (c) 2019 Paul Henschel | 状态管理 |
| [KaTeX](https://katex.org) | ^0.17.0 | MIT | Copyright (c) 2013-2020 Khan Academy and other contributors | 数学公式渲染引擎（含内置字体） |
| [highlight.js](https://highlightjs.org) | ^11.11.1 | BSD-3-Clause | Copyright (c) 2006, Ivan Sagalaev | 代码语法高亮引擎（LaTeX / BibTeX / Markdown / Python / JSON 等） |
| [@callmebill/lxgw-wenkai-web](https://github.com/CMBill/lxgw-wenkai-web) | ^1.522.0 | OFL-1.1（字体）/ MIT（npm 包装） | 字体 Copyright (c) 2020-2022 落霞孤鹜 (lxgw)；包装 Copyright (c) CM_Bill | 霞鹜文楷中文字体（Web 字体包） |
| [@fontsource/jetbrains-mono](https://fontsource.org/fonts/jetbrains-mono) | ^5.3.0 | OFL-1.1 | Copyright (c) 2020 The JetBrains Mono Project Authors | JetBrains Mono 等宽字体 |
| [@fontsource/noto-sans-sc](https://fontsource.org/fonts/noto-sans-sc) | ^5.2.9 | OFL-1.1 | Copyright (c) Google Inc. | Noto Sans SC 中文字体 |
| [@fontsource/stix-two-text](https://fontsource.org/fonts/stix-two-text) | ^5.3.0 | OFL-1.1 | Copyright (c) 2001-2021 The STIX Fonts Project Authors | STIX Two Text 学术衬线显示字体（品牌字标 / § 编号 / 小大写标签） |
| [sonner](https://sonner.emilkowal.ski) | ^2.0.7 | MIT | Copyright (c) 2023 Emil Kowalski | Toast 通知组件 |
| [lucide-react](https://lucide.dev) | ^1.23.0 | ISC and MIT | Copyright (c) Lucide Icons and Contributors | 图标库 |
| [@base-ui/react](https://base-ui.com) | ^1.6.0 | MIT | Copyright (c) 2019 Material-UI SAS | 无头 UI 组件原语（Dialog / ContextMenu / Select 等） |
| [class-variance-authority](https://cva.style) | ^0.7.1 | Apache-2.0 | Copyright (c) Joe Bell | 组件变体定义 |
| [clsx](https://github.com/lukeed/clsx) | ^2.1.1 | MIT | Copyright (c) Luke Edwards | 条件类名字符串 |
| [i18next](https://www.i18next.com) | ^26.3.6 | MIT | Copyright (c) 2011-present i18next | 国际化核心库 |
| [pinyin-pro](https://github.com/zh-lx/pinyin-pro) | ^3.29.3 | MIT | Copyright (c) 2020-present zh-lx | 编辑器命令快速输入的中文拼音转换（全拼 / 首字母序列预计算，按需动态加载） |
| [opencc-js](https://github.com/BYVoid/OpenCC) | ^1.4.2 | MIT AND Apache-2.0 | Copyright (c) BYVoid | 编辑器命令中文名简→繁转换（繁体界面本地化展示，按需动态加载） |
| [i18next-browser-languagedetector](https://github.com/i18next/i18next-browser-languagedetector) | ^8.2.1 | MIT | Copyright (c) 2011-present i18next | 浏览器语言自动检测 |
| [react-i18next](https://github.com/i18next/react-i18next) | ^17.0.11 | MIT | Copyright (c) 2015-present i18next | React i18next 集成 |
| [@uiw/react-codemirror](https://github.com/uiwjs/react-codemirror) | ^4.25.11 | MIT | Copyright (c) 2018 Kenny Wong | CodeMirror 6 的 React 受控封装 |
| [@codemirror/language](https://github.com/codemirror/dev) | ^6.12.4 | MIT | Copyright (c) by Marijn Haverbeke and others | 编辑器语言支持基础设施（StreamLanguage 流式模式挂载） |
| [@codemirror/view](https://github.com/codemirror/dev) | ^6.43.9 | MIT | Copyright (c) by Marijn Haverbeke and others | 编辑器视图层（主题 / 软换行等视图扩展） |
| [@codemirror/state](https://github.com/codemirror/dev) | ^6.7.1 | MIT | Copyright (c) by Marijn Haverbeke and others | 编辑器状态模型（事务 / 文档状态） |
| [@codemirror/legacy-modes](https://github.com/codemirror/legacy-modes) | ^6.5.3 | MIT | Copyright (c) by Marijn Haverbeke and others | stex LaTeX 流式语法高亮模式（经语言包抽象层引入） |
| [@codemirror/lint](https://github.com/codemirror/dev) | ^6.9.7 | MIT | Copyright (c) by Marijn Haverbeke and others | 编辑器实时语法诊断（CM6 lint 扩展：未闭合环境 / 花括号失衡内联提示，editor v3 阶段三） |
| [@lezer/highlight](https://github.com/lezer-parser/highlight) | ^1.2.3 | MIT | Copyright (c) by Marijn Haverbeke and others | 语法高亮标签定义（HighlightStyle） |
| [@lezer/common](https://github.com/lezer-parser/common) | ^1.5.0 | MIT | Copyright (c) by Marijn Haverbeke and others | Lezer 树公共类型（LaTeX 子集语法的括号配对 node prop：openedBy / closedBy，editor v3 阶段六b） |
| [tailwind-merge](https://github.com/dcastil/tailwind-merge) | ^3.6.0 | MIT | Copyright (c) 2021 Dany Castillo | Tailwind 类名合并 |
| [pdf.js (pdfjs-dist)](https://github.com/mozilla/pdf.js) | ^4.10.38 | Apache-2.0 | Copyright 2012 Mozilla Foundation | PDF 渲染引擎（编辑器 PDF 预览替换 iframe，SyncTeX 正反向定位的渲染层；按需动态加载） |
| [tw-animate-css](https://github.com/Wombosvideo/tw-animate-css) | ^1.4.0 | MIT | Copyright (c) 2025 Wombosvideo | CSS 动画工具 |

## 二、前端开发依赖 (npm devDependencies)

| 项目 | 版本 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [Vite](https://vitejs.dev) | ^6.3.0 | MIT | Copyright (c) 2019-present, VoidZero Inc. and Vite contributors | 构建工具 |
| [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react) | ^4.4.0 | MIT | Copyright (c) 2019-present, Evan You and Vite contributors | React 编译插件 |
| [Tailwind CSS](https://tailwindcss.com) | ^4.1.0 | MIT | Copyright (c) Tailwind Labs, Inc. | CSS 框架 |
| [@tailwindcss/vite](https://tailwindcss.com) | ^4.1.0 | MIT | Copyright (c) Tailwind Labs, Inc. | Tailwind Vite 插件 |
| [@lezer/generator](https://github.com/lezer-parser/generator) | ^1.8.0 | MIT | Copyright (c) by Marijn Haverbeke and others | LaTeX 子集语法构建期生成器（latex.grammar → parser，仅 dev/build/test 阶段使用，不打包进构建产物） |
| [shadcn](https://ui.shadcn.com) | ^4.12.0 | MIT | Copyright (c) 2023 shadcn | UI 组件系统 CLI（生成组件源码，运行时无依赖） |

## 三、后端依赖 (Python pip)

| 项目 | 版本约束 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [FastAPI](https://fastapi.tiangolo.com) | >=0.115.0 | MIT | Copyright (c) 2018 Sebastián Ramírez | Web 框架 |
| [uvicorn](https://uvicorn.dev) | >=0.30.0 | BSD-3-Clause | Copyright (c) 2017-present Encode OSS Ltd. | ASGI 服务器 |
| [httpx](https://www.python-httpx.org) | >=0.27.0 | BSD-3-Clause | Copyright (c) 2019 Encode OSS Ltd. | HTTP 客户端 |
| [openai](https://github.com/openai/openai-python) | >=1.66.0 | Apache-2.0 | Copyright (c) OpenAI | OpenAI API SDK |
| [pydantic](https://docs.pydantic.dev) | >=2.0.0 | MIT | Copyright (c) 2017 to present Pydantic Services Inc. and individual contributors | 数据模型校验 |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | >=2.0.0 | MIT | Copyright (c) 2022 Samuel Colvin and other contributors | 配置管理 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | >=1.0.0 | BSD-3-Clause | Copyright (c) 2020 Saurabh Kumar | 加载项目根 `.env` 环境变量文件（凭证类配置注入） |
| [python-multipart](https://github.com/andrew-d/python-multipart) | >=0.0.9 | Apache-2.0 | Copyright (c) Andrew Dunham | 文件上传 |
| [py7zr](https://github.com/miurahr/py7zr) | >=1.1.0 | LGPL-2.1-or-later | Copyright (c) Hiroshi Miura | 多用户上传 7z 压缩包解压 |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | >=23.1.0 | MIT | Copyright (c) 2023 Hynek Schlawack | 多用户模式密码哈希（argon2id） |
| [aiosmtplib](https://github.com/cole/aiosmtplib) | >=3.0.0 | MIT | Copyright (c) 2015-2025 Cole Maclean | 多用户模式 SMTP 异步邮件发送（多用户 v2 P6：邮件通知与自助找回） |
| [maxminddb](https://github.com/maxmind/MaxMind-DB-Reader-python) | >=2.6.0 | Apache-2.0 | Copyright (c) 2014 MaxMind, Inc. | 注册自动审批地理白名单：读取 GeoLite2 离线 .mmdb 库解析 IP 归属（库文件本身需按 MaxMind 协议自行获取） |
| [ip2region](https://github.com/lionsoul2014/ip2region)（vendored 源码，不经 pip） | master (2025/10) | Apache-2.0 OR MIT（双许可） | Copyright 2022 The Ip2Region Authors; MIT 部分 Copyright (c) 2015 Lionsoul<chenxin619315@gmail.com> | 注册自动审批地理白名单中国优先数据源：官方 Python binding 内嵌于 `backend/core/multiuser/ip2region_xdb/`（searcher.py + util.py，仅改相对导入），读取 ip2region 离线 .xdb 库解析 IP 归属（.xdb 数据文件按其官方渠道自行获取，不含在本仓内） |
| [docker SDK for Python](https://github.com/docker/docker-py) | >=7.0.0 | Apache-2.0 | Copyright (c) 2016-2022 Docker Inc. | Docker 沙箱执行后端（v2 P1a；sandbox.mode=auto/docker 时使用） |
| [Pillow](https://python-pillow.github.io) | >=10.0.0 | Pillow license | Copyright (c) 1997-2011 Secret Labs AB; 1995-2011 Fredrik Lundh; 2010- Jeffrey A. Clark (Alex) and contributors | 图片处理 |
| [aiofiles](https://github.com/Tinche/aiofiles) | >=24.0.0 | Apache-2.0 | Copyright (c) Tin Tvrtković | 异步文件 I/O |
| [trafilatura](https://trafilatura.readthedocs.io) | >=2.1.0 | Apache-2.0 | Copyright (c) Adrien Barbaresi | 网页正文抽取 |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) | >=4.15.0 | MIT | Copyright (c) Leonard Richardson | HTML 解析（网页正文抽取辅助） |
| [Playwright Python](https://playwright.dev/python/) | >=1.55.0 | Apache-2.0 | Copyright (c) Microsoft Corporation | 可选的 JavaScript 网页渲染回退；浏览器二进制需单独安装 |
| [numpy](https://numpy.org) | >=2.0.0 | BSD-3-Clause | Copyright (c) 2005-2025 NumPy Developers | 数值计算 |
| [pandas](https://pandas.pydata.org) | >=2.0.0 | BSD-3-Clause | Copyright (c) 2008-2011 AQR Capital Management, LLC, Lambda Foundry, Inc. and PyData Development Team; 2011-2026 Open source contributors | 数据表格读取与解析 |
| [scipy](https://scipy.org) | >=1.14.0 | BSD-3-Clause | Copyright (c) 2001-2002 Enthought, Inc.; 2003- SciPy Developers | 统计检验 |
| [matplotlib](https://matplotlib.org) | >=3.9.0 | PSF-based (BSD-compatible) | Copyright (c) 2003-2011 John D. Hunter; 2012- Matplotlib Development Team | 图表绘制与 PDF/SVG/PNG 导出 |
| [seaborn](https://seaborn.pydata.org) | >=0.13.0 | BSD-3-Clause | Copyright (c) 2012-2023 Michael L. Waskom | 统计绘图样式 |
| [colorcet](https://colorcet.holoviz.org) | >=3.1.0 | CC-BY-4.0 | Copyright (c) James A. Bednar (HoloViz) | 科研色板 |
| [openpyxl](https://openpyxl.readthedocs.io) | >=3.1.0 | MIT | Copyright (c) openpyxl contributors (Eric Gazoni, Charlie Clark) | 读取 .xlsx 数据文件 |
| [xlrd](https://xlrd.readthedocs.io) | >=2.0.1 | BSD | Copyright (c) 2005-2009 Stephen John Machin, Lingfo Pty Ltd | 读取 .xls 数据文件 |
| [pytest](https://pytest.org) | >=8.0.0 | MIT | Copyright (c) 2004 Holger Krekel and others | 测试框架 |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | >=0.24.0 | Apache-2.0 | Copyright (c) Tin Tvrtković | 异步测试支持 |
| [aiosmtpd](https://github.com/aio-libs/aiosmtpd) | >=1.4.6 | Apache-2.0 | Copyright (c) The aiosmtpd project and individual contributors | 本地 mock SMTP server（仅测试使用，多用户 v2 P6） |
| [ruff](https://docs.astral.sh/ruff/) | ==0.16.6 | MIT | Copyright (c) 2022 Charlie Marsh | Python 静态检查（开发/CI 门禁，见 `requirements-dev.txt`，不参与运行与分发） |
| [black](https://black.readthedocs.io) | ==26.5.1 | MIT | Copyright (c) 2018 Łukasz Langa | Python 代码格式化检查（开发/CI 门禁，见 `requirements-dev.txt`，不参与运行与分发） |
| [pytest-xdist](https://github.com/pytest-dev/pytest-xdist) | ==3.8.0 | MIT | Copyright (c) holger krekel and contributors | 测试并行分发（开发/CI 门禁，见 `requirements-dev.txt`，不参与运行与分发） |

## 四、内联字体文件 (bundled)

| 字体名称 | 许可证 | 版权 / 作者 | 来源 |
| --- | --- | --- | --- |
| 霞鹜文楷 | OFL-1.1 | Copyright (c) 2020-2022 落霞孤鹜 (lxgw) | [lxgw/LXGW WenKai](https://github.com/lxgw/LxgwWenKai) |
| Noto Sans SC | OFL-1.1 | Copyright (c) Google Inc. | [Google Fonts](https://fonts.adobe.com/fonts/noto-sans-sc) |
| JetBrains Mono | OFL-1.1 | Copyright (c) 2020 The JetBrains Mono Project Authors | [JetBrains](https://www.jetbrains.com/lp/mono) |
| STIX Two Text | OFL-1.1 | Copyright (c) 2001-2021 The STIX Fonts Project Authors | [STIX Fonts Project](https://github.com/stipub/stixfonts) |

> 说明：`showcase/` 展示页经又拍云 Zstatic（s4.zstatic.net，cdnjs 镜像）加载上述同一批字体（见 `showcase/css/fonts.css`、`showcase/index.html`），不新增仓库内资产与依赖。

## 五、系统级外部依赖

| 项目 | 版本 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [TeX Live](https://tug.org/texlive) | 2026 (xelatex/latexmk/latexindent.pl) | GPL-2.0-or-later | Copyright (c) TeX Live / DANTE e.V. 及各组件作者 | LaTeX 编译 |
| [Pandoc](https://pandoc.org) | 3.6.4 | GPL-2.0-or-later | Copyright (c) 2006- John MacFarlane | 格式转换 |
| [unar / The Unarchiver](https://theunarchiver.com) | Debian main 源（版本随镜像基础 Debian 发行版） | LGPL-2.1-or-later | Copyright (c) 2017-present, MacPaw Inc.; upstream Dag Ågren 及 XADMaster 贡献者 | 多用户模式 rar 压缩包解压的首选解压器（多用户 v2 P2；后端镜像内置） |
| [libarchive / bsdtar](https://www.libarchive.org) | 3.7.x（各发行版 main 源） | BSD-3-Clause（含个别组件 BSD-2-Clause） | Copyright (c) 2003-2025, Tim Kientzle, Charles P. Boyd 及 libarchive contributors | rar 解压回退解压器：宿主缺失 unar 时经 `libarchive-tools` 包提供的 bsdtar 子进程解压（RAR4/RAR5 reader 为 BSD 授权独立实现；系统包引入，不经 pip，不随本项目再分发） |
| [unar / The Unarchiver](https://theunarchiver.com)（convert 沙箱镜像内置） | Debian main 源（版本随镜像基础 Debian 发行版） | LGPL-2.1-or-later | Copyright (c) 2017-present, MacPaw Inc.; upstream Dag Ågren 及 XADMaster 贡献者 | Docker 沙箱模式 rar 解压容器（convert 镜像）内的首选解压器：`docker/convert/Dockerfile` 构建期经 Debian main 源 apt 安装，Debian 包随镜像分发 |
| [libarchive / bsdtar](https://www.libarchive.org)（convert 沙箱镜像内置） | Debian main 源 `libarchive-tools`（版本随镜像基础 Debian 发行版） | BSD-3-Clause（含个别组件 BSD-2-Clause） | Copyright (c) 2003-2025, Tim Kientzle, Charles P. Boyd 及 libarchive contributors | Docker 沙箱模式 rar 解压容器（convert 镜像）内的回退解压器，与 unar 按固定顺序取用：构建期经 Debian main 源 apt 安装 `libarchive-tools`，Debian 包随镜像分发 |

### Pandoc 许可证说明

Pandoc 的版权归属 John MacFarlane，使用 GPL-2.0-or-later 许可证。本项目通过子进程调用 Pandoc，不包含或修改其源代码，属于“独立交互”使用。

### TeX Live 许可证说明

TeX Live 包含多个组件（xelatex、latexmk、latexindent.pl、latexdiff 等），整体以 GPL-2.0-or-later 发布。本项目通过子进程调用 TeX 引擎编译 LaTeX 文档，不包含或修改其源代码。

### unar 许可证说明

unar 是 The Unarchiver 项目的命令行工具，核心库 XADMaster 以 LGPL-2.1-or-later 发布。本项目通过子进程调用 unar 解压用户上传的 rar 压缩包（仅多用户模式），不包含或修改其源代码；二进制经 Docker 后端镜像由 Debian main 源安装，不随本项目再分发。

### libarchive（bsdtar）许可证说明

libarchive 以 BSD-3-Clause 许可证发布（部分组件为 BSD-2-Clause），其 RAR4/RAR5 reader 为不含 unRAR 限制代码的 BSD 授权独立实现。当宿主机缺失 unar 时，本项目经 `libarchive-tools` 包提供的 bsdtar 以子进程方式解压 rar 压缩包作为回退（仅多用户模式），不包含或修改其源代码；bsdtar 由系统包管理器安装（各主流发行版 main 源收录），不经 pip 引入，不随本项目再分发。

## 六、主题资源

配色主题来源于 [ShadcnThemer.com](https://shadcnthemer.com)，做了些许修改，特此致谢。

| 中文名 | 英文原名 | 作者 | 来源 URL |
| --- | --- | --- | --- |
| 海螺橙 | Tangerine | @mike | [shadcnthemer.com/themes/44dec9b0...](https://shadcnthemer.com/themes/44dec9b0-4404-4ded-9c7f-8f2a74a18efb) |
| 琥珀黄 | Amber Minimal | @mike | [shadcnthemer.com/themes/1bf014bc...](https://shadcnthemer.com/themes/1bf014bc-3163-4d1f-98a6-2788cc66f569) |
| 樱桃红 | Sabbir shadcn theme | @sabbirhossendev | [shadcnthemer.com/themes/a00a22e6...](https://shadcnthemer.com/themes/a00a22e6-f853-474d-a332-f861626fbd5e) |
| 庭芜绿 | Northern Lights | @mike | [shadcnthemer.com/themes/4bd47006...](https://shadcnthemer.com/themes/4bd47006-412c-4464-aa5a-83283e26b92c) |
| 景泰蓝 | Twitter | @mike | [shadcnthemer.com/themes/abb2128e...](https://shadcnthemer.com/themes/abb2128e-7392-4ec7-880c-ef68a0051da3) |
| 凝夜紫 | Violet Bloom | @mike | [shadcnthemer.com/themes/240c9813...](https://shadcnthemer.com/themes/240c9813-0123-4128-b128-c54519c12a4e) |
| 幽冥黑 | Vercel | @mike | [shadcnthemer.com/themes/418a8650...](https://shadcnthemer.com/themes/418a8650-514e-483b-a8cd-2c6e619ee97c) |

这些主题的配色数据（oklch CSS 自定义属性）以 shadcn/ui 主题格式发布，本项目将其适配为 `[data-theme]` 选择器驱动的多主题系统。所有主题使用 MIT 许可证。
