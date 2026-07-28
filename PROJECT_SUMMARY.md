# LateXcellence — Project Summary

> 项目概要文件。项目结构变更时（新增/删除模块），必须同步更新此文件。

---

## 目录结构

```
latexcellence/
├── main.py                          # FastAPI 入口 + 静态文件挂载
├── config.json                      # 配置文件
├── config.example.json              # 配置模板
├── AGENTS.md                        # AI 编程助手规范
├── DEVELOPMENT.md                   # 开发文档索引（指向 docs/ 目录）
├── DESIGN.md                        # 设计文档
├── PROJECT_SUMMARY.md               # ← 本文件：项目概要（供 AI 快速读取）
├── README.md                        # 项目简介
├── THIRD_PARTY_NOTICES.md           # 第三方许可
├── clean-cache.sh                   # 缓存清理脚本
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
│   │   ├── latex_parser.py          # LaTeX 源码↔纯文本转换（Marker 保护）
│   │   ├── compile_error_translator.py  # LaTeX 编译错误 LLM 翻译（compile + git_diff 复用）
│   │   ├── latexindent_utils.py     # latexindent.pl 调用封装（code_review 使用）
│   │   ├── builtin_formatter.py     # 内置轻量 LaTeX 格式化器（latexindent 不可用时兜底）
│   │   ├── web_search/               # 联网搜索模块（多引擎抽象包）
│   │   │   ├── __init__.py           # 模块入口，向后兼容导出
│   │   │   ├── models.py             # SearchResult / SearchResponse 数据模型
│   │   │   ├── base.py               # BaseSearchEngine 抽象基类
│   │   │   ├── manager.py            # SearchManager + 全局单例 + Function Calling schema
│   │   │   └── engines/              # 具体搜索引擎实现
│   │   │       ├── ddg.py            # DuckDuckGo HTML 引擎 + DDG Challenge 处理
│   │   │       ├── searxng.py        # SearXNG 引擎
│   │   │       └── mock.py           # Mock 引擎（测试/降级）
│   │   └── search_router.py         # 通用搜索 API 路由（/api/search/ddg-challenge）
│   └── modules/                     # 功能模块目录
│       ├── typo_check/              # 错字查找
│       ├── expression_opt/          # 表达优化
│       ├── terminology_check/       # 术语一致性
│       ├── paragraph_translate/     # 逐段翻译
│       ├── code_review/              # LaTeX 代码审查
│       ├── compile/                 # 自动编译
│       ├── git_diff/                # Git 差异高亮
│       ├── template_gen/            # 模板生成（含自主 Agent）
│       ├── formula_ocr/             # 公式 OCR
│       ├── table_ocr/               # 表格 OCR
│       ├── format_converter/        # 格式转换
│       ├── style_modify/            # 样式修改
│       ├── writing_chat/            # 写作助手聊天
│       ├── bib_manager/             # 参考文献管理（含 DOI 多数据源查询）
│       │   └── providers/           # 可插拔 DOI 数据源提供者
│       └── project_router.py        # 项目管理路由
│
├── frontend/
│   └── src/
│       ├── App.jsx                  # 根路由
│       ├── i18n/                    # i18n 多语言配置 + 翻译资源
│       │   ├── index.js             # i18next 初始化（含语言检测）
│       │   └── locales/             # 翻译 JSON 文件
│       ├── pages/                   # 页面组件
│       │   └── writing-chat/        # 写作聊天子组件（MessageBubble/EditOpCard/CaptchaDialog）
│       ├── hooks/                   # 自定义 hook
│       │   └── writing-chat/        # 写作聊天子 hooks（sessions/assistants/captcha/sse/utils）
│       ├── components/              # 共享组件 + ui 基座组件
│       ├── theme/                   # 主题 CSS + useThemeManager.js
│       ├── api/index.js             # 统一 API 层
│       └── store/useStore.js        # Zustand 全局状态
│
├── docs/                            # 开发文档
│   ├── development.md               # 项目概述/技术栈/启动
│   ├── backend-core.md              # 后端核心模块
│   ├── modules.md                   # 模块索引
│   ├── frontend.md                  # 前端架构
│   ├── testing.md                   # 测试规范
│   ├── multi-user-migration-plan.md # 多用户改造计划
│   └── modules/                     # 模块文档
├── tests/                           # 测试用例
├── prompts/                         # 自定义提示词 
├── formats/                         # 参考文献格式定义
├── templates/                       # 生成的 LaTeX 模板
├── writing_chat_sessions/           # 写作助手聊天持久化数据
├── logs/                            # 运行时日志
└── static/                          # Vite 构建产物
```

## Feature Modules Quick Reference

