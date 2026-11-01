# LaTeXcellence — 测试文档

运行方式：`pytest tests/ -v`

代码门禁（`scripts/check_backend.sh`，本地 `check.sh` 与 CI 的 python-check 同源）以 pytest-xdist 并行执行：`-n auto --dist loadfile --maxprocesses=8`，按文件分发、保持文件内用例顺序。手工逐文件排查时直接 `pytest tests/ -v` 仍为串行。并行会话下 `.pytest_tmp` 会话目录的旧目录清理由 xdist 主进程统一执行（worker 跳过，避免并发误删）。

## docker marker 用例与 CI

`tests/test_docker/` 下的真容器冒烟带 `pytest.mark.docker`，无 Docker daemon 或缺少镜像时自动 skip。CI 的代码门禁 job（`python-check`）没有 daemon，因此显式以 `-m "not docker"` 排除（见 `scripts/check_backend.sh --skip-docker`）。

即使本机有 daemon，只要它运行在私有 mount namespace（如 systemd `PrivateTmp=yes`）里，宿主机 `/tmp` 路径在容器内不可见。warm pool 用例会以 `bind source path does not exist: /tmp/lx_warm_pool_*` 失败。这是环境问题而非代码问题，本地可加 `--skip-docker`。真容器冒烟应在 daemon 与工作目录共享挂载的专用机器 / runner 上跑。

## 测试基线配置与 CI 干净检出

`config.json` 含密钥不入库：存在时测试套件直接使用，干净检出（CI / 新 clone）自动回退加载入库的 `config.example.json`（见 `tests/conftest.py` 的 `_ensure_config`）。随后 fixture 在内存中统一钉住环境敏感基线——清空 access_token、关闭 multi_user、dummy api_key、沙箱 local 模式、打开联网/学术检索全局开关——保证测试结果不随开发者本机配置漂移。需要相反行为的用例自行 monkeypatch。

新增依赖环境配置的测试时优先沿用该基线，不要隐式依赖本机 `config.json` 的某个开关取值：否则 CI 干净检出上会出现本地无法复现的失败甚至挂起。

## 前端单测的栈预算

`npm test` 以 `node --stack-size=4000 --test` 运行：2MB 大文档的 Lezer 全量解析在树物化（`@lezer/common` 的 `takeNode`）中最深递归约 2500 层（`CutOff.Depth` 保护上限）。单帧较大、总耗栈 ≈1MB，恰好贴着 V8 默认 984KB 栈的边缘。本机侥幸通过，共享 CI runner 上以 `RangeError: Maximum call stack size exceeded` 告终。显式 4MB 栈（约为需求 4 倍，仍在 Linux 默认 8MB rlimit 之内）消除该环境不确定性。`--test` 的子进程会继承该标志。

## 运行环境注意：TestClient 与受限沙箱

在禁用 AF_UNIX socket 写入的受限沙箱（如网络受限的 Codex Linux 沙箱）中，`TestClient` 会在首次请求处挂起，属环境限制而非代码问题。解决方式：`~/.codex/config.toml` 开启 `network_access` 等。

## Windows 沙箱与 pytest 临时目录

Windows 上 Codex 沙箱以专用用户执行命令，pytest 的临时目录若跨会话、跨
主体复用，可能因 ACL 权限不足报 `PermissionError`（WinError 5）。仓库已改为
每次 pytest 会话使用独立的 `.pytest_tmp/session-*` 目录（根 `conftest.py`
负责创建，并在下次会话自动清理旧会话目录）。完整现象、根因、排查步骤与
修复手段见 [windows-temp-permissions.md](windows-temp-permissions.md)。

## 测试文件一览

按目录分组。各条目为覆盖摘要，完整断言以测试代码为准。

