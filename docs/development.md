# LateXcellence — 项目开发文档（概览）

> 本文档为项目开发主文档。各模块详细文档见同目录下其他文件。

**文档索引**：

| 文档 | 内容 |
|------|------|
| [development.md](./development.md) | 本文件：项目概述、技术栈、目录结构、启动入口、配置、架构、编码规范 |
| [backend-core.md](./backend-core.md) | 后端核心模块：config、llm_client、logger、project_manager、工具模块 |
| [modules.md](./modules.md) | 所有功能模块 |
| [frontend.md](./frontend.md) | 前端开发文档（样式架构、组件、Hook、状态管理） |
| [testing.md](./testing.md) | 测试规范与用例覆盖 |

---

## 项目概述

LateXcellence 是一个 AI 驱动的 LaTeX 文档助手。后端 FastAPI + 前端 React/Vite，通过 LLM 对 `.tex` 文件进行错字查找、表达优化、公式识别、模板生成等操作。

核心工作流：

1. 打开一个本地 LaTeX 项目目录
2. 选择 `.tex` 文件，系统解析段落并剥离 LaTeX 结构为纯文本
3. LLM 在纯文本上执行校对/优化（LaTeX 结构被替换为不可见的 marker，LLM 不能修改它们）
4. 用户审核 diff 结果，逐项确认
5. 确认后，系统将 marker 还原为 LaTeX 并原子性写回源文件

---

## 技术栈

| 层 | 语言 | 框架/关键库 |
|---|---|---|
| 后端 | Python 3.12+ | FastAPI, uvicorn, openai, pydantic **v2**, httpx |
| 前端 | JavaScript (JSX) | React 19, Vite 6, Tailwind CSS 4, Zustand 5, shadcn/ui (style: base-nova) |
| 测试 | Python | pytest + pytest-asyncio + httpx |

---

## 完整目录结构

