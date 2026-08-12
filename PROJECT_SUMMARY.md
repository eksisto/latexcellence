# LateXcellence — Project Summary

> 项目概要文件。项目结构变更时（新增/删除模块），必须同步更新此文件。

---

## 目录结构

```
latexcellence/
├── main.py                          # FastAPI 入口 + 静态文件挂载
├── config.json                      # 配置文件
├── config.example.json              # 配置模板
├── requirements.txt                 # Python 依赖
├── pytest.ini                       # pytest 配置
├── .pre-commit-config.yaml          # pre-commit 钩子配置
├── AGENTS.md                        # AI 编程助手规范
├── CLAUDE.md                        # Claude 项目说明
├── DEVELOPMENT.md                   # 开发文档索引（指向 docs/ 目录）
├── DESIGN.md                        # 设计文档
├── PROJECT_SUMMARY.md               # ← 本文件：项目概要（供 AI 快速读取）
├── README.md                        # 项目简介
├── THIRD_PARTY_NOTICES.md           # 第三方许可
├── clean-cache.sh / clean-cache.ps1 # 缓存清理脚本（Bash / PowerShell）
├── venv-shell.sh                   # 打开一个已激活 .venv 的交互式 bash
├── backend/
│   ├── core/
│   │   ├── config.py                # Pydantic 配置模型 + 热重载
│   │   ├── llm_client.py            # AsyncOpenAI 封装（流式 + 重试 + 限流 + Function Calling）
│   │   ├── logger.py                # TimedRotatingFileHandler 日志
│   │   ├── workspace_manager.py     # 多工作区管理（workspace_id → LatexProject）
│   │   ├── project_manager.py       # LaTeX 项目管理（文件树/段落/撤销）
│   │   ├── tool_registry.py         # Function Calling 工具注册表
│   │   ├── prompts.py               # 中央提示词管理模块（集中存储所有 LLM 提示词）
│   │   └── base_paragraph_check.py  # 逐段检查服务基类（typo/expression/code_review/terminology 共享）
│   ├── utils/
│   │   ├── json_utils.py            # LLM JSON 解析/校验/重试
│   │   ├── file_ops.py              # 原子写入/备份/MD5
│   │   ├── input_resolver.py        # \input/\include 递归解析
│   │   ├── time_utils.py            # 时间工具：当前日期/时间/星期格式化
│   │   ├── path_guard.py            # 项目路径安全校验唯一入口 resolve_project_path()
│   │   ├── tex_compile.py           # 统一 TeX 编译执行（texfot+latexmk、引擎自动检测、PDF/A）
│   │   ├── latex_parser.py          # LaTeX 源码↔纯文本转换（Marker 保护）
│   │   ├── latex_json_guard.py      # LaTeX×JSON 转义防护（内容校验器，跨模块共享）
│   │   ├── latex_literals.py        # LaTeX 字面内容识别（逐字命令/URL 参数，parser 与 guard 共享）
│   │   ├── latex_package_rules.py   # LaTeX 宏包规则表（命令/环境/列说明符 → 宏包映射）
│   │   ├── latex_package_analyzer.py # 宏包检测：确定性规则表为主，LLM 兜底
│   │   ├── compile_error_translator.py  # LaTeX 编译错误 LLM 翻译（compile + git_diff 复用）
│   │   ├── latex_formatter.py       # 统一 LaTeX 格式化工具（code_review / formula_ocr / table_ocr 共用）
│   │   ├── latexindent_utils.py     # latexindent.pl 调用封装（latex_formatter 使用）
│   │   ├── latexindent.yaml         # latexindent 统一缩进配置（每层 4 个空格）
│   │   ├── builtin_formatter.py     # 内置轻量 LaTeX 格式化器（latex_formatter 兜底）
│   │   ├── web_search/               # 联网搜索模块（多引擎抽象包）
│   │   │   ├── __init__.py           # 模块入口，向后兼容导出
│   │   │   ├── models.py             # SearchResult / SearchResponse 数据模型
│   │   │   ├── base.py               # BaseSearchEngine 抽象基类
│   │   │   ├── manager.py            # SearchManager + 全局单例 + 查询重写/Deep Read 编排
│   │   │   ├── deep_read.py          # DeepReader — 搜索结果页面正文抓取
│   │   │   ├── query_rewriter.py     # QueryRewriter — LLM 搜索查询重写
│   │   │   └── engines/              # 具体搜索引擎实现
│   │   │       ├── ddg.py            # DuckDuckGo HTML 引擎 + DDG Challenge 处理
│   │   │       ├── searxng.py        # SearXNG 引擎
│   │   │       ├── tavily.py         # Tavily Search API 引擎
│   │   │       └── mock.py           # Mock 引擎（测试/降级）
│   │   ├── scholarly_research/        # 学术研究工具（OpenAlex 检索、详情、引文追踪、OA 定位）
│   │   │   ├── __init__.py            # 包入口与生命周期 API
│   │   │   ├── models.py              # 统一作品、作者、主题和结果模型
│   │   │   ├── base.py                # 学术数据源抽象接口
│   │   │   ├── manager.py             # ResearchManager + OpenAlex + Function Calling 工具
│   │   │   ├── _utils.py              # 内部共享工具（DOI/URL 归一化等）
│   │   │   └── providers/             # 可插拔学术数据源
│   │   ├── project_tools/            # LLM Function Calling 工具集（浏览 + 编辑）
│   │   │   ├── __init__.py
│   │   │   ├── manager.py            # ProjectToolsManager — 浏览工具（list_files/read_file_content/get_section_structure/search_across_files）
│   │   │   └── edit_tools_manager.py # EditToolsManager — 段落编辑工具（edit_paragraph/undo_last_edit）
│   │   └── search_router.py         # 通用搜索 API 路由（/api/search/ddg-challenge）
│   └── modules/                     # 功能模块目录
│       ├── typo_check/              # 错字查找
│       ├── expression_opt/          # 表达优化
│       ├── terminology_check/       # 术语一致性
│       ├── paragraph_translate/     # 逐段翻译
│       ├── code_review/              # LaTeX 代码审查
│       ├── compile/                 # 自动编译
│       ├── git_diff/                # Git 差异高亮
│       ├── template_gen/            # 模板生成（自主 Agent）
│       ├── formula_ocr/             # 公式 OCR（含 visual_inline.py 视觉 inline/display 判定）
│       ├── table_ocr/               # 表格 OCR
│       ├── format_converter/        # 格式转换
│       ├── style_modify/            # 样式修改（粘贴代码 + 项目 Agent）
│       ├── writing_chat/            # 写作助手聊天
│       ├── bib_manager/             # 参考文献管理（格式规则层 + DOI 多数据源查询 + 重复检测 + 文献库管理）
│       │   └── providers/           # 可插拔 DOI 数据源
│       ├── plot_gen/                # 科研绘图（上传数据 -> 矢量图 + TikZ）
│       └── project_router.py        # 项目管理路由
│
├── frontend/
│   ├── package.json                 # npm 依赖与脚本
│   ├── vite.config.js               # Vite 配置
│   ├── index.html                   # 入口 HTML
│   ├── components.json              # shadcn/ui 组件配置
│   ├── jsconfig.json                # JS 路径别名配置
│   ├── public/                      # 静态资源（favicon）
│   ├── test/                        # 前端单元测试（formulaWrap / writingChatUtils）
│   └── src/
│       ├── main.jsx                 # React 入口
│       ├── App.jsx                  # 根路由
│       ├── index.css                # 全局样式（Tailwind + 代码块 CSS 变量）
│       ├── i18n/                    # i18n 多语言配置 + 翻译资源
│       │   ├── index.js             # i18next 初始化（含语言检测）
│       │   └── locales/             # 翻译 JSON 文件
│       ├── lib/                     # 通用前端工具
│       ├── pages/                   # 页面组件
│       │   ├── code-review/         # 代码审查子组件
│       │   └── writing-chat/        # 写作聊天子组件
│       ├── hooks/                   # 自定义 hook
│       │   ├── useParagraphCheck.js # 逐段检查统一流程 hook
│       │   ├── useWritingChat.js    # 写作聊天生命周期 hook
│       │   ├── usePlotGen.js        # 科研绘图 Agent hook
│       │   ├── writing-chat/        # 写作聊天子 hooks（sessions/assistants/captcha/sse/utils）
│       │   └── bib-manager/         # 参考文献管理子 hooks（format_analysis/metadata_extraction/bibtex_generation/bib_file_save/latex_generation）
│       ├── components/              # 共享组件 + ui 基座组件
│       ├── theme/                   # 主题 CSS + useThemeManager.js
│       ├── fonts/                   # 字体加载（文楷 / Noto Sans SC / JetBrains Mono / STIX Two Text）
│       ├── api/index.js             # 统一 API 层
│       ├── utils/                   # 前端工具（errorMessages.js 错误翻译 / formulaWrap.js 公式包裹重写 / highlight.js 高亮入口）
│       ├── styles/                  # 前端样式（code-highlight.css：高亮颜色独立文件，按需引入）
│       └── store/useStore.js        # Zustand 全局状态
│
├── showcase/                        # 独立展示页（零依赖静态站，详见 docs/showcase.md）
├── docs/                            # 开发文档
│   ├── development.md               # 项目概述/技术栈/启动
│   ├── backend-core.md              # 后端核心模块
│   ├── modules.md                   # 模块索引
│   ├── frontend.md                  # 前端架构
│   ├── testing.md                   # 测试规范
│   ├── latex-package-rules.md       # 宏包规则表维护指南
│   ├── duckduckgo-challenge.md      # DDG 验证码处理说明
│   ├── function-calling-tools.md    # Function Calling 工具体系说明
│   ├── modules/                     # 模块文档
│   └── showcase.md                  # 展示页（showcase）说明
├── tests/                           # 测试用例
│   ├── conftest.py                  # pytest 共享 fixture
│   ├── test_api/                    # 各模块 API 集成测试
│   ├── test_plot_gen/               # plot_gen 单元测试
│   ├── test_utils/                  # 后端工具单元测试（路径/JSON/宏包/搜索/学术检索/编译等）
│   └── fixtures/                    # 测试数据（宏包规则表、公式/表格 OCR、错字查找/表达优化语料等）
├── prompts/                         # 自定义提示词（expression/ 风格预设与 writing_chat/ 角色预设）
├── formats/                         # 参考文献格式定义
├── templates/                       # 生成的 LaTeX 模板（agent/<session_id>/ 沙箱产物）
├── writing_chat_sessions/           # 写作助手聊天持久化数据
├── plots/                           # plot_gen 绘图运行输出（运行时生成）
├── logs/                            # 运行时日志
├── tools/                         # 维护者工具（开发/评测/维护用脚本与 Agent 评测工作区，不进入运行时）
│   ├── eval_common.py             # 人工评估工具共享基座（语料加载/工作区/marker 还原/命中判定/CLI 骨架）
│   ├── generality_gate.py         # Agent 闭环优化通用性门禁（硬编码特征扫描/提示词体积监控/holdout 解析/对照组回归判定）
│   ├── tex_path.py                # 评测工具共享：TeX Live 路径自动发现（非交互 shell 也能定位 latexmk）
│   ├── latex_packages/            # 宏包规则表维护脚本（编译验证 + 覆盖率报告）
│   │   ├── verify_rules.py        # 离线编译验证（维护者专用，需 TeX）
│   │   ├── coverage_report.py     # 覆盖率报告（对照常用宏包清单）
│   │   └── common_packages.txt    # 常用宏包期望清单（覆盖率基准）
│   ├── formula_ocr_eval/          # 公式 OCR 人工评估工具（渲染语料 → 跑 OCR → 生成比对报告）
│   ├── table_ocr_eval/            # 表格 OCR 人工评估工具（渲染语料 → 跑 OCR → 生成比对报告）
│   ├── typo_check_eval/           # 错字查找人工评估工具（加载语料 → 跑检查 → 生成比对报告）
│   ├── expression_opt_eval/       # 表达优化人工评估工具（加载语料 → 跑优化 → 生成比对报告）
│   ├── terminology_check_eval/    # 术语一致性人工评估工具（加载多段术语语料 → 跑检查 → 生成比对报告）
│   ├── paragraph_translate_eval/  # 逐段翻译人工评估工具（加载语料 → 跑翻译 → 生成比对报告）
│   ├── prompt_comprehension_eval/ # Prompt 理解评测工具（逐一探测 prompts.py 提示词）
│   ├── plotgen_opt/               # plot_gen Agent 优化评测工作区（脚本/数据集/报告入库，results、previews 忽略）
│   ├── templategen_opt/           # template_gen Agent 优化评测工作区（脚本/场景定义/参考资料入库，results 忽略）
│   └── stylemodify_opt/           # style_modify Agent 优化评测工作区（脚本/场景定义/报告入库，results、runtime 忽略）
└── static/                        # Vite 构建产物
```