| Module | 目的 | 关键文件 | 依赖 LLM | Marker |
| --- | --- | --- | --- | --- |
| typo_check | 错字/拼写检查 | router.py, service.py | ✓ | ✓ |
| expression_opt | 学术表达优化 | router.py, service.py, prompt_manager.py | ✓ | ✓ |
| code_review | LaTeX 代码审查（工具格式化 + LLM 质量审查） | router.py, service.py | ✓ | ✓ |
| terminology_check | 术语一致性检查 | router.py, service.py, extractor.py, models.py | ✓ | ✓ |
| paragraph_translate | 逐段翻译 | router.py, service.py | ✓ | ✓ |
| compile | 编译 + 错误翻译 + 引擎自动检测 + PDF/A 支持 | router.py, service.py | ✓(错误翻译) | ✗ |
| git_diff | Git 版本差异 PDF | router.py, service.py | ✓(编译失败时翻译错误) | ✗ |
| template_gen | 模板生成（自主 Agent） | router.py, service.py, agent.py, cleanup.py | ✓ | ✗ |
| formula_ocr | 图片→LaTeX 公式 | router.py, service.py | ✓(Vision) | ✗ |
| table_ocr | 图片→LaTeX 表格 | router.py, service.py | ✓(Vision) | ✗ |
| format_converter | 格式转换(pandoc) | router.py, service.py | ✓(宏包分析) | ✗ |
| style_modify | 自然语言修改样式 | router.py, service.py | ✓ | ✗ |
| writing_chat | 写作助手对话 | router.py, service.py, context.py, models.py, prompt_manager.py | ✓ | ✗ |
| bib_manager | 参考文献管理 + DOI 多数据源查询 | router.py, service.py, provider_manager.py, providers/ | ✓(DOI 查询不依赖 LLM) | ✗ |

## 核心设计思路

- **集中式提示词管理**: 所有 LLM 提示词集中存储在 `backend/core/prompts.py` 中，按模块和功能分类。提供 `get_prompt(module, key, **kwargs)` 函数获取提示词，支持参数化模板。各模块通过 `from backend.core.prompts import get_prompt` 导入使用，便于统一维护和版本控制
- **LLM 输出语言注入**: 所有调用 LLM 的模块在 system prompt 中通过 `{language}` 占位符指定输出语言。前端 `getUiLanguage()` 将 i18n 语言代码（`"zh"`/`"en"`）转为完整名称（`"Chinese"`/`"English"`），通过 `language` 参数传入后端并注入 prompt 模板。无需额外的 `_general.language_context` 拼接
- **BaseParagraphCheckService**: 多个逐段检查模块（typo_check / expression_opt / code_review / terminology_check / paragraph_translate）共享的基类，封装文件解析、marker 替换、LLM 编排（批量+流式）、结果写回等流程。子类通过多个 hooks（_get_system_prompt / _build_user_content / _get_model_cls / _get_llm_params / _get_batch_size / _build_success_result）定制行为
- **WorkspaceManager**: 多浏览器多工作区隔离，每个 workspace_id 拥有独立的 `LatexProject` 实例（项目状态、文件树、MD5 缓存、撤销栈完全隔离）
- **乐观锁**: 多工作区并发编辑同一文件时，通过 MD5 哈希检测冲突。读取段落时返回 `file_md5`，应用修改时前端传回 `expected_md5`，后端校验失败时抛出 `FileConflictError`（HTTP 409），前端自动刷新 MD5 并提示用户重试。`inject_expected_md5s()` 辅助函数统一为各模块 router 注入预期 MD5
- **Marker Protection**: LaTeX 结构 → `__CMD_NNNN__` / `__MATH_NNNN__` / `__ENV_NNNN__` / `__VERB_NNNN__` marker → LLM 只改纯文本 → 按长度降序还原。apply 时若 marker_map 缺失（context 过期/重启/新操作覆盖），跳过该段写入并返回 `errors`，防止字面 marker 污染 `.tex` 文件；前端据此保留未应用项并提示用户重新检查。返回给前端展示的 `changes[]` 中的 `original`/`corrected` 会在 `_build_success_result` 中预先还原 marker，确保用户看到的是干净的 LaTeX 文本而非 `__CMD_NNNN__` 占位符
- **JSON Validation Pipeline**: `safe_parse_json()` → Pydantic `.model_validate()` → `retry_with_validation()` (最多 3 次重试 + 错误反馈)
- **SSE Streaming**: 所有段落检查模块通过 Server-Sent Events 推送 `progress`/`result`/`error`/`done` 事件
- **Function Calling 工具体系**: `ToolRegistry`（工具注册表）管理所有可用工具的 schema 和执行函数；`SearchManager`（搜索管理器）提供 `web_search` 工具的 schema 和执行，统一封装 `DuckDuckGoHtmlEngine`（模拟浏览器请求，遇 HTTP 202 Challenge 时抛出 `DDGChallengeError`，由 service 捕获并以 `captcha_challenge` SSE 事件推送到前端）和 `SearXNGEngine`（通过 `/search?format=json` 获取结果，无验证码）；DDG Challenge 验证码提交通过通用路由 `/api/search/ddg-challenge` 处理，不绑定特定业务模块；Writing Chat 的 `send()` 方法在检测到 LLM 返回 `tool_call` 时自动执行工具、将结果回传 LLM，再流式输出最终回复（"先工具调用再流式"模式）
- **DOI 多数据源查询**: `ProviderManager`（全局单例）管理多个可插拔 `BaseMetadataProvider`
    - Crossref 主索引 + arXiv 预印本专用 + 补全层
    - arXiv DOI（`10.48550/arXiv.*`）自动路由到 ArxivProvider
    - 普通 DOI 先查 Crossref 成功后用补全层合并空字段（`_merge()`），失败则串行降级
    - 配置通过 `config.json → features.bib_manager.providers` 独立开关每个数据源