```
latexcellence/
├── main.py                         # 统一入口，启动 FastAPI + 挂载前端
├── config.json                     # 运行时配置文件
├── config.example.json             # 配置文件模板（不含密钥，可提交 Git）
├── requirements.txt                # Python 依赖
├── .gitignore                      # Git 忽略规则
├── AGENTS.md                       # AI 编程助手规范
├── DEVELOPMENT.md                  # 项目开发文档
├── DESIGN.md                       # 设计技术文档
├── PROJECT_SUMMARY.md              # 项目概要（供 AI 快速读取）
├── README.md                       # 项目简介
├── clean-cache.sh                  # 清理缓存脚本
│
├── backend/
│   ├── __init__.py
│   │
│   ├── core/                       # 核心基础设施
│   │   ├── __init__.py
│   │   ├── config.py               # config.json 读写、热重载、Pydantic 校验
│   │   ├── llm_client.py           # OpenAI API 统一封装（含流式 + 重试 + 限流）
│   │   ├── logger.py               # 统一日志（TimedRotatingFileHandler，按日期旋转）
│   │   ├── workspace_manager.py    # 多工作区管理：workspace_id → LatexProject
│   │   ├── project_manager.py      # LaTeX 项目目录管理：打开、文件树、段落提取、原地修改、撤销栈
│   │   └── base_paragraph_check.py # 逐段检查服务基类（typo/expression/formatting/terminology 共享）
│   │
│   ├── utils/                      # 工具模块
│   │   ├── __init__.py
│   │   ├── file_ops.py             # 文件读写辅助：MD5 校验、原子写入、撤销栈（BackupManager）
│   │   ├── input_resolver.py       # \input/\include 递归解析器（多文件段落展开）
│   │   ├── json_utils.py           # LLM JSON 输出解析/校验/重试
│   │   └── latex_parser.py         # LaTeX → 纯文本转换（Marker 保护 + MARKER_PATTERN 常量）
│   │
│   └── modules/                    # 各功能模块 = 路由 + 服务
│       ├── __init__.py
│       ├── project_router.py       # 项目管理 API 路由
│       │
│       ├── typo_check/             # 错字查找
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：preview / check / apply / stream
│       │   └── service.py          # 业务逻辑：LLM 编排 + Pydantic 校验 + marker 保护
│       │
│       ├── expression_opt/         # 表达优化
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：preview / optimize / apply / stream / prompts CRUD
│       │   ├── service.py          # 业务逻辑：LLM 编排 + marker 保护 + 自定义 prompt 合并
│       │   └── prompt_manager.py   # 自定义提示词管理：CRUD + 激活状态追踪
│       │
│       ├── terminology_check/      # 术语一致性检查
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：preview / check / apply / stream
│       │   ├── service.py          # 业务逻辑：三步走（提取 → 聚类 → 生成结果）
│       │   ├── extractor.py        # LLM 术语提取器 + 语义聚类器
│       │   └── models.py           # Pydantic 模型：TermChange / TermCluster / TermClusterResult
│       │
│       ├── writing_chat/           # 写作助手聊天
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：send(SSE) / generate-edit / apply-edit / message ops / sessions / assistants
│       │   ├── service.py          # 业务逻辑：会话编排 + 纯对话流式 + 编辑双轮 + 压缩
│       │   ├── context.py          # ConversationContext：磁盘持久化 + 压缩/撤回/快照 + 重命名
│       │   ├── models.py           # Pydantic 模型：ChatMessage / EditOp / LlmEditResult 等
│       │   └── prompt_manager.py   # 助手提示词管理：CRUD + 激活状态追踪 + _active.txt 模式
│       │
│       ├── bib_manager/            # 参考文献管理
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：analyze-format / extract / generate / read-bib / save / list-bib-files
│       │   ├── service.py          # 业务逻辑：格式分析 + 元数据提取（文本/图片）+ BibTeX 生成 + .bib 操作
│       │   └── models.py           # Pydantic 模型：ReferenceFormatResult / ReferenceMetadata / BibEntry
│       │
│       ├── formula_ocr/            # 公式识别
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：recognize（图片上传）
│       │   └── service.py          # 业务逻辑：图片预处理 + LLM Vision API 调用
│       │
│       ├── table_ocr/              # 表格识别
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：recognize（图片上传）
│       │   └── service.py          # 业务逻辑：图片预处理 + LLM Vision API 调用
│       │
│       ├── template_gen/           # 模板生成
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：generate / save
│       │   └── service.py          # 业务逻辑：LLM 生成 + 模板组装 + 文件保存
│       │
│       ├── formatting/             # 源码格式化
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：preview / check / apply / stream
│       │   └── service.py          # 业务逻辑：LLM 编排 + Pydantic 校验（原始 LaTeX 排版）
│       │
│       ├── compile/                # 自动编译
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：run
│       │   └── service.py          # 业务逻辑：subprocess 调用 latexmk + LLM 错误翻译
│       │
│       ├── markdown_converter/     # Markdown 转 LaTeX
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：convert
│       │   └── service.py          # 业务逻辑：subprocess 调用 pandoc
│       │
│       ├── style_modify/           # LaTeX 样式修改
│       │   ├── __init__.py
│       │   ├── router.py           # API 路由：modify
│       │   └── service.py          # 业务逻辑：LLM 根据描述修改 LaTeX 源码
│       │
│       └── git_diff/               # Git 差异高亮
│           ├── __init__.py
│           ├── router.py           # API 路由：refs / render-pdf / pdf 下载
│           └── service.py          # 业务逻辑：git show + 依赖展开 + difflib + latexmk 编译
│
├── frontend/
│   ├── index.html                  # HTML 入口，内联暗色模式初始化脚本
│   ├── package.json                # 前端依赖：React 19, Vite 6, Tailwind 4, Zustand, shadcn
│   ├── package-lock.json           # 依赖锁定
│   ├── vite.config.js              # Vite 配置：@ 别名、代理 /api → localhost:8000
│   ├── jsconfig.json               # IDE 路径别名配置
│   ├── components.json             # shadcn/ui 配置
│   └── src/
│       ├── main.jsx                # React 入口：ReactDOM.createRoot + StrictMode
│       ├── App.jsx                 # 根组件：BrowserRouter + 路由定义 + 全局布局
│       ├── index.css               # Tailwind CSS 入口 + 暗/亮 CSS 变量 + 自定义字体
│       │
│       ├── api/
│       │   └── index.js            # API 层：封装所有后端接口调用
│       │
│       ├── store/
│       │   └── useStore.js         # Zustand 全局状态（含 persist 中间件）：项目路径、文件树、段落、加载状态
│       │
│       ├── hooks/
│       │   ├── useTheme.js         # 暗/亮主题切换 hook（localStorage + 系统偏好 + View Transitions API）
│       │   ├── useFavicon.js       # 动态 favicon 切换 hook（MutationObserver 监听 dark class）
│       │   ├── useEnabledFeatures.js # 功能开关加载 hook（从后端 /api/config/status 加载，幂等）
│       │   ├── useParagraphCheck.js # 逐段 LLM 检查公共逻辑 hook（供 TypoCheck/ExpressionOpt/Formatting 页面共享）
│       │   ├── useUndoRedo.js      # 撤销/重做状态管理 hook（轮询 can-undo/can-redo 端点 + 键盘快捷键支持）
│       │   ├── useResultFilter.js  # 段落检查结果搜索/过滤/跳转 hook（文本搜索 + 类型过滤 + 段落跳转）
│       │   ├── useWritingChat.js   # 写作助手 Chat hook：会话/文件/段落、SSE 流式发送、深度思考解析、生成编辑、应用、删除/撤回、会话重命名、助手管理（内部使用 writing-chat/ 子 hooks）
│       │
│       ├── lib/
│       │   └── utils.js            # cn() 工具函数（clsx + tailwind-merge）
│       │
│       
│       ├── components/
│       │   ├── DiffViewer.jsx       # 修改对比展示组件（核心 UI）
│       │   ├── MarkerText.jsx       # Marker 渲染组件：将 __CMD_NNNN__ 等标记渲染为彩色标签
│       │   ├── ParagraphContext.jsx  # 段落上下文组件：展开显示当前段落前后上下文
│       │   ├── ProjectExplorer.jsx  # 递归文件树组件
│       │   ├── FolderPickerDialog.jsx # 文件夹选择对话框
│       │   ├── PDFPreview.jsx       # PDF 预览（iframe）
│       │   ├── PromptEditor.jsx     # 自定义提示词编辑器
│       │   ├── SettingsDialog.jsx   # 全局设置对话框（API 密钥、模型、功能开关）
│       │   ├── ThemeToggle.jsx      # 暗/亮模式切换按钮
│       │   ├── ConfirmDialog.jsx    # 通用确认对话框（基于 Dialog，支持 destructive 样式 + 禁止遮罩关闭）
│       │   ├── ImageOCRPage.jsx     # 通用图片 OCR 页面组件（拖拽/粘贴/识别/复制，FormulaOCR 与 TableOCR 共享）
│       │   ├── ResultFilterBar.jsx  # 结果过滤工具栏（搜索框 + 类型下拉 + 段落跳转 + 重置）
│       │   ├── ProgressBar.jsx      # 检查进度条组件
│       │   ├── MarkdownRenderer.jsx # Markdown 渲染组件（react-markdown + remark-gfm + remark-math + rehype-katex）
│       │   └── ui/                  # shadcn/ui 封装组件（基于 @base-ui/react）
│       │       ├── button.jsx       # 按钮（多种 variant 和 size）
│       │       ├── card.jsx         # 卡片（Header/Title/Description/Content/Footer/Action）
│       │       ├── badge.jsx        # 徽章（多种 variant）
│       │       ├── dialog.jsx       # 模态对话框
│       │       ├── input.jsx        # 文本输入框
│       │       ├── label.jsx        # 标签
│       │       ├── scroll-area.jsx  # 可滚动区域
│       │       ├── select.jsx       # 下拉选择
│       │       ├── separator.jsx    # 分割线
│       │       ├── sonner.jsx       # Toast 通知容器（适配项目 useTheme，richColors + closeButton）
│       │       ├── switch.jsx       # 开关组件
│       │       ├── tabs.jsx         # 标签页
│       │       ├── textarea.jsx     # 多行文本域
│       │       └── tooltip.jsx      # 悬停提示
│       │
│       └── pages/
│           ├── WelcomePage.jsx      # 首页：功能卡片导航
│           ├── WorkspacePage.jsx    # 项目工作台：打开目录、文件树、主文件选择、段落信息、自动编译
│           ├── TypoCheckPage.jsx    # 拼写检查页：预览段落、LLM 检查、diff 审核（使用 useParagraphCheck + useResultFilter）
│           ├── ExpressionOptPage.jsx # 表达优化页：预览段落、LLM 优化、diff 审核、提示词管理（使用 useParagraphCheck + useResultFilter）
│           ├── TerminologyCheckPage.jsx # 术语一致性检查页：预览段落、LLM 语义聚类、自定义替换（使用 useParagraphCheck + useResultFilter）
│           ├── FormulaOCRPage.jsx   # 公式识别页：通过 ImageOCRPage 组件配置图标/API/结果元信息
│           ├── TableOCRPage.jsx     # 表格识别页：通过 ImageOCRPage 组件配置图标/API/结果元信息
│           ├── TemplateGenPage.jsx  # 模板生成页：自然语言描述 → LaTeX 模板
│           ├── FormattingPage.jsx   # 源码格式化页：预览源码、LLM 格式化、diff 审核（使用 useParagraphCheck + useResultFilter）
│           ├── StyleModifyPage.jsx  # 样式修改页：粘贴 LaTeX + 自然语言描述 → LLM 修改
│           ├── MarkdownConvertPage.jsx # Markdown 转 LaTeX 页：粘贴 Markdown → pandoc 转换
│           ├── GitDiffPage.jsx      # Git 差异高亮页：选择 Git 版本 → 生成红删绿增差异 PDF
│           ├── BibManagerPage.jsx   # 参考文献管理页
│           └── WritingChatPage.jsx  # 写作助手 Chat 页：三列布局（会话/上下文锚点/聊天），含 Markdown 渲染、深度思考折叠框、编辑建议 diff 卡片（支持手动微调）、助手提示词管理
│
├── static/                          # Vite 构建产物输出到此（生产模式）
│
├── prompts/                         # 用户自定义提示词存放目录
│   ├── expression/
│   │   ├── _active.txt              # 当前激活的提示词名称
│   │   └── *.txt                    # 提示词文件
│   └── writing_chat/
│       ├── _active.txt              # 当前激活的助手提示词名称
│       └── *.txt                    # 助手提示词文件
│
├── formats/                         # 参考文献格式定义存放目录
│
├── templates/                       # 生成的 LaTeX 模板存放目录
│
├── writing_chat_sessions/           # 写作助手聊天数据存放目录
│
├── docs/                            # 项目文档
│   ├── development.md               # 本文件：项目概述、技术栈、目录结构
│   ├── backend-core.md              # 后端核心模块文档
│   ├── modules.md                   # 所有功能模块文档索引
│   ├── frontend.md                  # 前端开发文档
│   ├── testing.md                   # 测试规范与用例覆盖
│   ├── multi-user-migration-plan.md # 多用户服务化改造计划
│   └── modules/                     # 各模块详细文档
│
├── logs/
│   └── latexcellence.log            # 应用日志（按天旋转，保留 30 天）
│
└── tests/
    ├── __init__.py
    ├── conftest.py                  # pytest fixtures（共享 mock、临时目录等）
    ├── test_latex_parser.py         # latex_parser 单元测试
    ├── test_project_manager.py      # project_manager 单元测试
    ├── test_json_utils.py           # json_utils 单元测试
    ├── test_llm_client.py           # llm_client 单元测试
    ├── test_formatting.tex          # 测试用 LaTeX 数据文件
    └── test_api/
        ├── __init__.py
        ├── test_typo_check.py       # typo_check 集成测试
        ├── test_expression_opt.py   # expression_opt 集成测试
        ├── test_terminology_check.py # terminology_check 集成测试
        ├── test_formatting.py       # formatting 集成测试
        ├── test_template_gen.py     # template_gen 集成测试
        ├── test_formula_ocr.py      # formula_ocr 集成测试
        ├── test_table_ocr.py        # table_ocr 集成测试
        ├── test_compile.py          # compile 集成测试
        ├── test_git_diff.py         # git_diff 单元测试
        ├── test_markdown_converter.py # markdown_converter 集成测试
        ├── test_style_modify.py     # style_modify 集成测试
        ├── test_bib_manager.py      # bib_manager 集成测试
        └── test_writing_chat.py     # writing_chat 集成测试
```