## Feature Modules Quick Reference

| Module | 目的 | 关键文件 | 依赖 LLM | Marker |
| --- | --- | --- | --- | --- |
| typo_check | 错字/拼写检查 | router.py, service.py | ✓ | ✓ |
| expression_opt | 学术表达优化 | router.py, service.py, prompt_manager.py | ✓ | ✓ |
| code_review | LaTeX 代码审查（工具格式化 + LLM 质量审查） | router.py, service.py | ✓ | ✗ |
| terminology_check | 术语一致性检查 | router.py, service.py, extractor.py, models.py | ✓ | ✓ |
| paragraph_translate | 逐段翻译 | router.py, service.py | ✓ | ✓ |
| compile | 编译 + 错误翻译 + 引擎自动检测 + PDF/A 支持 | router.py, service.py | ✓(错误翻译) | ✗ |
| git_diff | Git 版本差异 PDF | router.py, service.py | ✓(编译失败时翻译错误) | ✗ |
| template_gen | 模板生成（自主 Agent） | router.py, service.py, agent.py, trace.py, cleanup.py | ✓ | ✗ |
| formula_ocr | 图片→LaTeX 公式 | router.py, service.py, visual_inline.py | ✓(Vision) | ✗ |
| table_ocr | 图片→LaTeX 表格 | router.py, service.py | ✓(Vision) | ✗ |
| format_converter | 格式转换(pandoc) | router.py, service.py | ✓(宏包分析仅兜底) | ✗ |
| style_modify | 粘贴代码 / 项目 Agent 修改样式，支持 shadow 编译、proposal 审核、批量事务与撤销 | router.py, service.py, agent.py, models.py, session.py, trace.py, cleanup.py | ✓ | ✗ |
| writing_chat | 写作助手对话（含联网搜索、学术检索与引文追踪、项目浏览、段落编辑和工具确认） | router.py, service.py, context.py, models.py, prompt_manager.py | ✓ | ✗ |
| bib_manager | 参考文献管理 + 格式规则层 + DOI 多数据源查询 + 重复检测 + 文献库 | router.py, service.py, models.py, bib_parser.py, format_rules.py, style_registry.py, provider_manager.py, providers/ | ✓(格式规则命中不依赖 LLM；DOI 查询不依赖 LLM) | ✗ |
| plot_gen | 科研绘图 Agent（数据画像 -> 图表推荐 -> Python 执行 -> TikZ 编译） | router.py, service.py, agent.py, models.py, data_parser.py, recommender.py, styles.py, executor.py, tikzgen.py, sandbox_guard.py, trace.py, cleanup.py | ✓ | ✗ |

