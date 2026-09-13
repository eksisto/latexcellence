# LaTeXcellence — 测试文档

运行方式：`pytest tests/ -v`

## docker marker 用例与 CI

`tests/test_docker/` 下的真容器冒烟带 `pytest.mark.docker`，无 Docker daemon 或缺少镜像时自动 skip；CI 的代码门禁 job（`python-check`）没有 daemon，因此显式以 `-m "not docker"` 排除（见 `scripts/check_backend.sh --skip-docker`）。

即使本机有 daemon，只要它运行在私有 mount namespace（如 systemd `PrivateTmp=yes`）里，宿主机 `/tmp` 路径在容器内不可见，warm pool 用例会以 `bind source path does not exist: /tmp/lx_warm_pool_*` 失败——这是环境问题而非代码问题，本地可加 `--skip-docker`；真容器冒烟应在 daemon 与工作目录共享挂载的专用机器 / runner 上跑。

## 测试基线配置与 CI 干净检出

`config.json` 含密钥不入库：存在时测试套件直接使用，干净检出（CI / 新 clone）自动回退加载入库的 `config.example.json`（见 `tests/conftest.py` 的 `_ensure_config`）。随后 fixture 在内存中统一钉住环境敏感基线——清空 access_token、关闭 multi_user、dummy api_key、沙箱 local 模式、打开联网/学术检索全局开关——保证测试结果不随开发者本机配置漂移；需要相反行为的用例自行 monkeypatch。

新增依赖环境配置的测试时优先沿用该基线，不要隐式依赖本机 `config.json` 的某个开关取值：否则 CI 干净检出上会出现本地无法复现的失败甚至挂起。

## 前端单测的栈预算

`npm test` 以 `node --stack-size=4000 --test` 运行：2MB 大文档的 Lezer 全量解析在树物化（`@lezer/common` 的 `takeNode`）中最深递归约 2500 层（`CutOff.Depth` 保护上限），单帧较大、总耗栈 ≈1MB，恰好贴着 V8 默认 984KB 栈的边缘——本机侥幸通过、共享 CI runner 上以 `RangeError: Maximum call stack size exceeded` 告终。显式 4MB 栈（约为需求 4 倍，仍在 Linux 默认 8MB rlimit 之内）消除该环境不确定性；`--test` 的子进程会继承该标志。

## 运行环境注意：TestClient 与受限沙箱

在禁用 AF_UNIX socket 写入的受限沙箱（如网络受限的 Codex Linux 沙箱）中，`TestClient` 会在首次请求处挂起，属环境限制而非代码问题。解决方式：`~/.codex/config.toml` 开启 `network_access` 等。

## Windows 沙箱与 pytest 临时目录

Windows 上 Codex 沙箱以专用用户执行命令，pytest 的临时目录若跨会话、跨
主体复用，可能因 ACL 权限不足报 `PermissionError`（WinError 5）。仓库已改为
每次 pytest 会话使用独立的 `.pytest_tmp/session-*` 目录（根 `conftest.py`
负责创建，并在下次会话自动清理旧会话目录）。完整现象、根因、排查步骤与
修复手段见 [windows-temp-permissions.md](windows-temp-permissions.md)。