---

## 启动入口

### `main.py` — 统一入口

负责：

1. 调用 `load_config(config_path)` 从命令行参数指定的配置文件加载配置
2. 通过 `setup_logging()` 初始化日志系统
3. 创建 FastAPI 应用，注册所有路由模块
4. 配置 CORS（允许所有来源）
5. 在 `lifespan` 中执行启动/关闭日志
6. **生产模式**（`dev_mode: false`）：将 `static/` 挂载为静态文件目录
7. **开发模式**（`dev_mode: true`）：不挂载静态文件，前端由 Vite dev server 独立运行

```python
# 内联路由（直接定义在 app 上）
GET  /api/health           # 健康检查：返回状态、版本、模型名
GET  /api/config/status    # 配置状态：LLM 配置概览 + 功能开关状态
GET  /api/config           # 获取完整配置（api_key 掩码处理）
POST /api/config           # 部分更新配置（持久化到 config.json）

# 注册的路由模块
app.include_router(project_router)        # /api/project/*
app.include_router(typo_check_router)     # /api/typo-check/*
app.include_router(expression_opt_router) # /api/expression-opt/*
app.include_router(terminology_check_router) # /api/terminology-check/*
app.include_router(template_gen_router)   # /api/template-gen/*
app.include_router(formula_ocr_router)    # /api/formula-ocr/*
app.include_router(formatting_router)     # /api/formatting/*
app.include_router(compile_router)              # /api/compile/*
app.include_router(git_diff_router)              # /api/git-diff/*
app.include_router(markdown_converter_router)     # /api/markdown-converter/*
app.include_router(style_modify_router)           # /api/style-modify/*
app.include_router(table_ocr_router)              # /api/table-ocr/*
app.include_router(writing_chat_router)           # /api/writing-chat/*
app.include_router(bib_manager_router)            # /api/bib-manager/*
```

