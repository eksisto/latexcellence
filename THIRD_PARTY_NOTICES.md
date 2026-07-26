# 第三方开源项目声明

本项目 LateXcellence 使用了以下开源项目和资源。在此向所有贡献者表示感谢。

---

## 一、前端运行时依赖 (npm)

| 项目 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| [React](https://react.dev) | ^19.1.0 | MIT | 前端 UI 框架 |
| [React DOM](https://react.dev) | ^19.1.0 | MIT | DOM 渲染 |
| [React Router DOM](https://reactrouter.com) | ^7.5.0 | MIT | 客户端路由 |
| [React Markdown](https://github.com/remarkjs/react-markdown) | ^10.1.0 | MIT | Markdown 渲染 |
| [rehype-katex](https://github.com/remarkjs/remark-math) | ^7.0.1 | MIT | KaTeX rehype 插件 |
| [remark-gfm](https://github.com/remarkjs/remark-gfm) | ^4.0.1 | MIT | GFM Markdown 插件 |
| [remark-math](https://github.com/remarkjs/remark-math) | ^6.0.0 | MIT | 数学公式解析 |
| [zustand](https://github.com/pmndrs/zustand) | ^5.0.0 | MIT | 状态管理 |
| [KaTeX](https://katex.org) | ^0.17.0 | MIT | 数学公式渲染引擎（含内置字体） |
| [@callmebill/lxgw-wenkai-web](https://github.com/lxgw/LxgwWenKai) | ^1.522.0 | OFL-1.1 | 霞鹜文楷中文字体（Web 字体包） |
| [@fontsource/jetbrains-mono](https://fontsource.org/fonts/jetbrains-mono) | ^5.3.0 | OFL-1.1 | JetBrains Mono 等宽字体 |
| [@fontsource/noto-sans-sc](https://fontsource.org/fonts/noto-sans-sc) | ^5.2.9 | OFL-1.1 | Noto Sans SC 中文字体 |
| [sonner](https://sonner.emilkowal.ski) | ^2.0.7 | MIT | Toast 通知组件 |
| [lucide-react](https://lucide.dev) | ^1.23.0 | ISC and MIT | 图标库 |
| [@base-ui/react](https://base-ui.com) | ^1.6.0 | MIT | 无头 UI Button 组件 |
| [@radix-ui/react-dialog](https://radix-ui.com) | ^1.1.18 | MIT | 对话框原语 |
| [@radix-ui/react-label](https://radix-ui.com) | ^2.1.11 | MIT | 标签原语 |
| [@radix-ui/react-scroll-area](https://radix-ui.com) | ^1.2.13 | MIT | 滚动区域原语 |
| [@radix-ui/react-select](https://radix-ui.com) | ^2.3.2 | MIT | 下拉选择原语 |
| [@radix-ui/react-separator](https://radix-ui.com) | ^1.1.11 | MIT | 分隔线原语 |
| [@radix-ui/react-slot](https://radix-ui.com) | ^1.3.0 | MIT | Slot 原语 |
| [@radix-ui/react-tabs](https://radix-ui.com) | ^1.1.16 | MIT | 标签页原语 |
| [@radix-ui/react-tooltip](https://radix-ui.com) | ^1.2.11 | MIT | 工具提示原语 |
| [class-variance-authority](https://cva.style) | ^0.7.1 | Apache-2.0 | 组件变体定义 |
| [clsx](https://github.com/lukeed/clsx) | ^2.1.1 | MIT | 条件类名字符串 |
| [i18next](https://www.i18next.com) | ^26.3.6 | MIT | 国际化核心库 |
| [i18next-browser-languagedetector](https://github.com/i18next/i18next-browser-languagedetector) | ^8.2.1 | MIT | 浏览器语言自动检测 |
| [react-i18next](https://github.com/i18next/react-i18next) | ^17.0.11 | MIT | React i18next 集成 |
| [tailwind-merge](https://github.com/dcastil/tailwind-merge) | ^3.6.0 | MIT | Tailwind 类名合并 |
| [tw-animate-css](https://github.com/Wombosvideo/tw-animate-css) | ^1.4.0 | MIT | CSS 动画工具 |
| [shadcn](https://ui.shadcn.com) | ^4.12.0 | MIT | UI 组件系统 (CLI) |

## 二、前端开发依赖 (npm devDependencies)

| 项目 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| [Vite](https://vitejs.dev) | ^6.3.0 | MIT | 构建工具 |
| [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react) | ^4.4.0 | MIT | React 编译插件 |
| [Tailwind CSS](https://tailwindcss.com) | ^4.1.0 | MIT | CSS 框架 |
| [@tailwindcss/vite](https://tailwindcss.com) | ^4.1.0 | MIT | Tailwind Vite 插件 |

## 三、后端依赖 (Python pip)

| 项目 | 版本约束 | 许可证 | 用途 |
| --- | --- | --- | --- |
| [FastAPI](https://fastapi.tiangolo.com) | >=0.115.0 | MIT | Web 框架 |
| [uvicorn](https://uvicorn.dev) | >=0.30.0 | BSD-3-Clause | ASGI 服务器 |
| [httpx](https://www.python-httpx.org) | >=0.27.0 | BSD-3-Clause | HTTP 客户端 |
| [openai](https://github.com/openai/openai-python) | >=1.50.0 | Apache-2.0 | OpenAI API SDK |
| [pydantic](https://docs.pydantic.dev) | >=2.0.0 | MIT | 数据模型校验 |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | >=2.0.0 | MIT | 配置管理 |
| [python-multipart](https://github.com/andrew-d/python-multipart) | >=0.0.9 | Apache-2.0 | 文件上传 |
| [Pillow](https://python-pillow.github.io) | >=10.0.0 | Pillow license | 图片处理 |
| [aiofiles](https://github.com/Tinche/aiofiles) | >=24.0.0 | Apache-2.0 | 异步文件 I/O |
| [pytest](https://pytest.org) | >=8.0.0 | MIT | 测试框架 |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | >=0.24.0 | Apache-2.0 | 异步测试支持 |

## 四、内联字体文件 (bundled)

| 字体名称 | 许可证 | 来源 |
| --- | --- | --- |
| 霞鹜文楷 | OFL-1.1 license | [lxgw/LXGW WenKai](https://github.com/lxgw/LxgwWenKai) |
| Noto Sans SC | SIL Open Font License 1.1 | [Google Fonts](https://fonts.adobe.com/fonts/noto-sans-sc) |
| JetBrains Mono | SIL Open Font License 1.1 | [JetBrains](https://www.jetbrains.com/lp/mono) |

## 五、系统级外部依赖

| 项目 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| [TeX Live](https://tug.org/texlive) | 2026 (xelatex/latexmk/latexindent.pl) | GPL-2.0-or-later | LaTeX 编译 |
| [Pandoc](https://pandoc.org) | 3.8.2 | GPL-2.0-or-later | 格式转换 |

### Pandoc 许可证说明

Pandoc 的版权归属 John MacFarlane，使用 GPL-2.0-or-later 许可证。本项目通过子进程调用 Pandoc，不包含或修改其源代码，属于“独立交互”使用。

### TeX Live 许可证说明

TeX Live 包含多个组件（xelatex、latexmk、latexindent.pl、latexdiff 等），整体以 GPL-2.0-or-later 发布。本项目通过子进程调用 TeX 引擎编译 LaTeX 文档，不包含或修改其源代码。

## 六、主题资源

以下配色主题来源于 [ShadcnThemer.com](https://shadcnthemer.com)（作者 [Mike Tromba](https://github.com/miketromba)），部分主题做了些许修改，特此致谢。

| 中文名 | 英文原名 | 作者 | 来源 URL |
| --- | --- | --- | --- |
| 海螺橙 | Tangerine | @mike (tweakcn) | [shadcnthemer.com/themes/44dec9b0...](https://shadcnthemer.com/themes/44dec9b0-4404-4ded-9c7f-8f2a74a18efb) |
| 琥珀黄 | Amber Minimal | @mike (tweakcn) | [shadcnthemer.com/themes/1bf014bc...](https://shadcnthemer.com/themes/1bf014bc-3163-4d1f-98a6-2788cc66f569) |
| 樱桃红 | Sabbir shadcn theme | @sabbirhossendev | [shadcnthemer.com/themes/a00a22e6...](https://shadcnthemer.com/themes/a00a22e6-f853-474d-a332-f861626fbd5e) |
| 庭芜绿 | Northern Lights | @mike (tweakcn) | [shadcnthemer.com/themes/4bd47006...](https://shadcnthemer.com/themes/4bd47006-412c-4464-aa5a-83283e26b92c) |
| 景泰蓝 | Twitter | @mike | [shadcnthemer.com/themes/abb2128e...](https://shadcnthemer.com/themes/abb2128e-7392-4ec7-880c-ef68a0051da3) |
| 凝夜紫 | Violet Bloom | @mike (tweakcn) | [shadcnthemer.com/themes/240c9813...](https://shadcnthemer.com/themes/240c9813-0123-4128-b128-c54519c12a4e) |
| 幽冥黑 | Vercel | @mike | [shadcnthemer.com/themes/418a8650...](https://shadcnthemer.com/themes/418a8650-514e-483b-a8cd-2c6e619ee97c) |

这些主题的配色数据（oklch CSS 自定义属性）以 shadcn/ui 主题格式发布，本项目将其适配为 `[data-theme]` 选择器驱动的多主题系统。所有主题使用 MIT 许可证。