## 核心设计思路

- **集中式提示词管理**: 所有 LLM 提示词集中存储在 `backend/core/prompts.py` 中，按模块和功能分类。提供 `get_prompt(module, key, **kwargs)` 函数获取提示词，支持参数化模板。各模块通过 `from backend.core.prompts import get_prompt` 导入使用，便于统一维护和版本控制
- **LLM 输出语言注入**: 所有调用 LLM 的模块在 system prompt 中通过 `{language}` 占位符指定输出语言。前端 `getUiLanguage()` 将 i18n 语言代码（`"zh"`/`"en"`）转为完整名称（`"Chinese"`/`"English"`），通过 `language` 参数传入后端并注入 prompt 模板。无需额外的 `_general.language_context` 拼接
- **BaseParagraphCheckService**: 多个逐段检查模块（typo_check / expression_opt / code_review / terminology_check / paragraph_translate）共享的基类，封装文件解析、marker 替换、LLM 编排（批量+流式）、结果写回等流程。子类通过多个 hooks（\_get_system_prompt / \_build_user_content / \_get_model_cls / \_get_llm_params / \_get_batch_size / \_build_success_result）定制行为
- **WorkspaceManager**: 多浏览器多工作区隔离，每个 workspace_id 拥有独立的 `LatexProject` 实例（项目状态、文件树、MD5 缓存、撤销栈完全隔离）
- **乐观锁**: 多工作区并发编辑同一文件时，通过 MD5 哈希检测冲突。读取段落时返回 `file_md5`，应用修改时前端传回 `expected_md5`，后端校验失败时抛出 `FileConflictError`（HTTP 409），前端自动刷新 MD5 并提示用户重试。`inject_expected_md5s()` 辅助函数统一为各模块 router 注入预期 MD5
- **Marker Protection**: LaTeX 结构 → `__CMD_NNNN__` / `__MATH_NNNN__` / `__ENV_NNNN__` / `__VERB_NNNN__` marker → LLM 只改纯文本 → 按长度降序还原。apply 时若 marker_map 缺失（context 过期/重启/新操作覆盖），跳过该段写入并返回 `errors`，防止字面 marker 污染 `.tex` 文件；前端据此保留未应用项并提示用户重新检查。返回给前端展示的 `changes[]` 中的 `original`/`corrected` 会在 `_build_success_result` 中预先还原 marker，确保用户看到的是干净的 LaTeX 文本而非 `__CMD_NNNN__` 占位符
  - **code_review 例外**：code_review 的 LLM 建议只读展示、从不写回文件，直接审查真实格式化 LaTeX（无 marker），详见 docs/modules/code_review.md
