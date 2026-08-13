# LateXcellence — 项目开发文档（概览）

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
| [closed-loop-optimization-lessons.md](./closed-loop-optimization-lessons.md) | Agent 闭环优化复盘：通用性退化事故的原因、教训与改进机制 |

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

LateXcellence 是一个由 LLM 驱动的智能体论文写作助手。后端 FastAPI + 前端 React/Vite，智能体通过 Function Calling 真正操作本地 LaTeX 项目（浏览文件、修改段落、生成工程、编译验证），而不是只停留在对话。

基本流程：

1. 打开一个本地 LaTeX 项目目录
2. 选择 `.tex` 文件，系统解析段落并剥离 LaTeX 结构为纯文本
3. LLM 在纯文本上执行校对/优化（LaTeX 结构被替换为不可见的 marker，LLM 不能修改它们）
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
├── tools/                     # 维护者与评测工具（不进入运行时）
├── docs/                      # 开发文档（本目录）
├── tests/                     # pytest 测试
├── static/                    # Vite 构建产物
└── prompts/ templates/ formats/ plots/ logs/ writing_chat_sessions/  # 运行期与用户数据目录
```

> 带逐文件注释的完整目录树见 [PROJECT_SUMMARY.md](../PROJECT_SUMMARY.md)。

> 依赖说明：后端使用 `requirements.txt`，无 `pyproject.toml`；前端包管理文件为 `frontend/package.json`，根目录无 `package.json`。

## tools/ 维护者工具

`tools/` 存放**开发、评测与维护用脚本及 Agent 评测工作区**，与生产运行路径完全隔离：

- 不参与 FastAPI 运行时，也不被 `pytest tests/` 收集（除非脚本本身以 `test_` 开头并放在 `tests/` 下）
- 以 `python tools/<工具>/<入口>.py` 方式手动运行，通常需要本机 TeX Live 或 LLM 配置
- 每个工具子目录自带 `README.md` 说明用途与用法

当前子目录：

| 子目录 | 用途 |
| --- | --- |
| `tools/` 根目录 | `eval_common.py`（人工评估基座）、`tex_path.py`（TeX Live 路径发现）、`generality_gate.py`（Agent 闭环优化通用性门禁，详见 [closed-loop-optimization-lessons.md](./closed-loop-optimization-lessons.md)） |
| `latex_packages/` | 宏包规则表维护：离线编译验证与覆盖率报告（详见 [latex-package-rules.md](./latex-package-rules.md)） |
| `formula_ocr_eval/` | 公式 OCR 人工评估（渲染语料 → 跑 OCR → 比对报告） |
| `table_ocr_eval/` | 表格 OCR 人工评估 |
| `typo_check_eval/` | 错字查找人工评估 |
| `expression_opt_eval/` | 表达优化人工评估 |
| `terminology_check_eval/` | 术语一致性人工评估 |
| `paragraph_translate_eval/` | 逐段翻译人工评估 |
| `prompt_comprehension_eval/` | Prompt 理解评测 |
| `plotgen_opt/` | plot_gen Agent 闭环优化评测工作区 |
| `templategen_opt/` | template_gen Agent 闭环优化评测工作区 |
| `stylemodify_opt/` | style_modify Agent 闭环优化评测工作区 |

每个工具子目录的详细用法见其自带 `README.md`。

### Agent 闭环优化规范

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
  `size` 子命令跟踪基线）；
- `compare.py` 双 tag 模式自动执行对照组回归判定与硬编码门禁，非零退出即打回。

事故复盘与改进机制详见
[closed-loop-optimization-lessons.md](./closed-loop-optimization-lessons.md)。

---

## 启动入口

### `main.py` — 统一入口

1. 调用 `load_config(config_path)` 从命令行参数加载配置
2. 通过 `setup_logging()` 初始化日志系统
3. 创建 FastAPI 应用，注册所有路由模块
4. 配置 CORS（允许所有来源）
5. 在 `lifespan` 中执行启动/关闭日志
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
| `server` | host, port, dev_mode, vite_dev_url | 服务监听配置 |
| `llm` | api_key, base_url, model, temperature, max_tokens, request_interval, max_concurrency | LLM 连接与调用参数 |
| `http` | user_agent | 外部 HTTP 请求的全局 User-Agent（web_search 引擎与 bib_manager Provider 共用） |
| `features` | 各模块的 `enabled` + 特有配置（batch_size, output_dir, timeout 等） | 功能开关与模块级参数 |
| `features.function_calling` | `web_search.*`、`scholarly_research.*`、`project_tools.*`、`edit_tools.*` | 全局 LLM Function Calling 工具集；学术研究支持 OpenAlex API Key、结果上限和请求超时配置；联网搜索支持 Tavily / SearXNG / DuckDuckGo 引擎与 API Key 配置，Tavily 默认 `advanced` 深度并返回合成答案/原始正文，`deep_read_pages` 默认 0（由 open_page / find_in_page 按需读取） |
| `features.style_modify.agent` | 项目 Agent 的会话目录、轮次、工具/编译上限、大小限制、shadow 编译和清理配置 | style_modify 项目 Agent 模式 |
| `logging` | level, dir | 日志级别与存储路径 |

所有字段有 Pydantic 默认值，可部分配置。`LX_LLM_KEY` 环境变量优先于 `config.json` 中的 `api_key`。

参考完整示例见 `config.example.json`（可提交 Git，不含真实密钥）。

---

## 开发/生产模式

| 模式 | `dev_mode` | FastAPI 行为 | 前端行为 |
| --- | --- | --- | --- |
| 开发 | `true` | 不挂载 `static/`；CORS 允许所有来源 | 由 Vite dev server（默认 5173）独立运行，`vite.config.js` 代理 `/api` 到 `localhost:8000` |
| 生产 | `false` | 挂载 `static/` 目录作为静态文件服务 | 前端已构建为 `static/` 下的产物，由 FastAPI 直接提供 |

**切换到生产模式**：

1. 将 `config.json` 中 `server.dev_mode` 设为 `false`。
2. 在 `frontend/` 目录执行 `npm run build`，产物输出到根目录 `static/`。
3. 运行 `python main.py config.json`。
4. 访问 http://localhost:8000。

> 注意：`main.py` 仅在 `dev_mode == false` 时挂载 `static/`；若未切换就直接访问 8000 端口，将只能看到 API 接口而非前端页面。

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
core/ + utils/ (基础设施：配置、LLM 客户端、项目管理、文件 IO、JSON 校验)
```

### 单例模式

所有服务/客户端通过模块级工厂函数实现单例：`get_config()`、`get_llm_client()`、`get_workspace_manager()`、`get_backup_manager()` 及各模块 `get_service()`。

### 配置热重载

所有方法内实时调用 `get_config()`，不在 `__init__` 中缓存配置。

### 错误处理

前置条件检查（快速失败）→ try/except + 指数退避重试 → 日志后重抛。复用标准异常，禁止自定义异常类。

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

## 日志

- 使用 `logging` + `TimedRotatingFileHandler`，按天旋转，保留 30 天
- 核心调用点必须记录：LLM 请求/响应、文件读写、API 入口/出口、异常

---

## 安全与边界处理

| 场景 | 处理策略 |
| --- | --- |
| `\include`/ `\input` | 递归解析，记录依赖图 |
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
- 新增后端错误消息时，同步更新 `frontend/src/i18n/locales/zh.json` 和 `en.json` 的 `errors` 命名空间。
- 避免在文档中硬编码会频繁变化的文件/模块/组件数量（如“X 个功能模块”），使用“多个”“若干”等模糊描述，或直接引用目录结构。
