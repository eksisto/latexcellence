# 第三方开源项目声明

本项目 LateXcellence 使用了以下开源项目和资源。在此向所有贡献者表示感谢。

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
| [@base-ui/react](https://base-ui.com) | ^1.6.0 | MIT | Copyright (c) 2019 Material-UI SAS | 无头 UI Button 组件 |
| [@radix-ui/react-dialog](https://radix-ui.com) | ^1.1.18 | MIT | Copyright (c) 2022 WorkOS | 对话框原语 |
| [@radix-ui/react-label](https://radix-ui.com) | ^2.1.11 | MIT | Copyright (c) 2022 WorkOS | 标签原语 |
| [@radix-ui/react-scroll-area](https://radix-ui.com) | ^1.2.13 | MIT | Copyright (c) 2022 WorkOS | 滚动区域原语 |
| [@radix-ui/react-select](https://radix-ui.com) | ^2.3.2 | MIT | Copyright (c) 2022 WorkOS | 下拉选择原语 |
| [@radix-ui/react-separator](https://radix-ui.com) | ^1.1.11 | MIT | Copyright (c) 2022 WorkOS | 分隔线原语 |
| [@radix-ui/react-slot](https://radix-ui.com) | ^1.3.0 | MIT | Copyright (c) 2022 WorkOS | Slot 原语 |
| [@radix-ui/react-tabs](https://radix-ui.com) | ^1.1.16 | MIT | Copyright (c) 2022 WorkOS | 标签页原语 |
| [@radix-ui/react-tooltip](https://radix-ui.com) | ^1.2.11 | MIT | Copyright (c) 2022 WorkOS | 工具提示原语 |
| [class-variance-authority](https://cva.style) | ^0.7.1 | Apache-2.0 | Copyright (c) Joe Bell | 组件变体定义 |
| [clsx](https://github.com/lukeed/clsx) | ^2.1.1 | MIT | Copyright (c) Luke Edwards | 条件类名字符串 |
| [i18next](https://www.i18next.com) | ^26.3.6 | MIT | Copyright (c) 2011-present i18next | 国际化核心库 |
| [i18next-browser-languagedetector](https://github.com/i18next/i18next-browser-languagedetector) | ^8.2.1 | MIT | Copyright (c) 2011-present i18next | 浏览器语言自动检测 |
| [react-i18next](https://github.com/i18next/react-i18next) | ^17.0.11 | MIT | Copyright (c) 2015-present i18next | React i18next 集成 |
| [tailwind-merge](https://github.com/dcastil/tailwind-merge) | ^3.6.0 | MIT | Copyright (c) 2021 Dany Castillo | Tailwind 类名合并 |
| [tw-animate-css](https://github.com/Wombosvideo/tw-animate-css) | ^1.4.0 | MIT | Copyright (c) 2025 Wombosvideo | CSS 动画工具 |
| [shadcn](https://ui.shadcn.com) | ^4.12.0 | MIT | Copyright (c) 2023 shadcn | UI 组件系统 (CLI) |

## 二、前端开发依赖 (npm devDependencies)

| 项目 | 版本 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [Vite](https://vitejs.dev) | ^6.3.0 | MIT | Copyright (c) 2019-present, VoidZero Inc. and Vite contributors | 构建工具 |
| [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react) | ^4.4.0 | MIT | Copyright (c) 2019-present, Yuxi (Evan) You and Vite contributors | React 编译插件 |
| [Tailwind CSS](https://tailwindcss.com) | ^4.1.0 | MIT | Copyright (c) Tailwind Labs, Inc. | CSS 框架 |
| [@tailwindcss/vite](https://tailwindcss.com) | ^4.1.0 | MIT | Copyright (c) Tailwind Labs, Inc. | Tailwind Vite 插件 |

## 三、后端依赖 (Python pip)

| 项目 | 版本约束 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [FastAPI](https://fastapi.tiangolo.com) | >=0.115.0 | MIT | Copyright (c) 2018 Sebastián Ramírez | Web 框架 |
| [uvicorn](https://uvicorn.dev) | >=0.30.0 | BSD-3-Clause | Copyright (c) 2017-present Encode OSS Ltd. | ASGI 服务器 |
| [httpx](https://www.python-httpx.org) | >=0.27.0 | BSD-3-Clause | Copyright (c) 2019 Encode OSS Ltd. | HTTP 客户端 |
| [openai](https://github.com/openai/openai-python) | >=1.66.0 | Apache-2.0 | Copyright (c) OpenAI | OpenAI API SDK |
| [pydantic](https://docs.pydantic.dev) | >=2.0.0 | MIT | Copyright (c) 2017 to present Pydantic Services Inc. and individual contributors | 数据模型校验 |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | >=2.0.0 | MIT | Copyright (c) 2022 Samuel Colvin and other contributors | 配置管理 |
| [python-multipart](https://github.com/andrew-d/python-multipart) | >=0.0.9 | Apache-2.0 | Copyright (c) Andrew Dunham | 文件上传 |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | >=23.1.0 | MIT | Copyright (c) 2023 Hynek Schlawack | 多用户模式密码哈希（argon2id） |
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

## 四、内联字体文件 (bundled)

| 字体名称 | 许可证 | 版权 / 作者 | 来源 |
| --- | --- | --- | --- |
| 霞鹜文楷 | OFL-1.1 | Copyright (c) 2020-2022 落霞孤鹜 (lxgw) | [lxgw/LXGW WenKai](https://github.com/lxgw/LxgwWenKai) |
| Noto Sans SC | OFL-1.1 | Copyright (c) Google Inc. | [Google Fonts](https://fonts.adobe.com/fonts/noto-sans-sc) |
| JetBrains Mono | OFL-1.1 | Copyright (c) 2020 The JetBrains Mono Project Authors | [JetBrains](https://www.jetbrains.com/lp/mono) |
| STIX Two Text | OFL-1.1 | Copyright (c) 2001-2021 The STIX Fonts Project Authors | [STIX Fonts Project](https://github.com/stipub/stixfonts) |

> 说明：`showcase/` 展示页通过 cdnjs 从 CDN 加载上述同一批字体（见 `showcase/css/fonts.css`、`showcase/index.html`），不新增仓库内资产与依赖。

## 五、系统级外部依赖

| 项目 | 版本 | 许可证 | 版权 / 作者 | 用途 |
| --- | --- | --- | --- | --- |
| [TeX Live](https://tug.org/texlive) | 2026 (xelatex/latexmk/latexindent.pl) | GPL-2.0-or-later | Copyright (c) TeX Live / DANTE e.V. 及各组件作者 | LaTeX 编译 |
| [Pandoc](https://pandoc.org) | 3.10.1 | GPL-2.0-or-later | Copyright (c) 2006- John MacFarlane | 格式转换 |
| [unar / The Unarchiver](https://theunarchiver.com) | 1.10.8（Debian main 源） | LGPL-2.1-or-later | Copyright (c) 2017-present, MacPaw Inc.; upstream Dag Ågren 及 XADMaster 贡献者 | 多用户模式 rar 压缩包解压（多用户 v2 P2；后端镜像内置） |

### Pandoc 许可证说明

Pandoc 的版权归属 John MacFarlane，使用 GPL-2.0-or-later 许可证。本项目通过子进程调用 Pandoc，不包含或修改其源代码，属于“独立交互”使用。

### TeX Live 许可证说明

TeX Live 包含多个组件（xelatex、latexmk、latexindent.pl、latexdiff 等），整体以 GPL-2.0-or-later 发布。本项目通过子进程调用 TeX 引擎编译 LaTeX 文档，不包含或修改其源代码。

### unar 许可证说明

unar 是 The Unarchiver 项目的命令行工具，核心库 XADMaster 以 LGPL-2.1-or-later 发布。本项目通过子进程调用 unar 解压用户上传的 rar 压缩包（仅多用户模式），不包含或修改其源代码；二进制经 Docker 后端镜像由 Debian main 源安装，不随本项目再分发。

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