**关键设计**：`backend/modules/__init__.py` 为空——不使用插件自动发现，采用显式导入注册，简单直接。

---

## config.json 结构

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "dev_mode": true,
    "vite_dev_url": "http://localhost:5173"
  },
  "llm": {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "temperature": 0.3,
    "max_tokens": 4096,
    "request_interval": 1.5,
    "max_concurrency": 5
  },
  "features": {
    "typo_check": {
      "enabled": true,
      "batch_size": 5
    },
    "expression_opt": {
      "enabled": true,
      "batch_size": 3,
      "default_prompt": "...",
      "custom_prompts_dir": "prompts/expression/"
    },
    "formula_ocr": {
      "enabled": true,
      "max_image_size_mb": 10
    },
    "table_ocr": {
      "enabled": true,
      "max_image_size_mb": 10
    },
    "template_gen": {
      "enabled": true,
      "output_dir": "templates/"
    },
    "formatting": {
      "enabled": true,
      "batch_size": 5
    },
    "compile": {
      "enabled": true,
      "latexmk_timeout": 120
    },
    "git_diff": {
      "enabled": true
    },
    "markdown_converter": {
      "enabled": true
    },
    "style_modify": {
      "enabled": true
    },
    "terminology_check": {
      "enabled": true,
      "batch_size": 5
    },
    "writing_chat": {
      "enabled": true,
      "compression_threshold_tokens": 200000,
      "sessions_dir": "writing_chat_sessions/",
      "custom_prompts_dir": "prompts/writing_chat/",
      "summary_temperature": 0.2,
      "summary_max_tokens": 1024
    },
    "bib_manager": {
      "enabled": true,
      "format_configs_dir": "formats/"
    }
  },
  "logging": {
    "level": "DEBUG",
    "dir": "logs/"
  }
}
```

- 所有字段有 Pydantic 默认值，可部分配置
- `LX_LLM_KEY` 环境变量优先于 `api_key`
- 前端提交的 `api_key` 以 `***` 开头时保留原值

---

## 前端开发/生产模式区分

| 模式 | 配置 | 行为 |
|---|---|---|
| 开发模式 | `dev_mode: true` | FastAPI 不挂载静态文件；`vite.config.js` 代理 `/api` → `localhost:8000` |
| 生产模式 | `dev_mode: false` | FastAPI 挂载 `static/` 目录；前端无独立 dev server |

Vite 构建命令：
```bash
cd frontend
npm run dev      # 开发模式，Vite dev server 在 :5173
npm run build    # 生产构建到 ../static/
```

---

## 用户审核工作流（以错字查找为例）

```
1. 用户在 WorkspacePage 打开 LaTeX 项目目录 → 文件树显示
2. 用户选择一个 .tex 文件 → 进入 TypoCheckPage
3. 点击「Preview」→ 后端 preview_paragraphs()
    → latex_parser.strip_to_plain_text() 剥离 LaTeX
    → 前端展示纯文本段落（含 marker，前端不可见）
