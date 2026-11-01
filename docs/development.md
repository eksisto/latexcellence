# LaTeXcellence — 项目开发文档（概览）

> 本文档是开发文档的主入口。各模块的详细文档在同目录下。

**文档索引**：

| 文档 | 内容 |
| --- | --- |
| [development.md](./development.md) | 本文件：项目概述、技术栈、目录结构、启动入口、配置、架构、编码规范 |
| [backend-core.md](./backend-core.md) | 后端核心模块：config、llm_client、logger、project_manager、工具模块 |
| [modules.md](./modules.md) | 所有功能模块 |
| [frontend.md](./frontend.md) | 前端开发文档（样式架构、组件、Hook、状态管理） |
| [testing.md](./testing.md) | 测试规范与用例覆盖 |
| [latex-package-rules.md](./latex-package-rules.md) | 宏包规则表维护指南（新增规则模板 + 评审清单） |
| [function-calling-tools.md](./function-calling-tools.md) | Function Calling 工具体系：全局/私有工具、配置结构 |
| [duckduckgo-challenge.md](./duckduckgo-challenge.md) | DDG 验证码处理 |
| [templategen-optimization-lessons.md](./templategen-optimization-lessons.md) | template_gen 闭环优化复盘：通用性退化事故的原因、教训与改进机制 |
| [plotgen-optimization-lessons.md](./plotgen-optimization-lessons.md) | plot_gen 闭环优化复盘与最佳实践 |
| [prompt-guidelines.md](./prompt-guidelines.md) | 提示词工程原则与约定：小型模型可迁移原则、统一结构、回归口径 |
| [writing-chat-web-access-architecture.md](./writing-chat-web-access-architecture.md) | Writing Chat 网络访问架构：搜索、学术检索、分级抓取、结构化抽取与证据链 |
| [adr/0001-web-access-evaluation.md](./adr/0001-web-access-evaluation.md) | 网络访问阶段 0 的离线评测工具与依赖边界决策 |
| [adr/0002-web-access-stage0-dependencies.md](./adr/0002-web-access-stage0-dependencies.md) | 网络访问阶段 0 的浏览器、HTML 抽取和 PDF 解析依赖验证决策 |
| [windows-temp-permissions.md](./windows-temp-permissions.md) | Windows 下 Codex 沙箱临时目录权限问题排查：pytest 会话级 basetemp、ACL 清理与预防 |
| [multi-user.md](./multi-user.md) | 多用户模式设计（双模式/认证/用户空间/配额/权限协作/沙箱/撤销/邮件/多实例；权威设计参考） |
| [deployment.md](./deployment.md) | 后端部署指南（单用户/多用户单进程/多实例 Compose 部署、systemd 托管、HTTPS 反向代理、安全风险声明、备份、环境变量与升级回滚） |

---

**本文目录**：