## 测试文件一览

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_latex_parser.py` | 注释、verb、环境、数学模式、命令替换、控制符号（`\?`/`\'`/`\~{n}` 等）、`\$` 转义处理（不进入数学匹配）、内联逐字扩展（`\verb*`/`\lstinline`）、往返一致性 |
| `test_marker_numbering_demo.py` | marker 编号机制演示 |
| `test_project_manager.py` | 项目打开、段落定位、原地修改、MD5 校验、撤销、路径穿越防御 |
| `test_json_utils.py` | safe_parse_json 容错解析、retry_with_validation |
| `test_llm_client.py` | chat_stream_with_usage 用量追踪与降级 |
| `test_config_security.py` | 环境变量 API Key 防落盘、手动 Key 持久化、掩码保持 |
| `test_utils/test_path_guard.py` | resolve_project_path 六层校验 |
| `test_core/test_security_middleware.py` | CORS 白名单、访问令牌校验、同源放行与中间件拦截 |
| `test_utils/test_input_resolver.py` | 递归展开路径穿越检测 |
| `test_utils/test_compile_error_translator.py` | ErrorTranslation 模型、错误提取、LLM 流程、filtered_output |
| `test_utils/test_tex_compile.py` | TexCompileResult 模型、texfot 成功/失败/超时、引擎自动检测、PDF/A 注入 |
| `test_utils/test_latex_json_guard.py` | LaTeX/BibTeX 内容校验：控制字符、垃圾占位符、花括号/$/环境配平、bibtex 模式、宽松模式（relaxed=True 放行 `\t`/`\r`、错误归因） |
| `test_latex_output_adversarial.py` | JSON/LaTeX 静默损坏、Marker 保护、URL/逐字内容、分隔符截断和 BibTeX 字段边界的对抗性回归测试（历史缺口修复后均为正式用例，不再使用 xfail） |
| `test_utils/test_edit_tools_manager.py` | edit_paragraph / undo_last_edit 执行、LaTeX 内容校验拒绝、写回 |
| `test_utils/test_scholarly_research.py` | OpenAlex 元数据映射、摘要重建、去重、查询过滤、引文追踪、OA 定位和工具执行器 |
| `test_utils/test_project_tools_manager.py` | 项目浏览工具（list_files / read_file_content / get_section_structure / search_across_files）执行与路径保护 |
| `test_utils/test_agent_file_tools.py` | template_gen / plot_gen / style_modify 私有文件工具共享执行层与 `line_range` schema 契约 |
| `test_utils/test_web_search_agentic.py` / `test_web_search_tavily.py` | 联网搜索：查询重写、引擎路由、Tavily 请求与结果解析 |
| `test_utils/test_web_access_contracts.py` | 网络访问 v2 契约：查询计划、UTC 时间、类型化抓取失败、结构化文档块引用、证据 ID 与 legacy 兼容性；纯离线运行 |
| `test_utils/test_web_access_eval.py` | 网络访问离线评测集校验、URL/标识符归一化、重复抑制、块顺序、grounding 引用和 suite 隔离 |
| `test_utils/test_web_search_aggregation.py` | 搜索聚合：URL 安全规范化、跟踪参数清理、来源分类、RRF、近重复、域名上限、并发超时部分结果、熔断与查询计划校验重试；纯离线运行 |
| `test_utils/test_web_page_reading.py` | 结构化读页：HTML 标题/链接/列表/表格保留、boilerplate 清理、heading-aware chunks、BM25/MMR 证据、缓存命中、条件请求隐私和抽取版本失效；纯离线运行 |
| `test_utils/test_web_access_stage6.py` | 网络访问阶段 6：回滚开关与本地容量检查；纯离线运行 |
| `test_utils/test_web_fetch_resilience.py` | 阻断分类、重试/熔断、浏览器 URL 策略和浏览器关闭降级；纯离线运行 |
| `tools/web_access_eval/legacy.py` | 维护者手动采集 legacy/aggregate 搜索、legacy 学术结果或脱敏 grounding trace；aggregate 会保存 v2 引擎失败状态，需要显式 `--allow-network` 才访问外部服务 |
| `tools/web_access_eval/spike.py` | 离线比较 BeautifulSoup/trafilatura、Playwright 本地页面和 `pdftotext`，不属于生产运行时 |
| `test_utils/test_bib_manager_format_rules.py` | 参考文献格式确定性规则语料（格式识别命中与预期） |
| `test_utils/test_latex_package_analyzer.py` / `test_latex_package_rules.py` / `test_latex_package_corpus.py` / `test_latex_package_compile_verification.py` | 宏包规则表：分析器、规则数据完整性、语料查准/查全基线、离线编译验证 |
| `test_api/test_typo_check.py` | 模型校验、LLM mock、marker 保护、写回 |
| `test_api/test_expression_opt.py` | 模型校验、LLM mock、marker 保护、prompt CRUD |
| `test_api/test_terminology_check.py` | 模型校验、LLM mock、聚类逻辑、写回 |
| `test_api/test_template_gen.py` | Agent 循环、沙箱路径校验、写盘前 LaTeX 内容校验、编译错误解析、失败警告、运行追踪（trace.jsonl 读写与 get_session_trace） |
| `test_api/test_formula_ocr.py` | 分隔符协议、内容校验重试、图片校验、预处理、LLM mock |
| `test_api/test_table_ocr.py` | 分隔符协议、内容校验、图片校验、LLM mock |
| `test_api/test_code_review.py` | 模型校验、preview 原始 LaTeX、LLM mock、写回 |
| `test_api/test_compile.py` | 编译成功/失败/超时、LLM 错误翻译、PDF 端点、路径穿越防御 |
| `test_api/test_git_diff.py` | Git revision、差异 PDF 渲染、路径安全校验 |
| `test_api/test_git_manage.py` | 真实临时仓库上的 status/init/commit/log、空提交 400、已初始化 409、上限 413、index.lock 409、多用户脱敏、API 端点映射 |
| `test_api/test_format_converter.py` | 格式转换、pandoc mock |
| `test_api/test_style_modify.py` | 模型校验、LLM mock、样式修改 |
| `test_api/test_style_modify_agent.py` | 项目 Agent：LatexProject 批量事务、session overlay、proposal、应用冲突、撤销、workspace 隔离、路径穿越防御 |
| `test_api/test_writing_chat.py` | 模型、会话管理、发送/编辑/应用、RS2 分隔符协议提取/校验失败重试/CRLF 原文、助手提示词、搜索与学术研究工具事件 |
| `test_api/test_bib_manager.py` | 格式分析、元数据提取、BibTeX 生成、.bib 操作（含撤销/重做覆盖）、路径穿越防御、DOI 查询、多数据源 Provider 降级 |
| `test_api/test_paragraph_translate.py` | 模型校验、LLM mock、marker 保护、翻译写回 |
| `test_api/test_plot_gen.py` | 数据上传、Agent 生成/微调 SSE、沙箱校验、会话文件与 trace |
| `test_api/test_project_browse.py` | 项目目录浏览与路径解析 |
| `test_api/test_project_undo.py` | 项目撤销/重做操作与状态检查 |
| `test_api/test_latex_formatter.py` | 统一格式化入口（latexindent / 内置兜底） |
| `test_changes_contract.py` / `test_prompts.py` | changes 契约（主字段与 changes 一致性）与提示词模板回归 |