- **统一代码展示与高亮**: 所有 LaTeX / TikZ / BibTeX 源码展示收敛到 `CodeBlock`（块级）与 `MarkerText`（行内/含 marker 段落，默认 `variant="code"`）两个组件，源码粘贴/编辑输入走 `CodeEditorInput`（高亮层 + 透明 textarea 叠加）；代码块 chrome 由 `index.css` 的 `--code-*` 变量与 `.code-block` / `.code-inline` 组件类控制，语法 token 颜色由 `styles/code-highlight.css`（独立文件，随 `CodeBlock` / `MarkerText` / `CodeEditorInput` 按需引入）的 `--code-token-*` 变量控制，各主题文件在亮暗两档覆盖代码块 chrome、也可覆盖 token 颜色，不引入官方高亮主题
- **宏包规则表检测**:
  - 宏包分析以确定性规则表（`latex_package_rules.py`：命令/环境/列说明符 → 宏包）为主路径，零 LLM 开销、结果可复现
  - 扫描前屏蔽逐字内容与 `%` 注释防误报，LaTeX 内核能力不收录；LLM 仅在规则表未命中时兜底，输出经 schema 校验 + 归一化（小写/去 `.sty`/去重/排序）
  - 支持 `domain` 参数（general/math/table）按领域排除无关宏包（如公式片段不带表格类宏包），并按 `\usepackage` / `\RequirePackage` 剔除已加载宏包避免整篇文档重复推荐。规则表有语料测试与数据完整性守卫，另有基于真实 TeX 编译的离线验证（`tools/latex_packages/verify_rules.py`）