- [项目概述](#项目概述)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [tools/ 维护者工具](#tools-维护者工具)
- [启动入口](#启动入口)
- [config.json 结构](#configjson-结构)
- [开发/生产模式](#开发生产模式)
- [用户审核工作流（以错字查找为例）](#用户审核工作流以错字查找为例)
- [架构核心设计模式](#架构核心设计模式)
- [编码规范（关键要点）](#编码规范关键要点)
- [日志](#日志)
- [安全与边界处理](#安全与边界处理)
- [文档维护约定](#文档维护约定)

---

## 项目概述

LaTeXcellence 是一个由 LLM 驱动的智能体论文写作助手。后端 FastAPI + 前端 React/Vite。智能体通过 Function Calling 操作本地 LaTeX 项目（浏览文件、修改段落、生成工程、编译验证），而不是只停留在对话。

基本流程：

1. 打开一个本地 LaTeX 项目目录
2. 选择 `.tex` 文件，系统解析段落并剥离 LaTeX 结构为纯文本
3. LLM 校对/优化纯文本（LaTeX 结构已替换为不可见的 marker，LLM 不能修改它们）
4. 用户审核 diff 结果，逐项确认
5. 确认后，系统将 marker 还原为 LaTeX 并原子性写回源文件

---

## 技术栈

| 层 | 语言 | 框架/关键库 |
| --- | --- | --- |
| 后端 | Python 3.12+ | FastAPI, uvicorn, openai, pydantic v2, httpx |
| 前端 | JavaScript (JSX) | React 19, Vite 6, Tailwind CSS 4, Zustand 5, shadcn/ui |
| 测试 | Python | pytest + pytest-asyncio + httpx |

---

## 目录结构

```
latexcellence/
├── main.py                    # 统一入口（FastAPI + 静态文件挂载）
├── config.json / config.example.json / requirements.txt / pytest.ini
├── AGENTS.md / DEVELOPMENT.md / DESIGN.md / PROJECT_SUMMARY.md / README.md
├── backend/                   # FastAPI 后端（core / utils / modules）
├── frontend/                  # React 前端
├── showcase/                  # 独立展示页（零依赖静态站，详见 showcase.md）
├── screenshots/               # 项目截图（README / 文档配图）
├── tools/                     # 维护者与评测工具（不进入运行时）
├── docs/                      # 开发文档（本目录）
├── tests/                     # pytest 测试
├── static/                    # Vite 构建产物
├── logs/                    # 运行时日志
└── data/                    # 运行期与用户数据统一目录（formats/ plots/ prompts/ style_modify_sessions/ templates/ template_library/ writing_chat_sessions/ web_cache/）
```

> 带逐文件注释的完整目录树见 [PROJECT_SUMMARY.md](../PROJECT_SUMMARY.md)。

> 依赖说明：后端使用 `requirements.txt`，无 `pyproject.toml`。前端包管理文件为 `frontend/package.json`，根目录无 `package.json`。

## tools/ 维护者工具

`tools/` 存放**开发、评测与维护用脚本及 Agent 评测工作区**，与生产运行路径完全隔离：

- 不参与 FastAPI 运行时，也不被 `pytest tests/` 收集（除非脚本本身以 `test_` 开头并放在 `tests/` 下）
- 以 `python tools/<工具>/<入口>.py` 方式手动运行，通常需要本机 TeX Live 或 LLM 配置
- 每个工具子目录自带 `README.md` 说明用途与用法

当前子目录：

| 子目录 | 用途 |
| --- | --- |
| `tools/` 根目录 | `eval_common.py`（人工评估基座）、`tex_path.py`（TeX Live 路径发现）、`generality_gate.py`（Agent 闭环优化通用性门禁，详见 [templategen-optimization-lessons.md](./templategen-optimization-lessons.md)） |
| `latex_packages/` | 宏包规则表维护：离线编译验证与覆盖率报告（详见 [latex-package-rules.md](./latex-package-rules.md)） |
| `formula_ocr_eval/` | 公式 OCR 人工评估（渲染语料 → 跑 OCR → 比对报告） |
| `table_ocr_eval/` | 表格 OCR 人工评估 |
| `typo_check_eval/` | 错字查找人工评估 |
| `expression_opt_eval/` | 表达优化人工评估 |
| `terminology_check_eval/` | 术语一致性人工评估 |
| `paragraph_translate_eval/` | 逐段翻译人工评估 |
| `prompt_comprehension_eval/` | Prompt 理解评测：自动回归检测、多次采样置信区间、历史追踪（详见其 `README.md`） |
| `web_access_eval/` | Writing Chat 网络访问离线评测：搜索、学术、抽取和 grounding 数据集、Pydantic 预测契约、确定性指标与 legacy/v2 重放 |
| `plotgen_opt/` | plot_gen Agent 闭环优化评测工作区 |
| `templategen_opt/` | template_gen Agent 闭环优化评测工作区 |
| `stylemodify_opt/` | style_modify Agent 闭环优化评测工作区 |
| `builtin_templates/` | 内置 LaTeX 模板编译验证与元资产生成（详见 [template_library.md](./modules/template_library.md)「维护者脚本」） |

每个工具子目录的详细用法见其自带 `README.md`。

### Agent 闭环优化规范

「闭环优化」指在 `tools/*_opt/` 评测工作区里「跑基线 → 采集指标与 trace → 诊断 → 改提示词或代码 → 再跑对照」的迭代循环，每轮以通用性门禁与对照组回归收尾。

优化 template_gen / plot_gen / style_modify 等 Agent 时，必须遵守以下
内容边界与门禁（机制已内建在工具中，具体用法见各工具 `README.md`）：

- 评测数据只允许存在于 `tools/` 对应目录，禁止写入 `backend/` 的生产提示词
  或运行时逻辑；
- 生产提示词只允许出现通用能力、策略与协议，禁止硬编码具体学校/模板/期刊、
  字体名、宏包版本、操作系统/路径、评测场景内容；环境能力一律运行时探测或
  按缺失资源协议处理；
- 每个闭环工具维护 `HOLDOUT_SCENARIOS`：日常调优默认排除 holdout，仅最终
  回归可运行；
- 优化提交前运行 `python tools/generality_gate.py check`（提示词体积用
  `size` 子命令跟踪基线，并受每个 Agent 模块的体积预算约束，任一模块
  超限时 `size` 以非零退出）；
- `compare.py` 双 tag 模式自动执行对照组回归判定与硬编码门禁，非零退出即打回。

事故复盘与改进机制详见
[templategen-optimization-lessons.md](./templategen-optimization-lessons.md)。
plot_gen 的闭环复盘见 [plotgen-optimization-lessons.md](./plotgen-optimization-lessons.md)。

---

## 启动入口

### `main.py` — 统一入口

1. 调用 `load_config(config_path)` 从命令行参数加载配置
2. 通过 `setup_logging()` 初始化日志系统
3. 创建 FastAPI 应用，注册所有路由模块
4. 添加 CORS 白名单 + 访问令牌中间件（`backend/core/security_middleware.py`）
5. 在 `lifespan` 中记录启动/关闭日志
6. 生产模式挂载 `static/`，开发模式由 Vite dev server 独立运行

内联路由：`/api/health`、`/api/config/status`、`/api/config`

路由模块均通过显式 `app.include_router()` 注册。

### `showcase/` — 展示页

独立的零依赖静态展示页，用 Node.js 内置 `http` 提供文件服务：

```bash
cd showcase
npm start        # 或 node server.js
```

打开 http://127.0.0.1:4173。架构与维护方式见 [showcase.md](showcase.md)。

---

## config.json 结构

配置文件包含以下顶级段：

| 顶级段 | 关键字段 | 说明 |
| --- | --- | --- |
| `server` | host, port, dev_mode, vite_dev_url, allowed_origins | 服务监听配置与 CORS 白名单（前端部署域名 + 本地开发地址） |
| `security` | access_token | 共享访问令牌；非空时所有 `/api/*` 请求（`/api/health` 除外）必须携带 `X-Latexcellence-Token` 请求头（EventSource 等无法自定义请求头的场景通过 `?token=` 传递） |
| `llm` | api_key, base_url, model, temperature, max_tokens, request_interval, max_concurrency, context_window_tokens, models_probe_url, extra_body, profiles, assignments | LLM 连接与调用参数；`profiles`（模型档案列表）与 `assignments`（角色槽位指派）支持按任务路由不同模型，结构与校验见 [backend-core.md](backend-core.md)「模型路由器与客户端池」；`context_window_tokens`（0=自动探测）与 `models_probe_url`（直连 /models 探测地址）用于上下文窗口动态推导；探测地址与主服务不同源时不携带 API key；`extra_body`（默认 `{}`，空时不发送）透传厂商私有请求体参数，如思维链降档 `{"reasoning_effort": "low"}`，设置页 JSON 文本框编辑 |
| `http` | user_agent | 外部 HTTP 请求的全局 User-Agent（web_search 引擎与 bib_manager Provider 共用） |
| `features` | 各模块的 `enabled` + 特有配置（batch_size, output_dir, timeout 等） | 功能开关与模块级参数 |
| `features.function_calling` | `web_search.*`、`scholarly_research.*`、`project_tools.*`、`edit_tools.*` | 全局 LLM Function Calling 工具集；`web_search.strategy` 控制 legacy/aggregate 搜索，`web_search.page_reading.strategy` 独立控制 legacy/structured 读页。结构化路径的缓存目录、TTL、大小限制、抽取器版本与质量阈值见 `config.example.json`，设置页可热重载；默认均保留 legacy 回滚路径。Tavily 未配置 Key 时使用 keyless 公共接口 |
| `features.writing_chat` | compression_threshold_tokens（0=动态）、compression_headroom_tokens、compression_keep_recent_tokens/turns、summary_token_budget、summary_model 等 | 写作助手上下文压缩触发与摘要预算；摘要旧配置 `summary_max_tokens` 自动迁移并告警 |
| `features.editor` | enabled, assistant_enabled | 网页编辑器开关与编辑器 AI 助手独立子开关（关闭时服务端拒绝助手端点以控制 LLM 开销），见 [modules/editor.md](modules/editor.md) |
| `features.style_modify.agent` | 项目 Agent 的会话目录、轮次、工具/编译上限、大小限制、shadow 编译和清理配置 | style_modify 项目 Agent 模式 |
| `features.template_library` | enabled, user_library_dir, max_upload_mb, allowed_extensions | 模板库开关（关闭时 `/api/templates` 整体关闭、前端隐藏入口）、单用户库目录、上传 zip 上限与扩展名白名单，见 [modules/template_library.md](modules/template_library.md) |
| `logging` | level, dir | 日志级别与存储路径 |
| `multi_user` | enabled, db_path, users_root, staging_root、默认配额、上传限制、会话/回收站 TTL、LLM/登录限流、registration_enabled、admin_*_env、edit_log_*（反向补丁撤销日志：条数上限 / 单操作体积上限 / 保留时长）、writing_chat_retention_days（0 = 不自动删仅统计）、auto_approve_*（注册自动审批：总开关 / 同 IP 账号上限 / CIDR 白名单 / ip2region 库路径（主 + IPv6 补充，按地址族路由）与 GeoLite2 库路径 + 国家·城市白名单；地理查询 ip2region 中国优先、非/未知中国回落 GeoLite2）、trusted_proxies（可信反代，命中才信任 X-Forwarded-For）、register_rate_limit_per_hour | 多用户模式（默认关闭）；开启后强制登录、用户空间隔离、关闭任意路径打开与磁盘浏览，详见 [multi-user.md](multi-user.md)（反向补丁撤销 §8、衍生数据记账与自助清理 §3.3、注册自动审批 §2.1）；部署见 [deployment.md](deployment.md)「注册自动审批」 |
| `sandbox` | mode, image_tex/convert/plot, cpus, memory_mb, pids_limit, tmpfs_mb, max_concurrent_global, max_concurrent_per_user, startup_grace_seconds, warm_pool_enabled | 外部命令沙箱执行（编译/转换/绘图/git 等统一经 SandboxRunner，可进一次性 Docker 容器隔离）。`warm_pool_enabled`（默认 true）为容器预创建池开关：仅 docker 后端生效，auto 模式探测命中 docker 才启用；关闭后每次执行即时建容器（行为与历史版本一致）。完整字段说明与安全基线见 [deployment.md](deployment.md)「Docker 沙箱」，执行模型见 [backend-core.md](backend-core.md)「sandbox/ 包」 |

所有字段有 Pydantic 默认值，可部分配置。敏感项支持经环境变量注入（`SECRET_ENV_PATHS` 注册表）：`LX_LLM_KEY` 优先于 `llm.api_key`；`LATEXCELLENCE_SMTP_PASSWORD` 优先于 `smtp.password`；`LATEXCELLENCE_TAVILY_API_KEY` 优先于 Tavily 搜索 Key，学术数据源同理用 `LATEXCELLENCE_<PROVIDER 大写>_API_KEY`（如 `LATEXCELLENCE_OPENALEX_API_KEY`）。环境变量注入的敏感值均永不写盘，完整清单见 [deployment.md](deployment.md)「环境变量参考」。

这些环境变量也可以统一写进项目根目录的 `.env` 文件（模板见 `.env.example`）：配置解析时（`backend/core/config.py`）经 `backend/core/env.py` 自动加载，优先级为真实环境变量 > `.env` 文件 > config.json。

完整示例见 `config.example.json`（可提交 Git，不含真实密钥）。

---

## 多用户模式（可选）

将 `multi_user.enabled` 设为 `true` 即开启多用户模式（默认关闭，单用户行为完全保持）。**需重启服务**：启动时初始化 SQLite 数据库、创建用户空间根目录并引导首个管理员。开启后：

- 全部 `/api/*`（公开端点除外）强制会话 Cookie 认证，变更类请求需携带 `X-Latexcellence-CSRF` 请求头且其值与会话下发的 CSRF Cookie（`latexcellence_csrf`）一致（双提交防 CSRF）；共享访问令牌逻辑与任意路径打开/磁盘浏览被关闭；
- 首次启动且用户表为空时，从环境变量 `LATEXCELLENCE_ADMIN_USERNAME` / `LATEXCELLENCE_ADMIN_PASSWORD` 引导首个管理员；未设置时使用 CLI 创建；
- 认证依赖 argon2id 密码哈希（登录校验为恒定时长，防时序枚举用户名），会话令牌仅以哈希形式存入 SQLite（WAL 模式）。

**认证/管理 API**（多用户模式下生效，单用户模式返回 404）：

| 端点 | 说明 |
| --- | --- |
| `POST /api/auth/register` | 注册（`registration_enabled=true` 时），写入 `pending` 等待审批 |
| `POST /api/auth/login` / `POST /api/auth/logout` | 登录设置 HttpOnly 会话 Cookie / 登出 |
| `GET /api/auth/me` | 当前用户信息、角色、配额与用量 |
| `GET /api/admin/users` | 用户列表（仅 admin） |
| `POST /api/admin/users/{id}/approve` `/reject` `/disable` `/enable` | 审批 / 拒绝 / 禁用 / 启用 |
| `POST /api/admin/users/{id}/reset-password/preview` | 生成临时密码预览（一次性返回明文，不修改用户密码） |
| `POST /api/admin/users/{id}/reset-password/confirm` | 确认后写入临时密码并吊销该用户全部会话 |
| `PUT /api/admin/users/{id}/quota` | 调整用户配额覆盖（null 跟随全局默认） |

**CLI 管理工具**（无需启动服务器）：

```bash
python -m backend.manage create-admin <username> [--password xxx]
python -m backend.manage reset-password <username> [--password xxx]
python -m backend.manage list-users
python -m backend.manage set-status <username> pending|active|disabled
```

**P2 用户空间与访问控制**（§4.3 / §7）：

- 用户空间目录布局在 `backend/core/multiuser/userspace.py`（项目永久落在 `data/users/<user_id>/projects/<project_id>/`，衍生数据目录按用户隔离）；
- 多用户模式下统一路径解析入口 `path_guard.resolve_user_path(user_id, project_id, file_path)`（基于 `resolve_project_path` 的封装，禁止直接拼接路径）；
- 统一访问控制模块 `backend/core/access_control.py`：`owner / editor / viewer` 三档权限矩阵、`require_project_access()` / `get_projects_for_user()`、基于 workspace 绑定的请求级依赖 `require_workspace(write=...)`，权限**实时查库**（不缓存，变更立即生效）；
- 工作区绑定：`/api/project/open` 在多用户模式下仅接受 `project_id`，打开时写入 `workspace_id → (user_id, project_id, root_path)`；后续所有 `/api/project/*` 请求经 `require_workspace` 实时校验访问权限；
- 端点收紧：`/api/project/browse` 多用户模式返回 403（任意磁盘浏览失效）；`/api/project/open` 拒绝 `path` 字段（任意路径打开失效）；写操作（apply-change / set-main-file / undo / redo）要求非 viewer 权限。共享项目撤销禁令（v1）已在 v2 P3 由反向补丁解禁——undo / redo 只回退当前用户自己的修改，冲突 409、恢复超配额 413，状态端点按当前用户补丁栈实时计算。

**P3 上传与配额**（§8 / §9）：

| 端点 | 说明 |
| --- | --- |
| `GET /api/projects` | 我的项目 + 被共享的项目（含 role / owner / 用量，过滤回收站） |
| `POST /api/projects` | multipart 上传 `.zip` / `.7z` / `.rar` 创建项目（流式写暂存区、安全解压、命名去重 `xx (2)`、配额校验；rar 经外部解压器 unar 优先、缺失回退 bsdtar——解压命令经 SandboxRunner 执行，convert 镜像内置双工具，local 回落探测宿主 PATH、两者皆缺时返回 400 `rar_extractor_missing`；docker 模式 daemon 不可达返回 503 `sandbox_unavailable`） |
| `GET /api/projects/{id}/download` | 打包下载 zip（owner / editor / viewer 均可） |
| `PUT /api/projects/{id}` | 重命名（owner，同名冲突 409） |
| `DELETE /api/projects/{id}` | 项目进回收站（owner，删除即腾出配额） |
| `POST /api/projects/{id}/restore` | 恢复（owner；超限禁止恢复，413） |
| `DELETE /api/projects/{id}/permanent` | 提前永久删除回收站项目（owner） |

- 上传解压安全：拒绝路径穿越 / 绝对路径 / 符号链接 / 设备文件 / 加密包 / 分卷包 / 压缩炸弹（压缩比超限）/ 大小写不敏感重名；限制压缩包大小、文件数、单文件大小、解压总量（≤ 剩余配额）并限时。
- 项目根归一化兼容两种打包布局（含顶层目录 / 直接内容），项目目录固定为 `data/users/<owner_id>/projects/<project_id>/`。
- 配额记账核心在 `backend/core/multiuser/quota.py`：项目空间按活动项目 `size_bytes` 之和统计；衍生数据按磁盘实时统计；后台周期对账（`reconcile_project_sizes`）修正缓存漂移并清理过期回收站 / 暂存区（`maintenance.py`）。
- `apply-change` 多用户写回后刷新配额记账，超限自动回滚并返回 413 `quota_exceeded`。

**P4 权限协作**（§7）：

| 端点 | 说明 |
| --- | --- |
| `GET /api/projects/{id}/permissions` | 项目权限列表（owner + 协作者，owner 专属） |
| `POST /api/projects/{id}/permissions` | 按用户名添加协作者（已存在则更新角色；`editor` / `viewer`；owner 专属） |
| `PUT /api/projects/{id}/permissions/{user_id}` | 修改协作者角色（owner 专属） |
| `DELETE /api/projects/{id}/permissions/{user_id}` | 移除协作者（owner 专属，权限即时生效） |
| `POST /api/projects/{id}/transfer` | 转交项目 owner（`username` + `keep_access`；原 owner 转后 viewer 或退出） |

- 协作者必须是 `active` 用户，不能是 owner 自身；未知用户 404 `user_not_found`，非 active 400 `invalid_user`；非 owner 管理操作一律 403 `no_permission`。
- 权限实时查库：添加 / 改角色 / 移除后下一次请求立即生效（被移除者立即失去访问）。
- 转交前校验接收方项目空间配额（超限 413 `quota_exceeded`，目录与归属不变）；转交时项目目录迁移到新 owner 空间（`data/users/<new_owner_id>/projects/<project_id>/`），新 owner 同名项目自动生成 `xx (2)`，已打开该项目的 workspace 绑定与文件树同步更新。
- viewer 写保护（P2 已实现）：viewer 写操作 403。共享项目撤销已由 v2 P3 反向补丁解禁（按 actor 归属，转交后各用户只能撤销自己的修改）。

**P6 迁移与存量隔离**（§7.2 / §13）：

- 会话类资源（写作会话 / style_modify 会话 / 模板 / 绘图）按用户隔离：`userspace.user_resource_root(subdir, legacy_root)` 统一解析入口——多用户模式按请求级当前用户返回 `<users_root>/<user_id>/<子目录>/`，单用户模式回退存量全局路径（行为不变）。当前用户由路由依赖 `current_user_context` 注入（writing-chat / style-modify / template-gen / plot-gen 四个路由挂载）；后台清理任务经 `userspace.iterate_user_resource_roots()` 枚举全部用户资源目录。
- 存量数据迁移：`python -m backend.manage migrate-legacy [--dry-run]` 把存量全局目录（`data/writing_chat_sessions/`、`data/style_modify_sessions/`、`data/templates/`、`data/plots/`、`data/formats/`、`data/prompts/`）幂等复制到管理员用户空间对应子目录；目标文件已存在则跳过（不覆盖迁移后新数据）；复制后逐文件校验并输出报告。核心逻辑在 `backend/core/multiuser/migrate.py`。

基础设施、认证与项目实现的详细说明见 [docs/backend-core.md](backend-core.md)，完整设计见 [docs/multi-user.md](multi-user.md)，服务器部署指南（HTTPS、风险声明、备份与运维）见 [docs/deployment.md](deployment.md)。

> 运维提示：登录限流按"用户名 + 客户端 IP"计数。若部署在反向代理（nginx 等）之后，`request.client.host` 恒为代理 IP，"每 IP"维度会退化，需按部署接入受信代理 / 真实 IP 传递后才会生效。

---

## 开发/生产模式

| 模式 | `dev_mode` | FastAPI 行为 | 前端行为 |
| --- | --- | --- | --- |
| 开发 | `true` | 不挂载 `static/`；CORS 白名单默认含 `localhost:5173` / `127.0.0.1:5173` | 由 Vite dev server（默认 5173）独立运行，`vite.config.js` 代理 `/api` 到 `localhost:8000` |
| 生产 | `false` | 挂载 `static/` 目录作为静态文件服务（`SPAStaticFiles`，带 SPA 回退，见 `backend/utils/spa_static_files.py`） | 前端已构建为 `static/` 下的产物，由 FastAPI 直接提供 |

**切换到生产模式**：

1. 将 `config.json` 中 `server.dev_mode` 设为 `false`。
2. 在 `frontend/` 目录执行 `npm run build`，产物输出到根目录 `static/`。
3. 运行 `python main.py config.json`。
4. 访问 http://localhost:8000。

> 注意：`main.py` 仅在 `dev_mode == false` 时挂载 `static/`。若未切换就直接访问 8000 端口，将只能看到 API 接口而非前端页面。

> SPA 回退：前端使用 BrowserRouter（history 路由），生产模式下刷新 / 直达 `/formula-ocr` 等客户端路由时物理文件不存在。`SPAStaticFiles` 对期望 HTML 的页面导航回退返回 `index.html`；脚本 / 样式等资源缺失与 `/api/*` 未命中仍保持 404。详见 [backend-core.md](backend-core.md)。

**前后端分离部署（远程前端 + 本地后端）**：

前端可独立部署为静态站点（如 `https://latexcellence.example.com`），用户浏览器直接访问网站、自行下载并本地运行后端。此模式下：

- 前端 API 地址运行时可配置（设置页 → localStorage → `VITE_API_BASE_URL` → 默认 `http://localhost:8000/api`）。前后端分离部署时在设置页填写完整后端地址（如 `http://localhost:8000/api`）。
- 维护者可在 `frontend/.env`（模板见 `.env.example`）中设置 `VITE_API_BASE_URL` 为自己的后端地址并重新构建，用户打开前端即默认连接该地址，无需逐个在设置页填写。
- 后端返回的相对 URL（如 git-diff 的 `pdf_url`）由前端统一补全为完整后端地址并追加令牌参数。
- `server.allowed_origins` 必须包含前端域名；`security.access_token` 建议设置随机长字符串，前端在设置页填写同名令牌。
- 后端应保持绑定 `127.0.0.1`，只服务本机浏览器。

---

## 用户审核工作流（以错字查找为例）

```
1. 用户在 WorkspacePage 打开 LaTeX 项目目录 → 文件树显示
2. 用户选择 .tex 文件 → 进入 TypoCheckPage
3. 点击「Preview」→ 后端剥离 LaTeX → 前端展示纯文本段落
4. 点击「Check Typos」→ LLM 逐段检查 → 校验 JSON → 验证 marker 完整性 → 展示 diff
5. 用户逐项审核，点击「Apply Fix」→ 后端还原 marker → 原子写入（MD5 校验 + 备份）
```

---

## 架构核心设计模式

### 分层架构

```
router.py (薄层：参数校验、HTTP 异常转换、SSE 封装)
    ↓
service.py (厚层：业务编排、LLM 调用、marker 还原)
    ↓
core/ + utils/ (基础设施：配置、LLM 客户端、项目管理、文件 IO、Agent 文件工具、JSON 校验、工具结果统一协议)
```

### 单例模式

无状态的基础设施通过模块级工厂函数实现单例：`get_config()`、`get_llm_client()`、`get_workspace_manager()`、`get_backup_manager()`。持有请求级状态的服务（typo_check / expression_opt / code_review / terminology_check / paragraph_translate 的 `get_service()`）每次请求创建独立实例，避免并发请求互相覆盖 marker 映射等状态。跨请求共享的 `operation_id` 上下文存放在进程级共享存储中。

### 配置热重载

所有方法内实时调用 `get_config()`，不在 `__init__` 中缓存配置。

### 错误处理

前置条件检查（快速失败）→ try/except + 指数退避重试 → 日志后重抛。复用标准异常，禁止自定义异常类。

### 提示词管理

所有 LLM 提示词集中存放于 `backend/core/prompts.py`，通过 `get_prompt(module, key, **kwargs)` 参数化获取。当前按 Qwen3.6-35B-A3B 优化。

**结构规范**：

- 常规提示词顺序固定为 <role> → <task> → <critical_rules> → [<input>] → <output_format> → <examples>
- 共享规则常量（MARKER_PROTECTION_RULES、JSON_ONLY_RULE 等）避免跨模块重复书写
- Agent system prompt 采用阶段注入策略：核心 system 只保留强制工作流与硬约束，规划细节（`planning_system`）在开始时注入，编译恢复协议（`compile_phase_system`）在首次编译后注入，视觉质量门禁（`validation_phase_system`）在首次成功编译后注入
- Agent 规则用一致的 XML 分区：workflow / quality gate / tool protocol / failure budget 各占一个成对闭合标签；每个阶段块用 `<stage_contract>` 记录 entry（进入条件）、input（输入）、output（输出）、done（完成条件）、fallback（失败回退）
- 每个 Agent 模块设体积预算（template_gen ≤ 22000、plot_gen ≤ 15000、style_modify ≤ 5000 字符），由 `tools/generality_gate.py size` 门禁

**评测约束**：修改提示词必须通过 prompt_comprehension_eval 回归验证。Agent 提示词瘦身必须维持或改善 generality_gate check 结果。


---

## 编码规范（关键要点）

### Python

| 规范项 | 要求 |
| --- | --- |
| 导入顺序 | 标准库 → 第三方 → 应用内部；同包内用相对导入（`from . import ...`），跨包用绝对导入 |
| 类型注解 | 强制注解，使用 `list[X]` / `dict[K, V]` / `X \| None` |
| 字符串引号 | docstring/f-string 双引号；正则 `r"..."` |
| 路径操作 | `pathlib.Path`，禁止 `os.path` |
| 文件编码 | 显式 `encoding="utf-8"` |
| 命名 | 类 PascalCase、函数/变量 snake_case、常量 UPPER_SNAKE |
| Pydantic | 仅 v2 API（`model_validate()`、`model_dump()`） |
| 调试 | `logger.debug()`，禁止 `print()` |

### JavaScript/JSX

| 规范项 | 要求 |
| --- | --- |
| 导入 | ESM `import`，相对路径带 `./` |
| 字符串 | 双引号，无分号，2 空格缩进 |
| 状态管理 | Zustand 箭头函数 setter |
| API 调用 | 通过 `api/index.js` 的 `request()` |

---

## 约束机器化

AGENTS.md 的编码约束已尽量实现为机器检查，机器检查是准绳、文字只是解释。各层职责：

| 层 | 载体 | 覆盖内容 |
| --- | --- | --- |
| 静态检查 | `ruff.toml` | `print()`（T201）、`os.path`（PTH，例外 `os.path.islink`）、类型注解现代化（UP006/007/035/045）、导入排序（I）、pyflakes 基础（F）；豁免均带理由注释（vendored ip2region、bytes 路径的 upload_rar、path_guard 的跨平台 isabs 等） |
| 项目约定扫描 | `scripts/check_conventions.py` | ruff 覆盖不到的三类：Pydantic v1 残留（`parse_obj`/`@validator`/`class Config`）、path_guard 绕过（`xxx / file_path`、`os.path.join(root, ...)`、同行 `resolve()+is_relative_to()`，白名单为解压根/rglob 合法包含检查）、文档硬编码数量（「N 个模块」等，豁免「上限/最多/返回」限值措辞，自测用例内置） |
| 提交前拦截 | `.pre-commit-config.yaml` | black、prettier、ruff、约束扫描四个 local hook |
| 统一验证入口 | `scripts/check.sh` | 顺序调用下面两个脚本，**完成代码修改后必须运行并通过**；`--install` 装依赖、`--build` 追加前端构建、`--skip-docker` 跳过真容器冒烟 |
| 后端校验 | `scripts/check_backend.sh` | ruff + `black --check` + 约束扫描 + `pytest tests/ -q`；CI 的 `python-check` job 直接调用它（`--install --skip-docker`） |
| 前端校验 | `scripts/check_frontend.sh` | `npm test`（node --test）+ 可选 `npm run build`；CI 的 `web-check` job 调用它（`--install --build`） |
| 开发工具依赖 | `requirements-dev.txt` | ruff / black 精确锁版本（生产 `requirements.txt` 不含），CI 与本地装同一版本 |
| 提交时格式化 | `.pre-commit-config.yaml` | black / prettier 在**提交时点**统一格式化。刻意不做编辑期（agent 编辑后）自动格式化：格式化会改动 agent 正在编辑的文件，令其文件状态跟踪失效、触发整文件重读，并与连续编辑产生竞态 |
| 编辑器文档提醒 | `.zcode/config.json` hooks + `scripts/zcode_post_edit_hook.py` | PostToolUse（Edit/Write）在编辑 `backend/`、`frontend/src/` 或配置文件时经 `additionalContext` 注入文档同步提醒（maintain-latexcellence-docs / latexcellence-config-sync）。hook 只读、不修改任何文件。Windows 侧使用需把 command 改为 process 类型或调整解释器名 |

仓库 CI 分两条链路，触发条件与覆盖面不同，缺一不可：

| 链路 | 载体 | 触发 | 内容 |
| --- | --- | --- | --- |
| 代码门禁 | `.gitlab-ci.yml` 的 `check` 阶段（`python-check` / `web-check`） | 任意 MR 与默认分支 push（**不做路径过滤**） | 装依赖后调用 `scripts/check_backend.sh --install --skip-docker` / `scripts/check_frontend.sh --install --build`，与本地结论同源 |
| 评测回归 | `.gitlab-ci.yml` 的 `eval` 阶段 | 命中提示词 / 模型 / 解析器 / 评测工具路径，或定时、手动 | 离线门禁 + LLM 评测与基线比对，见 [eval-regression-ci.md](eval-regression-ci.md) |

CI job 不重写任何检查命令：检查逻辑只存在于 `scripts/check_*.sh`，CI 侧只负责装依赖（`--install`），避免「本地 CI 两套命令」漂移。

注意事项：

- 开发工具（ruff / black）锁在 `requirements-dev.txt`，与生产依赖分离：
  `pip install -r requirements.txt -r requirements-dev.txt`（pre-commit 仍以
  `language: system` 调用 `.venv` 内的同名命令，版本一致才不会出现「本地全绿、CI 报红」）。
- 约束扫描器新增规则时，必须同步在脚本内补自测用例并跑通。
- `pytest.mark.docker` 真容器冒烟在 CI 里被 `--skip-docker` 排除（job 无 daemon）；
  本机 daemon 与宿主机不共享 `/tmp` 时同样会失败，属环境问题而非代码问题，
  详见 [testing.md](testing.md)。
- 逐段检查、配置同步、错误消息翻译等**特性专属流程约束**不进 linter，
  以 workspace skill 形式按需加载（`.agents/skills/`），AGENTS.md 只留指针。

## 日志

- 使用 `logging` + `TimedRotatingFileHandler`，按天旋转，保留 30 天
- 核心调用点必须记录：LLM 请求/响应、文件读写、API 入口/出口、异常

---

## 安全与边界处理

| 场景 | 处理策略 |
| --- | --- |
| `\include`/ `\input` | 多文件展开，记录依赖图 |
| verbatim 等环境 | 整体替换为 marker，LLM 不可修改 |
| 控制符号（`\?`/`\'`/`\7`/`\~{n}`、`\`+换行、未配对 `\[`/`\(`） | 整体替换为 marker，不残留反斜杠/花括号 |
| 行内数学 `$...$` | 非贪婪匹配；`\$`（如 `\$500`）由转义字符规则处理，不进入数学匹配 |
| 内联逐字（`\verb`/`\verb*`/`\lstinline`） | 整体替换为 marker，逐字内容 LLM 不可修改 |
| LLM 调用 rate limit | 指数退避重试（最多 3 次） |
| LLM 修改/删除 marker | 后处理验证完整性，缺失时回退原文 |
| LLM JSON 输出不符 | `retry_with_validation()` 反馈重试 |
| LLM 输出的 LaTeX 含损坏内容（控制字符/括号不配平） | `latex_json_guard.py` 内容校验拦截并回喂重试（分隔符协议路径用 `relaxed=True` 宽松模式） |
| 用户审核期间修改文件 | MD5 校验，不一致拒绝写入 |
| 撤销/重做 | BackupManager 维护操作栈（最多 50 层） |
| API 密钥 | 加入 `.gitignore`，提供脱敏模板，环境变量优先 |

---

## 文档维护约定

- 新增/删除模块或变更核心文件路径时，同步更新 `PROJECT_SUMMARY.md` 与本目录下的相关文档。
- 模块级设计变更（接口、算法流程、关键约束）必须同步更新 `docs/modules/<module>.md`。
- 新增后端错误消息时，同步更新 `frontend/src/i18n/locales/zh.json`、`zh-TW.json` 和 `en.json` 的 `errors` 命名空间。
- 避免在文档中硬编码会频繁变化的文件/模块/组件数量（如“X 个功能模块”），使用“多个”“若干”等模糊描述，或直接引用目录结构。