### tests/ 根目录（核心单元与配置测试）

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_latex_parser.py` | 注释、verb、环境、数学模式、命令替换、控制符号（`\?`/`\'`/`\~{n}` 等）、`\$` 转义处理（不进入数学匹配）、内联逐字扩展（`\verb*`/`\lstinline`）、往返一致性 |
| `test_marker_numbering_demo.py` | marker 编号机制演示 |
| `test_project_manager.py` | 项目打开、段落定位、原地修改、MD5 校验、撤销、路径穿越防御 |
| `test_json_utils.py` | safe_parse_json 容错解析、retry_with_validation |
| `test_llm_client.py` | chat_stream_with_usage 用量追踪与降级 |
| `test_llm_client_compat.py` | 推理模型统一兼容封装层（multi-model §5.2） |
| `test_llm_client_context_window.py` | 上下文窗口探测与缓存行为 |
| `test_llm_routes_api.py` | GET /api/llm/routes 端点与 /api/config 保存校验生效路径（multi-model P4） |
| `test_model_router.py` | 模型路由器与档案化客户端池（multi-model §5.1） |
| `test_config_model_profiles.py` | 模型档案（profiles）与槽位指派（assignments）配置：自动迁移 + 能力硬校验 |
| `test_config_multi_user.py` | 多用户配置模型、校验、config.example.json 一致性与状态端点 |
| `test_config_security.py` | 环境变量 API Key 防落盘、手动 Key 持久化、掩码保持 |
| `test_latex_output_adversarial.py` | JSON/LaTeX 静默损坏、Marker 保护、URL/逐字内容、分隔符截断和 BibTeX 字段边界的对抗性回归测试（历史缺口修复后均为正式用例，不再使用 xfail） |
| `test_changes_contract.py` / `test_prompts.py` | changes 契约（主字段与 changes 一致性）与提示词模板回归 |
| `test_tmp_basetemp.py` | 会话级 basetemp 机制回归（根 `conftest.py` 配置） |
| `test_prompt_eval_compare.py` | prompt_comprehension_eval 工具函数单元测试 |
| `test_typo_check_eval_core.py` / `test_typo_check_eval_report.py` 及 expression_opt / terminology_check / paragraph_translate / formula_ocr / table_ocr 的同名对 | 各人工评估工具的核心逻辑、语料一致性与报告生成单测——不调真实 LLM，属 pytest 自动测试（评估工具本身见下文「人工评估工具」节） |