- **JSON Validation Pipeline**:
  - `safe_parse_json()` → Pydantic `.model_validate()` → `retry_with_validation()` (最多 3 次重试 + 错误反馈)
  - `latex_json_guard.py` 提供字段级 LaTeX 内容校验（`LatexText` / `check_latex_text()`），拦截 JSON 反斜杠转义静默损坏
  - `latex_literals.py` 统一逐字命令（`\verb`/`\Verb`/`\lstinline`/`\mintinline`）、逐字环境与 URL 参数的识别，
    latex_parser（Marker 化）、latex_json_guard（剥离校验）与分隔符协议提取器（跳过逐字内容中的 `<<<END>>>`）共用同一策略
  - 分隔符协议（非 JSON）路径使用 `relaxed=True` 宽松模式（放行 `\t` / `\r` 合法 LaTeX 空白，错误归因不再提示 JSON 转义）
  - marker 模块（typo_check / expression_opt / paragraph_translate）输出字段挂 `LatexTextLite` 轻量校验（只拦控制字符与垃圾占位符，不查配平，避免残余结构误报），`base_paragraph_check` 在模型校验后另有轻量兜底
  - 逐字类内容（`\verb`/`\Verb`/`\lstinline`/`\mintinline`/`alltt` 等）与 URL 参数按字面剥离（含嵌套花括号），错误归因按类型分诊（控制字符 / 配平 / 占位符）