4. 用户确认后点击「Check Typos」→ 后端 check_typos()
    → LLM 逐段检查（marker 保持原样）
    → retry_with_validation() 校验 JSON 输出
    → marker 完整性验证
    → 前端展示 diff（原始 vs 修正）
5. 用户逐项审核，点击「Apply Fix」
    → 后端 apply_changes()
    → restore_from_markers() 还原 LaTeX
    → project_manager.apply_change() 原地写入（MD5 校验 + 备份）
```

---

## 架构核心设计模式

### 分层架构

```
router.py  (薄层：参数校验、HTTP 异常转换、SSE 封装)
    ↓
service.py (厚层：业务编排、LLM 调用、marker 还原)
    ↓
core/      (基础设施：配置、LLM 客户端、项目管理、日志)
utils/     (工具：文件 IO、JSON 校验)
```

**路由层禁止写复杂业务逻辑**。

### 单例模式

所有服务/客户端通过模块级工厂函数实现单例：

```python
_instance: Optional[Type] = None
def get_instance() -> Type:
    global _instance
    if _instance is None:
        _instance = Type()
    return _instance
```

应用位置：
- `config.py`: `get_config()`
- `llm_client.py`: `get_llm_client()`
- `workspace_manager.py`: `get_workspace_manager()`, `get_project_manager_for_workspace()`
- `file_ops.py`: `get_backup_manager()`
- `typo_check/router.py`: `get_service()`
- `expression_opt/router.py`: `get_service()`
- `expression_opt/prompt_manager.py`: `get_prompt_manager()`
- `terminology_check/router.py`: `get_service()`
- `formatting/router.py`: `get_service()`
- `table_ocr/router.py`: `get_service()`
- `bib_manager/router.py`: `get_service()`

### 配置热重载

所有方法内**实时调用 `get_config()`**，不在 `__init__` 中缓存配置对象。

```python
# ✅ 正确
def my_method(self):
    cfg = get_config().llm
    ...