`tests/test_plot_gen/` 另含 plot_gen 单元测试：`test_data_parser.py`、`test_recommender.py`、`test_styles.py`、`test_executor.py`、`test_tikzgen.py`、`test_trace.py`、`test_prompts.py`。

## style_modify Agent 测试要点

`tests/test_api/test_style_modify_agent.py` 覆盖项目 Agent 的事务语义：

- `LatexProject.apply_whole_files()` 成功写入全部文件并更新 MD5，MD5 冲突时快速失败且不覆盖新内容
- `stage_replace` 冻结 baseline、写 overlay，真实项目在应用前保持不变
- `read_project_file` 使用 `line_range: [start, end]`，返回事件不再暴露 `start_line` / `end_line`
- proposal 生成后应用会校验 workspace、项目根目录、revision 和真实文件 MD5
- 多文件操作以 all-or-nothing 语义写回，operation 保存 before/applied 内容
- undo 只恢复仍等于 applied MD5 的文件，不覆盖应用后的其他编辑
- 不同 workspace 不能读取同一 session

Agent 的 LLM 循环测试应 mock `chat_with_tools()`，不发起真实网络请求；shadow 编译测试应 mock 或复用 `run_tex_compile()` 的确定性路径。

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
LLM 并自动核验 JSON 格式、marker 保护、分隔符协议、JSON 转义、语言注入
等关键契约，输出 `logs/prompt_comprehension_eval/results.json` 供人工复核。
该流程依赖真实 LLM 调用，不属于 `pytest tests/` 自动测试范围。使用说明见
`tools/prompt_comprehension_eval/README.md`。

---

## RAR 入库样本与结构校验

`tests/fixtures/rar/` 是多用户 rar 上传管线的自动化测试样本，分两类：

- **纯字节手工构造**（`tools/rar_fixtures/make_fixtures.py` 生成，store 模式
  RAR4）：布局、GBK 文件名、加密/分卷标志、服务块/子块数据区回归等结构性
  用例，不依赖构建环境装包；
- **真实 rar CLI 生成**（`compressed_rar4.rar` / `compressed_rar5.rar` /
  `encrypted_rar5.rar` / `volume4.part1.rar` / `volume4.part2.rar`，rarlab
  rar 6.24 产物，总体积 KB 级）：压缩流无法伪造，用于「真实压缩 RAR4/RAR5
  双工具端到端上传解压」验收。再生成步骤见 `make_fixtures.py` 模块 docstring
  （注意 rar 7.x 起移除 `-ma4`，无法生成 RAR4 归档）。

`tools/rar_fixtures/verify_fixtures.py` 对**全部**入库样本做结构对账
（版本头、压缩方法与实际压缩比、加密/分卷标志、成员名清单、单样本体积
上限），并与生产管线 RAR4 预扫行为互验（预期放行/拒绝逐一对账）；环境
存在 unar/bsdtar 时额外做真实解压对账（压缩样本解出内容 sha256 与源
指纹一致、真实加密包必须被解压器拒绝）。任何环境都可运行：

```bash
python tools/rar_fixtures/verify_fixtures.py   # 退出码 0=全部通过
```

新增/删除入库样本必须同步该脚本的 `EXPECTATIONS` 期望表。