- **SSE Streaming**: 所有段落检查模块通过 Server-Sent Events 推送 `progress`/`result`/`error`/`done` 事件
- **Function Calling 工具体系**
  - `features.function_calling` 配置组将全局 LLM 工具归入嵌套结构（替代原先扁平的 `features.function_calling.web_search` / `features.project_tools`），支持旧配置自动迁移
  - `ToolRegistry`（工具注册表）管理所有可用工具的 schema 和执行函数，基于 `config_path` 动态检查功能开关
  - 自注册模式：每个工具管理器在首次初始化时自动注册自身工具
  - `SearchManager`（搜索管理器）提供 `web_search` / `open_page` / `find_in_page` 三个工具的 schema 和执行：`web_search` 统一封装 `DuckDuckGoHtmlEngine`（模拟浏览器请求，遇 HTTP 202 Challenge 时抛出 `DDGChallengeError`，由 service 捕获并以 `captcha_challenge` SSE 事件推送到前端）、`SearXNGEngine`（通过 `/search?format=json` 获取结果，无验证码）和 `TavilyEngine`（Tavily Search API，`search_depth="advanced"` + `include_answer` + `include_raw_content`，需 API Key，建议主引擎）；`open_page` 按需抓取指定 URL 正文，`find_in_page` 页内定位关键词，模拟 Codex 的 agentic 检索模式
  - `ProjectToolsManager`（项目工具管理器）提供 4 个浏览工具（`list_files` / `read_file_content` / `get_section_structure` / `search_across_files`），允许 LLM 浏览项目文件和目录结构
  - `EditToolsManager`（段落编辑工具管理器）提供 2 个编辑工具（`edit_paragraph` / `undo_last_edit`），允许 LLM 直接修改 .tex 文件段落并支持撤销，敏感操作需前端用户确认
  - `ResearchManager` 提供基于 OpenAlex 的论文检索、详情查询、引文追踪、摘要/OA 定位和研究数据资源检索；统一映射为可追溯、可去重的 `ScholarlyWork`，共享 HTTP 客户端由 FastAPI lifespan 管理
  - DDG Challenge 验证码提交通过通用路由 `/api/search/ddg-challenge` 处理，不绑定特定业务模块
  - 搜索流程可串联 **Query Rewriting**（将用户查询拆解为多条并行查询，去重合并，合并上限 15 条）和 **Deep Read**（可配置自动抓取前 N 条，默认关闭；全局共享 `DeepReader`，带 5 分钟 TTL 缓存；仅允许公网 HTTPS，拦截私网/环回/保留地址防 SSRF）
  - 搜索结果与页面正文经 `format_for_llm()` 统一以 `UNTRUSTED DATA` 包装头注入并按字符预算截断，防 prompt injection 与上下文膨胀；Writing Chat 落盘前校验回复链接是否来自当轮工具真实来源，未验证链接通过 `citation_note` + `citation_note` SSE 事件警示前端
  - Writing Chat 工具调用循环上限 6 轮，工具证据按 60000 字符滚动预算裁剪最旧工具调用对，支持"搜索 → 读页 → 再搜索"的迭代检索
  - Writing Chat 的 `send()` 方法作为 Function Calling 工具的消费端，集成联网搜索、学术研究、项目浏览和段落编辑工具：检测 LLM 返回 `tool_call` 时自动执行、将结果回传 LLM，再流式输出最终回复。学术检索结果通过 `tool_result.scholarly_results` 推送前端；敏感写操作推送 `tool_pending` 等待确认
  - RS2 生成编辑走分隔符协议（`<<<LATEX>>>...<<<END>>>` 纯文本块 + 可选 `<<<REASON>>>...<<<END>>>`），不再经 JSON 传输，避免反斜杠转义静默损坏；`original` 由后端从段落原文填充（不再要求 LLM 回显），提取出的修改段落经 `validate_latex_field(relaxed=True)` 宽松校验，失败回喂重试（最多 3 次）
- **DOI 多数据源查询**: `ProviderManager`（全局单例）管理多个可插拔 `BaseMetadataProvider`
  - Crossref 主索引 + arXiv 预印本专用 + 补全层
  - arXiv DOI（`10.48550/arXiv.*`）自动路由到 ArxivProvider
  - 普通 DOI 先查 Crossref 成功后用补全层合并空字段（`_merge()`），失败则串行降级
  - 配置通过 `config.json → features.bib_manager.providers` 独立开关每个数据源