### tests/test_core/

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_security_middleware.py` | CORS 白名单、访问令牌校验、同源放行与中间件拦截 |
| `test_base_paragraph_check_roles.py` | 逐段检查基类 LLM 槽位角色钩子（multi-model §6） |

### tests/test_utils/

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_path_guard.py` | resolve_project_path 六层校验 |
| `test_input_resolver.py` | 递归展开路径穿越检测 |
| `test_file_ops.py` | file_ops 单元测试 |
| `test_env_loader.py` | `backend/core/env.py` 的 `.env` 加载器 |
| `test_latex_json_guard.py` | LaTeX/BibTeX 内容校验：控制字符、垃圾占位符、花括号/$/环境配平、bibtex 模式、宽松模式（relaxed=True 放行 `\t`/`\r`、错误归因） |
| `test_compile_error_translator.py` | ErrorTranslation 模型、错误提取、LLM 流程、filtered_output |
| `test_tex_compile.py` | TexCompileResult 模型、texfot 成功/失败/超时、引擎自动检测、PDF/A 注入 |
| `test_edit_tools_manager.py` | edit_paragraph / undo_last_edit 执行、LaTeX 内容校验拒绝、写回 |
| `test_project_tools_manager.py` | 项目浏览工具（list_files / read_file_content / get_section_structure / search_across_files）执行与路径保护 |
| `test_agent_file_tools.py` | template_gen / plot_gen / style_modify 私有文件工具共享执行层与 `line_range` schema 契约 |
| `test_scholarly_research.py` | OpenAlex 元数据映射、摘要重建、去重、查询过滤、引文追踪、OA 定位和工具执行器 |
| `test_llm_cache.py` | llm_cache：LRU 缓存、并发合流、失败不缓存、键隔离 |
| `test_prompt_name_security.py` | 提示词管理器名称校验（防路径穿越） |
| `test_spa_static_files.py` | SPA 静态文件回退（history 路由刷新回 index.html；静态资源缺失、API 未命中保持 404） |
| `test_sandbox_models.py` | sandbox 包契约：ExecSpec / ExecResult / 挂载语义 |
| `test_sandbox_runner.py` | SandboxRunner：模式选择、探测回落、错误翻译、并发治理 |
| `test_sandbox_docker_backend.py` | docker 后端纯逻辑单测（无需 Docker daemon） |
| `test_sandbox_warm_pool.py` | warm pool（容器预创建池）hermetic 单测 |
| `test_web_search_agentic.py` / `test_web_search_tavily.py` / `test_web_search_ddg.py` | 联网搜索：查询重写、agentic 增强（open_page / find_in_page / 证据预算 / 引用校验）、Tavily / DDG 请求与解析 |
| `test_web_search_aggregation.py` | 搜索聚合：URL 安全规范化、跟踪参数清理、来源分类、RRF、近重复、域名上限、并发超时部分结果、熔断与查询计划校验重试；纯离线运行 |
| `test_web_access_contracts.py` | 网络访问 v2 契约：查询计划、UTC 时间、类型化抓取失败、结构化文档块引用、证据 ID 与 legacy 兼容性；纯离线运行 |
| `test_web_access_eval.py` | 网络访问离线评测集校验、URL/标识符归一化、重复抑制、块顺序、grounding 引用和 suite 隔离 |
| `test_web_page_reading.py` | 结构化读页：HTML 标题/链接/列表/表格保留、boilerplate 清理、heading-aware chunks、BM25/MMR 证据、缓存命中、条件请求隐私和抽取版本失效；纯离线运行 |
| `test_web_fetch_resilience.py` | 阻断分类、重试/熔断、浏览器 URL 策略和浏览器关闭降级；纯离线运行 |
| `test_web_access_stage6.py` | 网络访问阶段 6：回滚开关与本地容量检查；纯离线运行 |
| `test_writing_chat_context_security.py` | writing_chat 会话 id 校验（防目录穿越） |
| `test_bib_manager_format_rules.py` | 参考文献格式确定性规则语料（格式识别命中与预期） |
| `test_latex_package_analyzer.py` / `test_latex_package_rules.py` / `test_latex_package_corpus.py` / `test_latex_package_compile_verification.py` | 宏包规则表：分析器、规则数据完整性、语料查准/查全基线、离线编译验证 |

