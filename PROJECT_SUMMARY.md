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
├── DEVELOPMENT.md                   # 开发文档索引（指向 docs/ 目录）
├── DESIGN.md                        # 设计文档
├── PROJECT_SUMMARY.md               # ← 本文件：项目概要（供 AI 快速读取）
├── README.md                        # 项目简介
├── THIRD_PARTY_NOTICES.md           # 第三方许可
├── clean-cache.sh / clean-cache.ps1 # 缓存清理脚本（Bash / PowerShell）
├── venv-shell.sh                   # 打开一个已激活 .venv 的交互式 bash
├── backend/
│   ├── manage.py                    # 多用户模式 CLI 管理工具（python -m backend.manage）
│   ├── core/
│   │   ├── config.py                # Pydantic 配置模型 + 热重载
│   │   ├── llm_client.py            # AsyncOpenAI 封装（流式 + 重试 + 限流 + Function Calling）
│   │   ├── security_middleware.py   # CORS 白名单 + 共享访问令牌中间件（多用户模式：会话 + CSRF）
│   │   ├── logger.py                # TimedRotatingFileHandler 日志
│   │   ├── workspace_manager.py     # 多工作区管理（workspace_id → LatexProject）
│   │   ├── project_manager.py       # LaTeX 项目管理（文件树/段落/撤销）
│   │   ├── tool_registry.py         # Function Calling 工具注册表
│   │   ├── prompts.py               # 中央提示词管理模块（集中存储所有 LLM 提示词）
│   │   ├── base_paragraph_check.py  # 逐段检查服务基类（typo/expression/code_review/terminology 共享）
│   │   └── multiuser/               # 多用户模式基础设施包（db/userspace/auth/dependencies）
│   ├── utils/
│   │   ├── json_utils.py            # LLM JSON 解析/校验/重试
│   │   ├── file_ops.py              # 原子写入/备份/MD5
│   │   ├── agent_file_tools.py      # Agent 私有文件工具共享 schema、调度与 line_range 读取
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
│   │   │   ├── models.py             # legacy 搜索响应 + v2 搜索候选/引擎状态契约
│   │   │   ├── planner.py            # 结构化查询计划契约 + 校验重试 + 规则回退
│   │   │   ├── ranking.py            # 多引擎候选合并、RRF 与域名多样性
│   │   │   ├── url_utils.py           # URL 安全规范化、跟踪参数清理与来源分类
│   │   │   ├── errors.py             # v2 类型化抓取失败与产物契约
│   │   │   ├── base.py               # BaseSearchEngine 抽象基类
│   │   │   ├── manager.py            # SearchManager + legacy/聚合策略 + 截止时间/熔断编排
│   │   │   ├── deep_read.py          # DeepReader — 搜索结果页面正文抓取
│   │   │   ├── page_reader.py         # v2 结构化读页、抽取缓存与证据编排
│   │   │   ├── query_rewriter.py     # QueryRewriter — LLM 搜索查询重写
│   │   │   ├── fetch/                # v2 抓取策略、条件请求与原始响应缓存
│   │   │   │   ├── browser.py       # 可选 Playwright 回退池、健康状态与资源隔离
│   │   │   │   ├── detector.py      # 页面阻断/挑战/付费墙/JS shell 分类
│   │   │   │   ├── scheduler.py     # 域名并发、请求间隔与熔断调度
│   │   │   │   ├── coordinator.py   # 抓取协调器
│   │   │   │   ├── cache.py         # 原始响应缓存
│   │   │   │   └── policy.py        # 抓取策略（条件请求）
│   │   │   ├── extract/              # v2 MIME 路由、多候选抽取、质量评分、切块与证据选择
│   │   │   └── engines/              # 具体搜索引擎实现
│   │   │       ├── ddg.py            # DuckDuckGo HTML 引擎 + DDG Challenge 处理
│   │   │       ├── searxng.py        # SearXNG 引擎
│   │   │       ├── tavily.py         # Tavily Search API 引擎
│   │   │       └── mock.py           # Mock 引擎（测试/降级）
│   │   ├── scholarly_research/        # 学术研究工具（多 Provider 联邦检索、详情、引文追踪、OA 定位）
│   │   │   ├── __init__.py            # 包入口与生命周期 API
│   │   │   ├── models.py              # 统一作品、作者、主题和结果模型
│   │   │   ├── planner.py             # v2 学术查询计划契约（尚未接入运行时）
│   │   │   ├── base.py                # 学术数据源抽象接口
│   │   │   ├── manager.py             # ResearchManager + Provider 编排 + Function Calling 工具
│   │   │   ├── _utils.py              # 内部共享工具（DOI/URL 归一化等）
│   │   │   └── providers/             # 可插拔学术数据源
│   │   ├── tool_result_protocol.py  # 工具结果统一协议（shown/total/truncated/next_step 状态块）
│   │   ├── project_tools/            # LLM Function Calling 工具集（浏览 + 编辑）
│   │   │   ├── __init__.py
│   │   │   ├── manager.py            # ProjectToolsManager — 浏览工具（list_files/read_file_content/get_section_structure/search_across_files）
│   │   │   └── edit_tools_manager.py # EditToolsManager — 段落编辑工具（edit_paragraph/undo_last_edit）
│   │   └── search_router.py         # 通用搜索 API 路由（/api/search/ddg-challenge）
│   └── modules/                     # 功能模块目录
│       ├── auth/                    # 多用户认证与管理路由（/api/auth、/api/admin）
│       ├── typo_check/              # 错字查找
│       ├── expression_opt/          # 表达优化
│       ├── terminology_check/       # 术语一致性
│       ├── paragraph_translate/     # 逐段翻译
│       ├── code_review/              # LaTeX 代码审查
│       ├── compile/                 # 自动编译
│       ├── git_diff/                # Git 差异高亮
│       │   └── service/             # 服务包（models/constants/process/revisions/flatten/diff/render）
│       ├── template_gen/            # 模板生成（自主 Agent）
│       │   └── agent/               # Agent 包（service/executor/validation/compile/sandbox/loop/constants）
│       ├── formula_ocr/             # 公式 OCR（含 visual_inline.py 视觉 inline/display 判定）
│       ├── table_ocr/               # 表格 OCR
│       ├── format_converter/        # 格式转换
│       ├── style_modify/            # 样式修改（粘贴代码 + 项目 Agent）
│       ├── writing_chat/            # 写作助手聊天
│       │   └── service/             # 业务逻辑包（state/sessions/conversation/edits/compaction/_deps）
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
│       │   ├── language.js          # 语言代码归一化 + 后端语言名映射
│       │   └── locales/             # 翻译 JSON 文件
│       ├── lib/                     # 通用前端工具
│       ├── pages/                   # 页面组件
│       │   ├── code-review/         # 代码审查子组件
│       │   └── writing-chat/        # 写作聊天子组件
│       ├── hooks/                   # 自定义 hook
│       │   ├── useParagraphCheck.js # 逐段检查统一流程 hook
│       │   ├── useWritingChat.js    # 写作聊天生命周期 hook
│       │   ├── usePlotGen.js        # 科研绘图 Agent hook
│       │   ├── useStyleModifyAgent.js # 样式修改项目 Agent hook
│       │   ├── useEnabledFeatures.js # 功能开关加载 hook（幂等）
│       │   ├── useUndoRedo.js        # 撤销/重做状态 hook
│       │   ├── useResultFilter.js    # 检查结果排序过滤 hook
│       │   ├── useTheme.js           # 亮/暗主题切换 hook
│       │   ├── useFavicon.js         # 页面图标 hook
│       │   ├── writing-chat/        # 写作聊天子 hooks（sessions/assistants/captcha/sse/utils）
│       │   └── bib-manager/         # 参考文献管理子 hooks（format_analysis/metadata_extraction/bibtex_generation/bib_file_save/latex_generation）
│       ├── components/              # 共享组件 + ui 基座组件
│       │   └── settings/            # 设置对话框分区配置组件
│       ├── theme/                   # 主题 CSS + useThemeManager.js
│       ├── fonts/                   # 字体加载（dev 本地 npm 包 / build 走 cdnjs）
│       ├── api/index.js             # 统一 API 层（接口分组封装）
│       ├── api/connection.js        # 后端连接配置（基地址/令牌/认证头/连接测试）
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
│   ├── templategen-optimization-lessons.md  # template_gen 闭环优化复盘与通用性门禁
│   ├── plotgen-optimization-lessons.md      # plot_gen 闭环优化复盘与最佳实践
│   ├── prompt-optimization-plan.md          # 提示词优化计划
│   ├── windows-temp-permissions.md          # Windows 沙箱临时目录权限排查（pytest basetemp/ACL/清理）
│   ├── web-access-operations.md              # 网络访问灰度、观测、容量与回滚运维
│   ├── multi-user-plan.md                    # 多用户模式设计方案（双模式/认证/用户空间/配额/权限协作/管理后台/迁移）
│   ├── modules/                     # 模块文档
│   └── showcase.md                  # 展示页（showcase）说明
├── tests/                           # 测试用例
│   ├── conftest.py                  # pytest 共享 fixture
│   ├── test_api/                    # 各模块 API 集成测试
│   ├── test_plot_gen/               # plot_gen 单元测试
│   ├── test_utils/                  # 后端工具单元测试（路径/JSON/宏包/搜索/学术检索/编译等）
│   └── fixtures/                    # 测试数据（宏包规则表、公式/表格 OCR、错字查找/表达优化语料等）
├── data/                            # 应用运行期与用户数据统一目录
│   ├── latexcellence.db             # 多用户模式 SQLite 数据库（运行时生成）
│   ├── users/                       # 多用户模式用户空间（<user_id>/ 下 projects 与衍生数据目录）
│   ├── staging/                     # 多用户模式上传解压暂存区（运行时生成）
│   ├── formats/                     # 参考文献格式定义
│   ├── plots/                       # plot_gen 绘图运行输出（运行时生成）
│   ├── prompts/                     # 自定义提示词（expression/ 风格预设与 writing_chat/ 角色预设）
│   ├── style_modify_sessions/       # style_modify Agent 会话缓存
│   ├── templates/                   # 生成的 LaTeX 模板（<session_id>/ 沙箱产物）
│   ├── writing_chat_sessions/       # 写作助手聊天持久化数据
│   └── web_cache/                   # 结构化网页原始/抽取缓存（运行时生成，可清理）
├── logs/                            # 运行时日志
├── tools/                         # 维护者工具（开发/评测/维护用脚本与 Agent 评测工作区，不进入运行时）
│   ├── eval_common.py             # 人工评估工具共享基座（语料加载/工作区/marker 还原/命中判定/CLI 骨架）
│   ├── web_access_eval/            # Writing Chat 网络访问评测（离线重放、legacy 手动采集、阶段 6 容量/回滚检查）
│   ├── visual_review_api.py       # 视觉评审工具的 Responses API 请求适配
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
│   ├── prompt_comprehension_eval/ # Prompt 理解评测工具（逐一探测 + 历史追踪 + 回归检测）
│   ├── plotgen_opt/               # plot_gen Agent 优化评测工作区（脚本/数据集/报告入库，results、previews 忽略）
│   ├── templategen_opt/           # template_gen Agent 优化评测工作区（脚本/场景定义/参考资料入库，results 忽略）
│   └── stylemodify_opt/           # style_modify Agent 优化评测工作区（脚本/场景定义/报告入库，results、runtime 忽略）
└── static/                        # Vite 构建产物
```

## Feature Modules Quick Reference

| Module | 目的 | 关键文件 | 依赖 LLM | Marker |
| --- | --- | --- | --- | --- |
| auth | 多用户认证与管理（注册/审批/登录/会话/管理员路由） | router.py, service.py, core/multiuser/* | ✗ | ✗ |
| typo_check | 错字/拼写检查 | router.py, service.py | ✓ | ✓ |
| expression_opt | 学术表达优化 | router.py, service.py, prompt_manager.py | ✓ | ✓ |
| code_review | LaTeX 代码审查（工具格式化 + LLM 质量审查） | router.py, service.py | ✓ | ✗ |
| terminology_check | 术语一致性检查 | router.py, service.py, extractor.py, models.py | ✓ | ✓ |
| paragraph_translate | 逐段翻译 | router.py, service.py | ✓ | ✓ |
| compile | 编译 + 错误翻译 + 引擎自动检测 + PDF/A 支持 | router.py, service.py | ✓(错误翻译) | ✗ |
| git_diff | Git 版本差异 PDF | router.py, service/ | ✓(编译失败时翻译错误) | ✗ |
| template_gen | 模板生成（自主 Agent） | router.py, service.py, agent/, trace.py, cleanup.py | ✓ | ✗ |
| formula_ocr | 图片→LaTeX 公式 | router.py, service.py, visual_inline.py | ✓(Vision) | ✗ |
| table_ocr | 图片→LaTeX 表格 | router.py, service.py | ✓(Vision) | ✗ |
| format_converter | 格式转换(pandoc) | router.py, service.py | ✓(宏包分析仅兜底) | ✗ |
| style_modify | 粘贴代码 / 项目 Agent 修改样式，支持 shadow 编译、proposal 审核、批量事务与撤销 | router.py, service.py, agent.py, models.py, session.py, trace.py, cleanup.py | ✓ | ✗ |
| writing_chat | 写作助手对话（含联网搜索、学术检索与引文追踪、项目浏览、段落编辑和工具确认） | router.py, service/, context.py, models.py, prompt_manager.py | ✓ | ✗ |
| bib_manager | 参考文献管理 + 格式规则层 + DOI 多数据源查询 + 重复检测 + 文献库 | router.py, service.py, models.py, bib_parser.py, format_rules.py, style_registry.py, provider_manager.py, providers/ | ✓(格式规则命中不依赖 LLM；DOI 查询不依赖 LLM) | ✗ |
| plot_gen | 科研绘图 Agent（数据画像 -> 图表推荐 -> Python 执行 -> TikZ 编译） | router.py, service.py, agent.py, models.py, data_parser.py, recommender.py, styles.py, executor.py, tikzgen.py, sandbox_guard.py, trace.py, cleanup.py | ✓ | ✗ |

## 核心设计思路

> 本节只列关键概念的一句话说明与文档入口；完整设计见 [DESIGN.md](DESIGN.md)，开发细节见 [docs/](docs/)。

| 设计要点 | 一句话说明 | 详细文档 |
| --- | --- | --- |
| Marker 保护 | LaTeX 结构替换为 `__CMD_NNNN__` / `__MATH_NNNN__` 等 marker，LLM 只能修改纯文本；还原按创建顺序（counter）降序，非标准 marker 按长度降序；code_review 例外（建议只读展示，无 marker） | [DESIGN.md §2](DESIGN.md)、[backend-core.md](docs/backend-core.md) |
| JSON 输出校验流水线 | `safe_parse_json()` 容错解析 → Pydantic `model_validate()` 严格校验 → 失败反馈 LLM 重试（最多 3 次） | [DESIGN.md §3](DESIGN.md) |
| LaTeX×JSON 转义防护 | `latex_json_guard.py` 拦截控制字符、`[object Object]` 占位符、花括号 / `$` / 环境配平；分隔符协议等非 JSON 路径用 `relaxed=True` | [backend-core.md](docs/backend-core.md) |
| 分隔符协议 | OCR、style_modify、writing_chat 编辑输出用 `<<<LATEX>>>...<<<END>>>` 直接传输纯文本，从根上避免 JSON 转义损坏 | [formula_ocr.md](docs/modules/formula_ocr.md) 等模块文档 |
| 集中式提示词管理 | 所有 LLM 提示词集中存放在 `backend/core/prompts.py`，`get_prompt()` 参数化获取，`{language}` 占位符注入输出语言 | [backend-core.md](docs/backend-core.md) |
| BaseParagraphCheckService | 多个逐段检查模块共享的基类，封装文件解析、marker 替换、LLM 编排与写回，子类通过 hooks 定制 | [backend-core.md](docs/backend-core.md) |
| CheckOperationContext | 每次 preview/check 生成唯一 `operation_id`，保存 marker 映射与 MD5 快照（TTL 1 小时），apply 时精确还原，防止 marker 污染源文件 | [DESIGN.md §1.4](DESIGN.md) |
| 乐观锁 | 读取时返回文件 MD5，写回时校验；不一致抛 `FileConflictError`（HTTP 409），前端刷新 MD5 后重试 | [DESIGN.md §4.2](DESIGN.md) |
| SSE 流式 | 段落检查、Agent 运行、写作助手均通过 SSE 推送 `progress` / `result` / `error` / `done` 等事件 | [modules.md](docs/modules.md) |
| Function Calling 工具体系 | `ToolRegistry` 统一注册与管理工具开关；SearchManager / ProjectToolsManager / EditToolsManager / ResearchManager 自注册；template_gen / plot_gen / style_modify 使用 session 私有工具 | [function-calling-tools.md](docs/function-calling-tools.md) |
| 工具结果统一协议 | list_files / read_file_content / search_across_files 与学术检索工具在结果末尾追加 `shown` / `total` / `truncated` / `next_step` 状态块，指导模型在结果被截断时缩小范围、继续读取或换用工具 | [backend-core.md](docs/backend-core.md) |
| 通用搜索聚合 | `SearchQueryPlan` 经统一 JSON 校验重试生成；`SearchManager` 可并发查询多个引擎，按 URL/近重复合并后使用 RRF、来源加权和域名上限排序，并保留 legacy 回滚路径 | [backend-core.md](docs/backend-core.md) |
| 宏包规则表检测 | 确定性规则表（命令/环境/列说明符 → 宏包）优先，零 LLM 开销；规则表未命中才 LLM 兜底 | [latex-package-rules.md](docs/latex-package-rules.md) |
| 多文件 \input 展开 | 递归解析 `\input` / `\include` / `\subfile`，段落索引为文件内局部索引，结果按文件分组展示与写回 | [backend-core.md](docs/backend-core.md) |
| 编译与错误翻译 | `tex_compile.py` 统一编译执行（texfot + 引擎自动检测 + PDF/A），`compile_error_translator.py` 把失败日志翻译为修复建议 | [compile.md](docs/modules/compile.md) |
| DOI 多数据源查询 | Crossref 主索引 + arXiv 专用路由 + 补全层，`ProviderManager` 全局单例管理可插拔数据源 | [bib_manager.md](docs/modules/bib_manager.md) |
| 自主 Agent | template_gen / plot_gen / style_modify 项目 Agent 基于 Function Calling 自主规划、执行、验证、自修，`trace.jsonl` 全程回放；私有文件工具复用 `agent_file_tools.py` 的 schema、调度与范围读取契约 | [function-calling-tools.md](docs/function-calling-tools.md)、[template_gen.md](docs/modules/template_gen.md) 等模块文档 |
| 代码展示与高亮 | CodeBlock / MarkerText / CodeEditorInput 统一 LaTeX 源码展示，token 颜色由 `--code-token-*` 变量控制 | [frontend.md](docs/frontend.md) |
| 逐段检查前端流程 | `useParagraphCheck` 统一封装恢复、预览、检查、排序、应用与持久化；`useResultFilter` 负责排序过滤 | [frontend.md](docs/frontend.md) |
| 写作助手 | `useWritingChat` 管理会话、SSE 流式、工具确认、编辑建议双轮协议 | [writing_chat.md](docs/modules/writing_chat.md) |
| 主题系统 | 主题 CSS 自动发现，亮/暗模式独立；`--primary` / `--accent` 等由单一色相派生 | [frontend.md](docs/frontend.md) |
| 多用户双模式 | `multi_user.enabled` 开关；单用户保持现状（本地目录 + 共享令牌），多用户强制会话认证、CSRF、禁用任意路径打开/浏览 | [multi-user-plan.md](docs/multi-user-plan.md) |
| 多用户认证 | argon2id 密码哈希、HttpOnly 会话 Cookie（库只存 SHA-256）、注册审批流、登录限流、管理员引导（env/CLI） | [backend-core.md](docs/backend-core.md) |
| i18n 多语言 | i18next 简中 / 繁中 / 英语，按 `common` / `pages` / `components` / `hooks` / `errors` 命名空间拆分 | [frontend.md](docs/frontend.md) |

各功能模块的单模块设计（数据流、Pydantic 模型、API、配置）见 [docs/modules.md](docs/modules.md)。