- **bib_manager 格式识别规则层**: `format_rules.py` 以确定性规则（文献类型标识、期刊缩写、DOI 前缀、版式特征）优先判定 14 种内置格式，命中即返回 `high` 置信度且不调用 LLM；未命中时 LLM 兜底（提示词内置各格式规范样例）。规则语料离线测试保证可复现，新增格式必须同步补语料
- **HTTP 全局配置**: `AppConfig.http.user_agent` 统一管理所有外部 HTTP 请求的 User-Agent，供 web_search 引擎和 bib_manager Provider 共用
- **模板生成自主 Agent**: 分层为 `service.py`（`TemplateGenService` 创建沙箱并编排）+ `agent.py`（`TemplateAgentService` Function Calling 主循环 + `_ToolExecutor` 工具执行），实现"写文件 → 编译验证 → 读日志 → 修错 → 再编译"的自主循环，产出完整可编译的多文件 LaTeX 工程。
  - 工具（`write_file`/`read_file`/`list_files`/`compile_latex`/`finish`）为 session 级私有，schema 由 `TemplateAgentService._build_tools_schema` 动态构建并经 `chat_with_tools` 注入，不注册到全局 `ToolRegistry` 避免污染其他模块工具集。
  - 每次生成在 `output_dir/agent/<session_id>/` 创建独立沙箱，路径经 `_resolve_sandbox_path` 校验（禁绝对路径 / `..` / 非白名单扩展名 / 越界）。SSE 推送 `plan`/`tool_call`/`tool_result`/`compile`（含结构化 `errors` 列表）/`warning`（编译未过却 finish）/`done`/`error` 事件。
  - 编译失败时 `_extract_errors` 从日志中提取类型/文件/行号，`_format_error_analysis` 拼接聚焦摘要回传 LLM；prompt 中 COMPILATION ERROR RECOVERY PROTOCOL 强制 LLM 走 ANALYZE→READ→FIX→RECOMPILE 循环，禁止编译未过时调用 finish。
  - 每次生成的完整交互由 `trace.py` 按时间顺序追加到沙箱 `trace.jsonl`（`run_start`/`llm`/`tool_result`/`warning`/`error`/`run_end` 事件，含完整工具参数与回传 LLM 的结果），通过 `/api/template-gen/trace` 查看或 `/trace/download` 导出 JSON，用于分析与改进 Agent。
  - 沙箱自动清理由 `cleanup.py` 实现：`cleanup_expired_sandboxes()` 按目录内最新文件 mtime 与 `cleanup_retention_days` 比较判定过期，`cleanup_loop()` 作为 `asyncio` 后台任务在 FastAPI lifespan 中启动并按 `cleanup_interval_seconds` 周期扫描，删除过期目录，服务关闭时优雅取消。
- **科研绘图**: `PlotGenAgent` 基于 Function Calling 实现"数据画像 → 图表方案 → 写脚本 → Python 沙箱执行 → 错误回修 → TikZ 编译 → finish"的自主循环，支持上传 CSV/TSV/Excel/JSON 数据并输出 PDF/SVG/TikZ。
  - 数据文件只进入 session 沙箱 `data/`，绘图脚本通过 `python_sandbox` 在 `plots/<session_id>/` 内运行；风格由 `styles.py` 后端注入，不依赖 LLM 手写样式。
  - `python_sandbox` 执行前由 `sandbox_guard.py` 注入 `sys.addaudithook()` 审计钩子作为安全监听器：拦截读沙箱外文件、子进程/命令执行、网络访问、删除/修改系统文件与 ctypes 危险用法（进程句柄加载、按路径加载库、危险符号解析），并将 `TMPDIR`/`TEMP`/`TMP` 与 `MPLCONFIGDIR` 重定向进沙箱，避免正常绘图流程被误拦；属 Python 层纵深防御而非操作系统级沙箱。
  - TikZ 源由 `tikzgen.py` **受控确定性生成**（替代 matplot2tikz）：Agent 脚本只写 `artifacts/data_*.csv` + 声明式 `artifacts/tikz_spec.json`，`python_sandbox` 成功后后端经 Pydantic v2 校验（含 CSV 列/数值/热力图整数索引校验、误差列空值/对数轴非正坐标/柱状图缺柱与标签错位等静默保真问题拦截）确定性渲染 `artifacts/figure.tex`；支持 line/scatter/bar/box/heatmap/histogram/kde（violin 回退 boxplot），热力图内置 RdBu_r/RdBu 色标与显著性标注，误差棒需 `y error plus/minus` 列 + addplot 启用 `error bars/.cd, y dir=both, y explicit` 才渲染。
  - 工具集为 session 私有：`write_file`/`read_file`/`list_files`/`python_sandbox`/`compile_tikz`/`stats_test`/`list_styles`/`apply_style`/`finish`，不注册到全局 `ToolRegistry`。
  - SSE 推送 `plan`/`data_profile`/`chart_plan`/`tool_call`/`tool_result`/`execution`/`tikz`/`warning`/`done`/`error` 事件；会话元数据和 messages 持久化在 `meta.json`，支持刷新后自然语言微调。
  - 每次运行（生成/微调）的完整交互由 `trace.py` 按时间顺序追加到沙箱 `trace.jsonl`（`run_start`/`llm`/`tool_result`/`warning`/`error`/`run_end` 事件，含完整工具参数与回传 LLM 的结果），通过 `/api/plot-gen/sessions/{session_id}/trace` 查看或 `/trace/download` 导出 JSON，用于分析与改进 Agent。