### tests/test_api/

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_typo_check.py` | 模型校验、LLM mock、Marker 保护、写回 |
| `test_expression_opt.py` | 模型校验、LLM mock、Marker 保护、prompt CRUD |
| `test_terminology_check.py` | 模型校验、LLM mock、聚类逻辑、写回 |
| `test_paragraph_translate.py` | 模型校验、LLM mock、Marker 保护、翻译写回 |
| `test_code_review.py` | 模型校验、preview 原始 LaTeX、LLM mock、写回 |
| `test_template_gen.py` | Agent 循环、沙箱路径校验、写盘前 LaTeX 内容校验、编译错误解析、失败警告、运行追踪（trace.jsonl 读写与 get_session_trace） |
| `test_plot_gen.py` | 数据上传、Agent 生成/微调 SSE、沙箱校验、会话文件与 trace |
| `test_formula_ocr.py` | 分隔符协议、内容校验重试、图片校验、预处理、LLM mock |
| `test_table_ocr.py` | 分隔符协议、内容校验、图片校验、LLM mock |
| `test_compile.py` | 编译成功/失败/超时、LLM 错误翻译、PDF 端点、路径穿越防御 |
| `test_compile_synctex.py` | SyncTeX 正反向端点（Mock 沙箱执行，无真实 synctex / docker / 网络）、`-synctex=1` 追加规则 |
| `test_git_diff.py` | Git revision、差异 PDF 渲染、路径安全校验 |
| `test_git_diff_redact.py` | git_diff 命令错误输出脱敏 |
| `test_git_manage.py` | 真实临时仓库上的 status/init/commit/log、空提交 400、已初始化 409、上限 413、index.lock 409、多用户脱敏、API 端点映射 |
| `test_format_converter.py` | 格式转换、pandoc mock |
| `test_style_modify.py` | 模型校验、LLM mock、样式修改 |
| `test_style_modify_agent.py` | 项目 Agent：LatexProject 批量事务、session overlay、proposal、应用冲突、撤销、workspace 隔离、路径穿越防御 |
| `test_writing_chat.py` | 模型、会话管理、发送/编辑/应用、RS2 分隔符协议提取/校验失败重试/CRLF 原文、助手提示词、搜索与学术研究工具事件 |
| `test_bib_manager.py` | 格式分析、元数据提取、BibTeX 生成、.bib 操作（含撤销/重做覆盖）、路径穿越防御、DOI 查询、多数据源 Provider 降级 |
| `test_project_browse.py` | 项目目录浏览与路径解析 |
| `test_project_undo.py` | 项目撤销/重做操作与状态检查 |
| `test_latex_formatter.py` | 统一格式化入口（latexindent / 内置兜底） |
| `test_llm_test_endpoint.py` | POST /api/llm/test 模型连通性测试端点（deploy-followups §4.4） |
| `test_editor_file_io.py` | 编辑器整文件读写（单用户）：409 乐观锁、字节保真、invalid_encoding |
| `test_editor_file_ops.py` | 文件树新建/上传/删除/移动/新建目录（单用户） |
| `test_editor_format_latex.py` | 一键格式化端点（Mock `format_latex_source`，无真实 latexindent 子进程） |
| `test_editor_latex_symbols.py` | 引用符号扫描端点（单用户，纯文件操作） |
| `test_editor_outline.py` | 跨文件大纲端点（缓存命中与保存失效） |
| `test_editor_assistant.py` | 编辑器 AI 助手：上下文注入、建议提取、SSE 端点（Mock LLM）、工具白名单、DDG 挑战分支 |

### tests/test_multiuser/

多用户模式测试的共享 fixtures 在 `tests/test_multiuser/conftest.py`。

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_auth_core.py` | 认证核心纯函数（哈希/会话/引导/限流） |
| `test_api_auth.py` | 注册 / 审批 / 登录 / 会话 / CLI 引导 API 集成 |
| `test_api_admin.py` | 管理员端点（用户列表/审批/禁用/重置密码/配额） |
| `test_db.py` | SQLite 初始化与 schema 完整性 |
| `test_manage_cli.py` | CLI 管理工具（`python -m backend.manage`） |
| `test_access_control.py` | 用户空间与访问控制 |
| `test_feature_router_access.py` | 功能模块路由访问控制（回归） |
| `test_projects_api.py` | 项目 API：上传 / 列表 / 下载 / 重命名 / 回收站 / 配额 |
| `test_quota.py` | 配额记账核心 |
| `test_derived_usage.py` | 衍生数据记账与细粒度清理 |
| `test_upload_security.py` | 上传解压管线安全 |
| `test_upload_rar.py` | rar 上传解压管线 + 回退层 |
| `test_upload_sessions.py` | 大文件分片上传与断点续传 |
| `test_collaboration.py` | 权限协作：协作者管理 / 转交 / 权限即时生效 |
| `test_edit_log.py` | 反向补丁编辑日志（undo/redo 记账） |
| `test_llm_governance.py` | LLM 每用户治理：滑动窗口限流 + 并发租约 + 登录限流 |
| `test_sandbox_leases.py` | 沙箱并发池租约（sandbox_slots） |
| `test_git_manage_auto_init.py` | 上传自动 Git 初始化 + `.git` 配额豁免与独立上限 |
| `test_mail.py` | 邮件服务：outbox 队列 / 降级 / 重试 / 限速 / 配额预警 |
| `test_password_reset.py` | 自助找回密码与邮箱绑定 |
| `test_registration_auto_approve.py` | 注册自动审批（IP 策略） |
| `test_p6_legacy_and_migrate.py` | 存量迁移与隔离 |
| `test_p7_instance_ops.py` | 多实例运维：维护选主租约、flock 会话互斥、配置热重载、stdout 日志开关 |
| `test_p7_state_externalization.py` | 多实例状态外置：跨实例交叉操作 |
| `test_editor_file_ops.py` / `test_editor_save_file.py` | 编辑器文件树操作与整文件读写（多用户模式） |
| `test_required_fixes.py` | 审查修复项回归 |

