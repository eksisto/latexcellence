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

---

## 项目概述

LateXcellence 是一个 AI 驱动的 LaTeX 文档助手。后端 FastAPI + 前端 React/Vite，通过 LLM 对 `.tex` 文件进行错字查找、表达优化、公式识别、模板生成等操作。

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
├── main.py                         # 启动入口
├── config.json                     # 配置文件
├── config.example.json             # 配置模板
├── requirements.txt                # Python 依赖
├── AGENTS.md / DEVELOPMENT.md / DESIGN.md / PROJECT_SUMMARY.md / README.md
│
├── backend/
│   ├── core/                       # 核心基础设施
│   │   ├── config.py               # 配置读写、热重载、Pydantic 校验
│   │   ├── llm_client.py           # OpenAI API 统一封装（流式 + 重试 + 限流）
│   │   ├── logger.py               # 统一日志（按日期旋转）
│   │   ├── workspace_manager.py    # 多工作区管理
│   │   ├── project_manager.py      # LaTeX 项目管理（文件树、段落、撤销栈）
│   │   ├── prompts.py              # 中央提示词管理（所有 LLM 提示词模板）
│   │   ├── tool_registry.py        # Function Calling 工具注册表
│   │   └── base_paragraph_check.py # 逐段检查服务基类
│   │
│   ├── utils/                      # 工具模块
│   │   ├── file_ops.py             # 文件读写（MD5、原子写入、BackupManager）
│   │   ├── input_resolver.py       # \input/\include 递归解析器
│   │   ├── json_utils.py           # LLM JSON 输出解析/校验/重试
│   │   ├── latex_json_guard.py     # LaTeX×JSON 转义防护（内容校验，跨模块共享）
│   │   ├── latex_parser.py         # LaTeX → 纯文本（Marker 保护）
│   │   ├── latex_literals.py       # LaTeX 字面内容识别（逐字命令/URL 参数，parser 与 guard 共享）
│   │   ├── tex_compile.py          # 统一 TeX 编译执行（texfot 过滤 + 引擎自动检测 + PDF/A 支持）
│   │   ├── compile_error_translator.py  # 编译错误 LLM 翻译（compile + git_diff 复用）
│   │   ├── latexindent_utils.py    # latexindent.pl 调用封装
│   │   ├── builtin_formatter.py    # 内置轻量 LaTeX 格式化器
│   │   ├── project_tools/          # LLM Function Calling 工具集（浏览 + 编辑）
│   │   ├── web_search/             # 联网搜索多引擎抽象
│   │   └── scholarly_research/     # OpenAlex 学术检索与引文追踪工具
│   │
│   └── modules/                    # 各功能模块（router + service）
│       ├── project_router.py
│       ├── typo_check/             # 错字查找
│       ├── expression_opt/         # 表达优化
│       ├── terminology_check/      # 术语一致性检查
│       ├── paragraph_translate/    # 逐段翻译
│       ├── writing_chat/           # 写作助手聊天
│       ├── bib_manager/            # 参考文献管理
│       ├── formula_ocr/            # 公式识别
│       ├── table_ocr/              # 表格识别
│       ├── template_gen/           # 模板生成
│       ├── plot_gen/               # 科研绘图 Agent
│       ├── code_review/            # LaTeX 代码审查
│       ├── compile/                # 自动编译
│       ├── format_converter/       # 文档格式转换
│       ├── style_modify/           # 样式修改
│       └── git_diff/               # Git 差异高亮
│
├── frontend/
│   ├── index.html / package.json / vite.config.js
│   └── src/
│       ├── main.jsx / App.jsx / index.css
│       ├── api/index.js            # API 层封装
│       ├── store/useStore.js       # Zustand 全局状态
│       ├── hooks/                  # 自定义 hooks
│       ├── components/             # 共享组件
│       │   ├── ui/                 # shadcn/ui 封装
│       │   └── ...
│       └── pages/                  # 页面组件
│
├── static/                         # Vite 构建产物
├── prompts/                        # 自定义提示词
├── templates/                      # 生成的 LaTeX 模板
├── writing_chat_sessions/          # 聊天数据
├── clean-cache.sh                  # 清理缓存脚本
├── docs/                           # 项目文档
└── tests/                          # 测试
```

> 依赖说明：后端使用 `requirements.txt`，无 `pyproject.toml`；前端包管理文件为 `frontend/package.json`，根目录无 `package.json`。

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

---

## config.json 结构

配置文件包含以下顶级段：

| 顶级段 | 关键字段 | 说明 |
| --- | --- | --- |
| `server` | host, port, dev_mode, vite_dev_url | 服务监听配置 |
| `llm` | api_key, base_url, model, temperature, max_tokens, request_interval, max_concurrency | LLM 连接与调用参数 |
| `features` | 各模块的 `enabled` + 特有配置（batch_size, output_dir, timeout 等） | 功能开关与模块级参数 |
| `features.function_calling` | `web_search.*`、`scholarly_research.*`、`project_tools.*`、`edit_tools.*` | 全局 LLM Function Calling 工具集；学术研究支持 OpenAlex API Key、结果上限和请求超时配置 |
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
| 导入顺序 | 标准库 → 第三方 → 应用内部（禁止相对导入） |
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