- **style_modify 项目 Agent**: 在真实项目只读前提下执行 Function Calling 循环，候选文件写入会话 overlay，在 shadow 项目编译，`finish` 生成不可变 proposal revision。
  - 私有工具为 `list_project_files` / `search_project_text` / `read_project_file` / `stage_replace` / `discard_file_changes` / `compile_staged_project` / `finish`，不注册到全局 `ToolRegistry`；路径统一走 `resolve_project_path()`，默认允许 `.tex/.sty/.cls/.bib`。
  - 会话目录包含 `baseline/`、`overlay/`、`shadow/`、`meta.json` 和 `trace.jsonl`；真实项目在确认前 MD5 不变。SSE 推送 `session`/`plan`/`tool_call`/`tool_result`/`file_staged`/`compile`/`proposal`/`warning`/`done`/`error` 事件。
  - 应用通过 `LatexProject.apply_whole_files()` 公开批量事务写回，写入前建立 operation 备份；撤销前校验 applied MD5，拒绝覆盖应用后的新编辑。
  - 优化闭环：`tools/stylemodify_opt/` 提供 5 个可复现样式场景，自动驱动 Agent 并核验 proposal 需求遵守 / 内容保留 / 事务应用撤销，供提示词与 Agent 逻辑离线迭代。
- **useParagraphCheck Hook**: TypoCheck/ExpressionOpt/CodeReview/TerminologyCheck/ParagraphTranslate 多页共享同一 hook 封装流程
- **useEnabledFeatures Hook**: 从后端 `/api/config/status` 加载功能开关到 Zustand store，幂等（已加载则跳过）。WelcomePage 和 WorkspacePage 均调用此 hook，确保直接刷新任一页面时功能开关均可用
- **Zustand Persist**: `partialize` 选择性持久化检查结果，支持刷新保留
- **Multi-Theme**: `theme/` 目录下每个 CSS 文件定义一组完整配色（`[data-theme="xxx"]` light + dark），通过 `import.meta.glob` 自动发现，用户可在设置或顶栏下拉菜单中实时切换
- **CheckOperationContext**: 每次 preview/check 生成唯一 `operation_id`，将 marker 映射和 MD5 快照深拷贝存入上下文（TTL 1 小时），apply 时通过 operation_id 精确还原。若 context 丢失（过期/重启/新操作覆盖），apply_changes 逐段检测 marker_map，缺失时跳过写入并返回 `errors` 列表，前端据此保留结果项并提示重新检查
- **useUndoRedo Hook**: 基于后端每文件独立 undo/redo deque（最大 50 层）的撤销/重做状态管理，与 project_manager 同步
- **useResultFilter Hook**: 检查结果排序与过滤逻辑封装——changes 非空优先、无建议在后、同类按 paragraph_index 升序
- **useWritingChat Hook**: 写作助手聊天的完整生命周期管理（消息流式、thinking 标签解析、上下文压缩、会话持久化、工具调用确认）
- **MarkerText / ParagraphContext 组件**: `MarkerText` 将含 marker 的文本渲染为友好标签（如 `__MATH_0000__` → 公式图标），默认以行内代码样式渲染纯文本片段并做 LaTeX 高亮；`ParagraphContext` 展示当前段落前后文，用于逐段检查结果页面
- **i18n 多语言**: 基于 `i18next` + `react-i18next` + `i18next-browser-languagedetector`，支持中文/英文双语。翻译文件按命名空间拆分（`common` / `pages` / `components` / `hooks`），组件通过 `useTranslation()` hook 或 `i18n.t()` 调用。语言选择持久化到 localStorage，用户可在设置弹窗中切换