- **HTTP 全局配置**: `AppConfig.http.user_agent` 统一管理所有外部 HTTP 请求的 User-Agent，供 web_search 引擎和 bib_manager Provider 共用
- **模板生成自主 Agent**: `TemplateAgentService` 基于 Function Calling 实现"写文件 → 编译验证 → 读日志 → 修错 → 再编译"的自主循环，产出完整可编译的多文件 LaTeX 工程。
    - 工具（`write_file`/`read_file`/`list_files`/`compile_latex`/`finish`）为 session 级私有，schema 由 service 动态构建并经 `chat_with_tools` 注入，不注册到全局 `ToolRegistry` 避免污染其他模块工具集。
    - 每次生成在 `output_dir/agent/<session_id>/` 创建独立沙箱，路径经 `_resolve_sandbox_path` 校验（禁绝对路径 / `..` / 非白名单扩展名 / 越界）。SSE 推送 `plan`/`tool_call`/`tool_result`/`compile`（含结构化 `errors` 列表）/`warning`（编译未过却 finish）/`done`/`error` 事件。
    - 编译失败时 `_extract_errors` 从日志中提取类型/文件/行号，`_format_error_analysis` 拼接聚焦摘要回传 LLM；prompt 中 COMPILATION ERROR RECOVERY PROTOCOL 强制 LLM 走 ANALYZE→READ→FIX→RECOMPILE 循环，禁止编译未过时调用 finish。
    - 沙箱自动清理由 `cleanup.py` 实现：`cleanup_expired_sandboxes()` 按目录内最新文件 mtime 与 `cleanup_retention_days` 比较判定过期，`cleanup_loop()` 作为 `asyncio` 后台任务在 FastAPI lifespan 中启动并按 `cleanup_interval_seconds` 周期扫描，删除过期目录，服务关闭时优雅取消。
- **useParagraphCheck Hook**: TypoCheck/ExpressionOpt/CodeReview/TerminologyCheck/ParagraphTranslate 多页共享同一 hook 封装流程
- **useEnabledFeatures Hook**: 从后端 `/api/config/status` 加载功能开关到 Zustand store，幂等（已加载则跳过）。WelcomePage 和 WorkspacePage 均调用此 hook，确保直接刷新任一页面时功能开关均可用
- **Zustand Persist**: `partialize` 选择性持久化检查结果，支持刷新保留
- **Multi-Theme**: `theme/` 目录下每个 CSS 文件定义一组完整配色（`[data-theme="xxx"]` light + dark），通过 `import.meta.glob` 自动发现，用户可在设置或顶栏下拉菜单中实时切换
- **CheckOperationContext**: 每次 preview/check 生成唯一 `operation_id`，将 marker 映射和 MD5 快照深拷贝存入上下文（TTL 1 小时），apply 时通过 operation_id 精确还原。若 context 丢失（过期/重启/新操作覆盖），apply_changes 逐段检测 marker_map，缺失时跳过写入并返回 `errors` 列表，前端据此保留结果项并提示重新检查
- **useUndoRedo Hook**: 基于后端每文件独立 undo/redo deque（最大 50 层）的撤销/重做状态管理，与 project_manager 同步
- **useResultFilter Hook**: 检查结果排序与过滤逻辑封装——changes 非空优先、无建议在后、同类按 paragraph_index 升序
- **useWritingChat Hook**: 写作助手聊天的完整生命周期管理（消息流式、thinking 标签解析、上下文压缩、会话持久化）
- **MarkerText / ParagraphContext 组件**: `MarkerText` 将含 marker 的文本渲染为友好标签（如 `__MATH_0000__` → 公式图标）；`ParagraphContext` 展示当前段落前后文，用于逐段检查结果页面
- **i18n 多语言**: 基于 `i18next` + `react-i18next` + `i18next-browser-languagedetector`，支持中文/英文双语。翻译文件按命名空间拆分（`common` / `pages` / `components` / `hooks`），组件通过 `useTranslation()` hook 或 `i18n.t()` 调用。语言选择持久化到 localStorage，用户可在设置弹窗中切换
