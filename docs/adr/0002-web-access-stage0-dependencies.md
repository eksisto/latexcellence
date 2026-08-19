# ADR 0002：网络访问阶段 0 依赖技术验证

## 状态

候选已评估，生产接入暂缓。

## 背景

阶段 0 需要验证 JavaScript 页面、第二 HTML 抽取器和 PDF 文本解析能力，同时保持当前 `SearchManager`、`ResearchManager`、DeepReader 与 Writing Chat 运行时不变。技术验证使用仓库自有 HTML fixture 和本地 PDF，禁止在 pytest 中访问公网。

## 候选与实测

| 能力 | 候选 | 许可证 | Windows / 安装特征 | 本地结果 | 生产回退 |
| --- | --- | --- | --- | --- | --- |
| 浏览器 | Playwright Python + Chromium | Apache-2.0；浏览器另有各自许可 | Python 包可安装；浏览器二进制需单独下载并与包版本匹配，安装体积和进程内存显著高于 HTTP 抓取 | Python 包可导入，但 Chromium headless shell 下载未完成，报告为 `unavailable` | 保持 HTTP-only；不得因浏览器缺失阻塞请求 |
| 第二 HTML 抽取器 | BeautifulSoup 结构遍历 | MIT | 已是项目依赖，无新增安装体积；Windows 纯 Python 接口稳定 | 仓库 fixture 可恢复标题、段落、列表项和代码，共得到结构化候选块 | 回退 legacy trafilatura 纯文本；低质量时返回类型化状态 |
| HTML 基线 | trafilatura | Apache-2.0 | 已是项目依赖 | fixture 正文可抽取，但输出为纯文本，无法保留 block ID 与 heading path | 维持当前 DeepReader 行为直到 v2 切换 |
| PDF | TeX Live `pdftotext` | GPL-2.0-or-later，独立子进程 | 当前 Windows 开发环境已提供，无 Python 依赖；需进程超时和输出上限 | 本地 PDF 可执行，但只得到极少文本，扫描件需要单独 OCR 路径 | 失败或低文本密度时返回 `unsupported_document` / `low_quality`，不伪装全文 |
| PDF Python 候选 | pypdf | BSD-3-Clause | 纯 Python，安装较小，Windows 友好 | 当前环境未安装，未计入实测质量 | 仅在文本型 PDF 基准优于系统工具或部署可移植性有明确收益时引入 |
| PDF Python 候选 | PyMuPDF | AGPL / 商业双许可 | 预编译 wheel 较大，原生扩展和许可证审查成本更高 | 当前环境未安装 | 本项目当前许可边界下不作为默认候选 |

实测命令和机器可读结果位于 `tools/web_access_eval/spike.py` 与 `tools/web_access_eval/results/stage0-spike.json`。延迟仅用于发现数量级问题，不作为跨机器发布门槛。

## 决策

- 阶段 0 不新增生产依赖。第二 HTML 候选先使用现有 BeautifulSoup 做结构恢复对照，legacy trafilatura 作为纯文本基线。
- Playwright 保留为阶段 3 候选。生产接入前必须完成浏览器二进制可复现安装、进程池上限、导航 SSRF 复核、下载/弹窗禁用、全局超时和 lifespan 清理验证。
- PDF 先补充项目自有的文本型与扫描型 fixture，再比较 `pdftotext` 和 pypdf。扫描件应进入显式 OCR 能力，不把空文本解释为抓取成功。
- 不采用 PyMuPDF 作为默认依赖，除非后续许可评审和质量基准同时改变结论。

## 资源与安全边界

- 浏览器默认关闭，单请求页面数、并发、导航时间和响应体大小必须有硬上限；登录、付费墙和 CAPTCHA 不升级为绕过流程。
- 外部进程使用参数数组调用，不拼接 shell 字符串；设置超时，限制输入路径与输出大小，并在取消时终止子进程。
- HTML 与 PDF 内容始终视为不可信数据，不能把页面指令解释为工具调用或系统提示。

## 参考

- [Playwright 浏览器安装与版本匹配](https://playwright.dev/python/docs/browsers)
- [Playwright CI 与浏览器资源说明](https://playwright.dev/python/docs/ci)
- [pypdf 文本提取文档](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [PyMuPDF 安装与许可文档](https://pymupdf.readthedocs.io/)