### tests/test_template_library/

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_models.py` | TemplateManifest 等模型校验 |
| `test_builtin_store.py` | 内置模板发现与共享文件原语 |
| `test_user_store.py` | 用户模板 CRUD（双模式 + 用户隔离 + 配额并入） |
| `test_api_templates.py` | 单用户模式 /api/templates 全端点 |
| `test_api_templates_multiuser.py` | 多用户模式：认证 / 越权隔离 / 配额 413 / 清洗 / 特殊端点 |

### tests/test_docker/（marker: docker，无 daemon 自动 skip）

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_sandbox_smoke.py` | Docker 沙箱真容器冒烟 |
| `test_warm_pool_smoke.py` | warm pool 真容器冒烟 |
| `test_rar_sandbox_smoke.py` | convert 镜像 rar 解压真容器冒烟（三重 skip 门） |
| `test_compose_deploy.py` | Compose 编排冒烟（多副本） |

### tests/test_plot_gen/

plot_gen 单元测试：`test_data_parser.py`（数据画像）、`test_recommender.py`（图表推荐）、`test_styles.py`（风格系统）、`test_executor.py`（沙箱执行器）、`test_tikzgen.py`（受控 PGFPlots 生成器）、`test_trace.py`（运行追踪）、`test_prompts.py`（提示词模板）。

## style_modify Agent 测试要点

`tests/test_api/test_style_modify_agent.py` 覆盖项目 Agent 的事务语义：

- `LatexProject.apply_whole_files()` 成功写入全部文件并更新 MD5，MD5 冲突时快速失败且不覆盖新内容
- `stage_replace` 冻结 baseline、写 overlay，真实项目在应用前保持不变
- `read_project_file` 使用 `line_range: [start, end]`，返回事件不再暴露 `start_line` / `end_line`
- proposal 生成后应用会校验 workspace、项目根目录、revision 和真实文件 MD5
- 多文件操作以 all-or-nothing 语义写回，operation 保存 before/applied 内容
- undo 只恢复仍等于 applied MD5 的文件，不覆盖应用后的其他编辑
- 不同 workspace 不能读取同一 session

Agent 的 LLM 循环测试应 mock `chat_with_tools()`，不发起真实网络请求。shadow 编译测试应 mock 或复用 `run_tex_compile()` 的确定性路径。

## 人工评估工具（非 pytest）

`tests/fixtures/formula_ocr/` 是公式 OCR 的**人工评估语料**（每条公式一个 LaTeX 片段 + `manifest.json`），配套维护者工具位于 `tools/formula_ocr_eval/`：

- `render_corpus.py`：把语料渲染为评估输入图片（需本机 TeX Live）
- `run_eval.py`：逐条调用真实 OCR 管线（含 LLM），把识别结果渲染成图片并生成 HTML 比对报告

该流程涉及真实 LLM 调用且依赖人工判断，**不属于** `pytest tests/` 自动测试范围，也不会在 CI 中运行。使用说明见 `tools/formula_ocr_eval/README.md`。

---

`tests/fixtures/table_ocr/` 是表格 OCR 的**人工评估语料**（每条表格一个完整 LaTeX 浮动体片段 + `manifest.json`），配套维护者工具位于 `tools/table_ocr_eval/`：

- `render_corpus.py`：把语料渲染为评估输入图片（需本机 TeX Live）
- `run_eval.py`：逐条调用真实 OCR 管线（含 LLM），把识别结果渲染成图片并生成 HTML 比对报告