# ❌ 错误——不支持热重载
class MyClass:
    def __init__(self):
        self.cfg = get_config().llm
```

### 错误处理

三层策略：

1. **前置条件检查**（快速失败）：`if not path.exists(): raise FileNotFoundError(...)`
2. **try/except + 指数退避重试**：`for attempt in range(max_retries): ... await asyncio.sleep(2**attempt)`
3. **日志后重抛**：所有异常在重新抛出前记录（`logger.warning` 或 `logger.error`）

禁止自定义异常类，复用标准异常（`ValueError`, `RuntimeError`, `FileNotFoundError`, `IndexError`）。

---

## 编码规范（关键要点）

### Python

| 规范项 | 要求 |
|---|---|
| 导入顺序 | 标准库 → 第三方 → 应用内部（**禁止相对导入**） |
| 类型注解 | 强制注解。使用 `list[X]` / `dict[K, V]` / `X | None` |
| 联合类型 | 使用 `Optional[X]`，**禁止** `X \| None` |
| 字符串引号 | docstring/f-string → 双引号；正则 → `r"..."` |
| 路径操作 | 使用 `pathlib.Path`，**禁止** `os.path` |
| 文件编码 | 显式指定 `encoding="utf-8"` |
| 命名 | 类 PascalCase、函数/变量 snake_case、常量 UPPER_SNAKE |
| 缩进 | 4 空格，禁止 Tab |
| Pydantic | 仅使用 v2 API（`model_validate()`、`model_dump()`） |
| 调试 | 使用 `logger.debug()`，**禁止** `print()` |
| `__init__` | 保持空（仅作为包标记，不在其中写逻辑） |

### JavaScript/JSX

| 规范项 | 要求 |
|---|---|
| 导入 | ESM `import`，相对路径带 `./` |
| 字符串 | 双引号 |
| 分号 | 无 |
| 缩进 | 2 空格 |
| 状态管理 | Zustand 箭头函数 setter |
| API 调用 | 通过 `api/index.js` 的 `request()` |

---

## 日志

- 使用 Python 标准 `logging` + `TimedRotatingFileHandler`
- 日志文件存放在 `logs/`，按**每天午夜**旋转，保留 **30 天**
- 核心调用点必须记录：LLM 请求/响应（含 token 消耗）、文件读写、API 入口/出口、异常
- 每个类在 `__init__` 中创建实例 logger：`self._logger = get_logger("module_name")`

---

## 安全与边界处理

| 场景 | 处理策略 |
|---|---|
| `.tex` 文件包含 `\include`/`\input` | `project_manager` 递归解析，记录文件依赖图 |
| `verbatim`/`lstlisting`/`table` | 整体替换为 marker，LLM 不可见，不可修改 |
| 中文 + LaTeX 混合（UTF-8） | 统一按 UTF-8 读写 |
| LLM 调用 rate limit | 指数退避重试（最多 3 次），请求间隔由 `request_interval` 控制 |
| LLM 修改/删除 marker | 后处理验证 marker 完整性，缺失时回退到原始文本 |
| LLM JSON 输出不符 schema | `retry_with_validation()` 反馈错误给 LLM 重试（最多 3 次） |
| 用户在审核期间手动修改了文件 | 执行原地修改前检查文件 MD5 是否与解析时一致，不一致则拒绝写入 |
| 撤销/重做 | `BackupManager` 维护操作撤销栈（最多 50 层），而非简单 `.bak` 备份 |
| `config.json` 含 API 密钥 | 加入 `.gitignore`；提供 `config.example.json` 作为脱敏模板；环境变量 `LX_LLM_KEY` 优先 |

---

## Phase 1 — UX 打磨

Phase 1 在不改变后端核心业务逻辑的前提下，对前端交互体验进行系统性打磨。所有改动遵循 shadcn/ui + Zustand + 自定义 hook 的现有架构。

### Toast 通知系统

引入 [`sonner`](https://sonner.emilkowal.ski/) 作为全局 toast 库。

| 文件 | 职责 |
|---|---|
| `components/ui/sonner.jsx` | `<Toaster>` 包装：读取 `useTheme` 同步暗/亮模式，配置 `richColors + closeButton` |
| `App.jsx` | 全局挂载 `<Toaster />` |
| `hooks/useParagraphCheck.js` | 在 apply 成功/失败、check 完成、SSE 错误时触发 toast |
| `pages/WorkspacePage.jsx` | 项目打开、编译成功/失败、关闭项目时触发 toast |
| `pages/ExpressionOptPage.jsx` | 提示词保存/删除/切换时触发 toast |
| `pages/FormulaOCRPage` 等 | OCR 识别完成/失败、复制成功时触发 toast |
| `components/SettingsDialog.jsx` | 用 `toast.error()` 替换原 `alert()` |

设计原则：操作成功/失败 → toast；进度/状态信息（如"已加载 N 个段落"）保留内联 `message`；移除段落检查页和 OCR 页的冗余内联 `{error && ...}` 文本。

### 撤销/重做 UI

| 文件 | 职责 |
|---|---|
| `hooks/useUndoRedo.js` | 轮询 `/api/project/can-undo`、`/can-redo` 端点维护 `canUndo`/`canRedo` 状态；暴露 `handleUndo`/`handleRedo`，操作后自动刷新状态并回调通知调用方重新加载段落 |
| `api/index.js` | 新增 `api.project.canUndo(filePath, workspaceId)` / `canRedo(filePath, workspaceId)`（GET 请求） |
| `pages/WorkspacePage.jsx` | 顶栏添加 Undo/Redo 图标按钮（带 Tooltip），根据状态动态启用/禁用；键盘快捷键 `Ctrl+Z` 撤销、`Ctrl+Shift+Z` / `Ctrl+Y` 重做；输入框/文本域中不拦截，保留原生 undo |

### "全部应用"按钮

在 `useParagraphCheck` hook 中新增 `handleApplyAll`：

- 收集所有 `changes.length > 0` 的结果，组装 `changes` 数组
- 单次 API 调用批量写回（后端 apply 端点已支持 `list[ApplyChangeItem]`）
- 用 `toast.loading` → `toast.success` 展示进度
- 应用后从 `results` 中移除已应用段落（保持与单段应用一致的 UX）
- 新增 `correctedTextField` 配置项（TypoCheck/Formatting 用 `"corrected"`，ExpressionOpt 用 `"optimized"`）

三个页面（TypoCheck/ExpressionOpt/Formatting）在结果列表顶部渲染"全部应用"按钮，点击后经 ConfirmDialog 二次确认。

### 确认对话框

| 文件 | 职责 |
|---|---|
| `components/ConfirmDialog.jsx` | 通用确认对话框：基于 shadcn/ui `Dialog`（`disablePointerDismissal` 禁止遮罩关闭），支持 `variant="destructive"` 红色确认按钮、`confirmLoading` 加载态 |

覆盖场景：

| 场景 | 页面 | variant |
| --- | --- | --- |
| 关闭项目 | WorkspacePage | destructive |
| 撤销操作 | WorkspacePage | default |
| 删除提示词 | ExpressionOptPage | destructive |
| 全部应用 | TypoCheck/ExpressionOpt/Formatting | default |

### 结果搜索/过滤

| 文件 | 职责 |
| --- | --- |
| `hooks/useResultFilter.js` | 提供 `searchQuery`（文本搜索）、`typeFilter`（按 change.type 过滤）、`jumpToParagraph`（scrollIntoView 跳转）、`availableTypes`（从 results 自动提取去重）、`reset`、`hasFilters` |
| `components/ResultFilterBar.jsx` | 搜索框 + 类型下拉 + 段落跳转输入 + 重置按钮，与 `useResultFilter` 配套使用 |

三个段落检查页面在结果列表顶部渲染 `ResultFilterBar`，列表改为遍历 `filteredResults`；每个结果 Card 添加 `id={`${idPrefix}-P${idx}`}` 供跳转定位。FormattingPage 传入 `CHANGE_TYPE_LABELS` 作为 `typeLabels` 显示中文类型名。

### 编译后内联 PDF 预览

| 层 | 文件 | 改动 |
|---|---|---|
| 后端 | `modules/compile/router.py` | 新增 `GET /api/compile/pdf?main_file=...` 端点，返回 `FileResponse`（latexmk 产出的 `{stem}.pdf`） |
| 前端 | `api/index.js` | 新增 `api.compile.pdfUrl(mainFile)` 返回 URL 字符串 |
| 前端 | `pages/WorkspacePage.jsx` | 编译成功后 `setPdfUrl(compile.pdfUrl + "&t=timestamp")`（cache-busting）；在编译结果 Card 下方渲染 `PDFPreview` 组件（70vh iframe）；关闭项目时清空 `pdfUrl` |

### 公式/表格 OCR 组件抽取

| 文件 | 职责 |
|---|---|
| `components/ImageOCRPage.jsx` | 通用图片 OCR 页面：拖拽上传、Ctrl+V 粘贴、FormData 识别、结果展示、复制。通过 props 配置差异化部分 |
| `pages/FormulaOCRPage.jsx` | 精简为少量代码：传入 `title`/`icon`/`recognize`/`recognizeButtonText`/`renderResultMeta` |
| `pages/TableOCRPage.jsx` | 同上 |

抽取后两个页面大幅缩减，消除了全部重复逻辑。`renderResultMeta` prop 允许各自定制结果头部元信息（公式显示行内/行间 + 置信度，表格仅显示置信度）。