同样依赖人工判断，不属于 `pytest tests/` 自动测试范围。使用说明见 `tools/table_ocr_eval/README.md`。

---

`tests/fixtures/typo_check/` 是错字查找的**人工评估语料**（每条一个 LaTeX 段落片段 + `manifest.json`，预埋期望错字，含干净段落用于误报检测），配套维护者工具位于 `tools/typo_check_eval/`：

- `run_eval.py`：逐条调用真实逐段检查管线（含 LLM），把修正文本还原 marker 后生成原文/修正后对照 HTML 报告

同样依赖人工判断，不属于 `pytest tests/` 自动测试范围。使用说明见 `tools/typo_check_eval/README.md`。

---

`tests/fixtures/expression_opt/` 是表达优化的**人工评估语料**（每条一个 LaTeX 段落片段 + `manifest.json`，预埋期望优化，含已精炼段落用于误报检测），配套维护者工具位于 `tools/expression_opt_eval/`：

- `run_eval.py`：逐条调用真实逐段优化管线（含 LLM），把优化文本还原 marker 后生成原文/优化后对照 HTML 报告

同样依赖人工判断，不属于 `pytest tests/` 自动测试范围。使用说明见 `tools/expression_opt_eval/README.md`。

---

`tools/prompt_comprehension_eval/` 是提示词理解评测工具：对
`backend/core/prompts.py` 中每个 LLM 提示词构造最简探测任务，调用真实
LLM 并自动核验 JSON 格式、Marker 保护、分隔符协议、JSON 转义、语言注入
等关键契约，输出 `logs/prompt_comprehension_eval/results.json` 供人工复核。
该流程依赖真实 LLM 调用，不属于 `pytest tests/` 自动测试范围。使用说明见
`tools/prompt_comprehension_eval/README.md`。

---

`tools/web_access_eval/` 是网络访问的维护者工具（非 pytest）：

- `legacy.py`：手动采集 legacy/aggregate 搜索、legacy 学术结果或脱敏
  grounding trace；aggregate 会保存 v2 引擎失败状态，需要显式
  `--allow-network` 才访问外部服务
- `spike.py`：离线比较 BeautifulSoup/trafilatura、Playwright 本地页面和
  `pdftotext`，不属于生产运行时

---

## RAR 入库样本与结构校验

`tests/fixtures/rar/` 是多用户 rar 上传管线的自动化测试样本，分两类：

- **纯字节手工构造**（`tools/rar_fixtures/make_fixtures.py` 生成，store 模式
  RAR4）：布局、GBK 文件名、加密/分卷标志、服务块/子块数据区回归等结构性
  用例，不依赖构建环境装包。
- **真实 rar CLI 生成**（`compressed_rar4.rar` / `compressed_rar5.rar` /
  `encrypted_rar5.rar` / `volume4.part1.rar` / `volume4.part2.rar`，rarlab
  rar 6.24 产物，总体积 KB 级）：压缩流无法伪造，用于「真实压缩 RAR4/RAR5
  双工具端到端上传解压」验收。再生成步骤见 `make_fixtures.py` 模块 docstring
  （注意 rar 7.x 起移除 `-ma4`，无法生成 RAR4 归档）。

`tools/rar_fixtures/verify_fixtures.py` 对**全部**入库样本做结构对账
（版本头、压缩方法与实际压缩比、加密/分卷标志、成员名清单、单样本体积
上限），并与生产管线 RAR4 预扫行为互验（预期放行/拒绝逐一对账）。环境
存在 unar/bsdtar 时额外做真实解压对账（压缩样本解出内容 sha256 与源
指纹一致、真实加密包必须被解压器拒绝）。任何环境都可运行：

```bash
python tools/rar_fixtures/verify_fixtures.py   # 退出码 0=全部通过
```

新增/删除入库样本必须同步该脚本的 `EXPECTATIONS` 期望表。
