# AGENTS.md — LaTeXcellence AI Coding Guidelines

LaTeXcellence 是一个 AI 驱动的 LaTeX 文档助手。后端 FastAPI + 前端 React/Vite，通过 LLM 对 `.tex` 文件进行错字查找、表达优化等操作。

## 全局规则

- **每次开始新对话**，先读 PROJECT_SUMMARY.md（精简索引）定位本次任务相关的模块与设计要点，需要细节时再读 `docs/` 对应文档，不要整篇背诵。
- **项目结构变更**（新增/删除模块、核心文件路径变更），必须同步更新 PROJECT_SUMMARY.md。
- **特性专属约束按需加载**：配置同步、逐段检查、错误消息翻译等场景约束已下沉为 `.agents/skills/` 下的项目 skill，碰到对应场景必须先读对应 SKILL.md 再动手。

## 技术栈速查

| 层 | 语言 | 框架/主要库 |
| --- | --- | --- |
| 后端 | Python 3.12+ | FastAPI, uvicorn, openai, pydantic v2 |
| 前端 | JavaScript (JSX) | React 19, Vite 6, Tailwind CSS 4, Zustand, shadcn/ui |
| 测试 | Python | pytest + pytest-asyncio + httpx |

## 常用命令

```bash
# 后端启动
python main.py config.json

# 前端开发/构建（在 frontend/ 目录下）
npm run dev
npm run build

# 运行后端测试
pytest tests/ -v

# 统一验证入口：改完代码跑这一条（ruff + black + 约束扫描 + pytest + 前端单测）
bash scripts/check.sh
```

## 约束机器化（优先靠工具，不靠记忆）

本文件的编码约束已尽量落地为机器检查，**机器检查是准绳，文字只是解释**：

- `ruff.toml`：os.path / print / Pydantic v1 API 之外的可静态化禁令（含豁免清单与理由）
- `scripts/check_conventions.py`：ruff 覆盖不到的约定——Pydantic v1 残留、path_guard 绕过、文档硬编码数量
- `.pre-commit-config.yaml`：black / prettier / ruff / 约束扫描，提交前自动拦截
- `scripts/check.sh`：以上全部 + pytest + 前端单测的统一入口；**完成代码修改后必须运行并通过**

## 缓存清理

- 根目录提供功能一致的 `clean-cache.sh`（Bash）和 `clean-cache.ps1`（PowerShell）。
- 清理缓存时直接运行对应脚本，不需要再向用户询问授权。
- 非交互执行 PowerShell 版本时使用 `.\clean-cache.ps1 -NoPause`，避免等待按键。

---

## Commit Message 规范

格式：`<type>: <中文描述>` 或 `<type>(<scope>): <中文描述>`

- **type**：`feat`（新功能）、`fix`（修 bug）、`refactor`（重构）、`docs`（文档）等
- **scope**（可选）：影响范围，如 `frontend`、`backend`、`config`、`compile`
- **描述**：用中文，简洁明了

示例：

```
feat: 多工作区支持 + 配置清理
fix(compile): 修复 latexmk 超时后子进程泄漏问题
refactor: 消除外部模块对私有属性的直接访问
```

---

## 核心业务约束（最高优先级）

### 1. Marker 保护（防止 AI 改坏 LaTeX）

与 LLM 交互时，LaTeX 结构必须替换为 marker（`__CMD_0000__` / `__MATH_0000__` 等）。

- **Prompt 约束**：必须在 System Prompt 中强约束 LLM：“**绝对禁止修改、删除或拆分任何 marker，只能修改纯文本**”。
- **还原逻辑**：调用 `LatexParser.restore_from_markers()` 时，**必须按创建顺序（counter）从大到小还原**（外层 marker 先还原），防止短的误匹配长的；非标准 marker 回退为按长度从长到短
- **code_review 例外**：code_review 的 LLM 输出（建议）只读展示、从不写回文件，`new_text` 由工具格式化生成；因此该模块直接让 LLM 审查格式化后的真实 LaTeX，不使用 marker 保护

### 2. JSON 输出校验流程

LLM 输出**必须**经过 `json_utils.py` 的 `retry_with_validation()` 处理：

1. `safe_parse_json()` 容错解析（去 markdown 围栏、尾逗号、单引号）
2. Pydantic `.model_validate()` 严格校验
3. 失败时，用 `format_validation_error()` 将错误格式化后反馈给 LLM 重试（最多 3 次）

### 3. LaTeX 内容校验（JSON 转义防护）

LLM 输出的 LaTeX 文本（经 JSON 传输或写盘前）**必须**使用 `backend/utils/latex_json_guard.py` 的共享校验器（`LatexText` / `check_latex_text()` / `validate_latex_field()`）：

- 校验项：控制字符（JSON 反斜杠转义损坏特征）、`[object Object]` 垃圾占位符、花括号 / `$` 配平，可选 `\begin` / `\end` 环境配平
- BibTeX 字段使用 `bibtex=True`（`%` 为字面字符，避免 URL percent-encoding 误判）
- 直接传输 LaTeX 的模块（OCR 类、writing_chat 编辑输出等）优先采用分隔符协议（`<<<LATEX>>>...<<<END>>>`）直接传输纯文本，从根上避免 JSON 转义；非 JSON 路径使用 `latex_json_guard.py` 的 `relaxed=True` 宽松校验（放行 `\t` / `\r` 等合法 LaTeX 空白）

### 4. 多用户模式路径脱敏（不向用户暴露服务器路径）

**多用户模式下，不显示完整项目路径（必要时可使用相对路径）。** 多用户模式下项目存放在服务器用户空间（形如 `<users_root>/<user_id>/projects/<project_id>` 的 SHA256 长路径），该路径对用户无意义且泄露服务器结构：

- **API 响应**：禁止在响应体/SSE 事件中返回 `root_path`、`git_root`、`sandbox`、`project_root` 等绝对路径字段（多用户模式置 `null` 或直接省略；单用户本地模式可保留现状）
- **错误消息**：面向用户的异常消息（HTTPException detail、toast 文案）必须用相对路径，严禁拼接 `{full_path}`；完整路径只允许进服务端日志
- **命令输出**：编译日志、traceback 等天然携带绝对路径的文本，展示给用户前必须经前缀脱敏（复用 `tex_compile.sanitize_output_paths()`，把工作目录前缀改写为 `./`）
- **前端判定**：判断"项目是否打开"必须用 `useIsOpened()`（多用户看 projectId），不得依赖 `rootPath` 真值

---

## Python 后端规范

### 类型和 Pydantic v2（容易出错）

- **类型注解**：强制注解。使用 `list[str]` / `dict[str, Any]`，**`X | None`**。
- **Pydantic v2 专属**：
  - 使用 `model_validate()` / `model_dump()`（**严禁**使用 v1 的 `parse_obj()` / `dict()`）。
  - 配置使用 `model_config = ConfigDict(...)`（**严禁**使用 v1 的 `class Config`）。
  - 字段校验使用 `@field_validator`（**严禁**使用 `@validator`）。

### 异步与并发陷阱

- **事件循环保护**：FastAPI 路由和 Service 必须 `async def`。遇到**同步阻塞 I/O** 或 **CPU 密集型 LaTeX 解析**，**必须**使用 `asyncio.to_thread()` 或 `run_in_executor`，严禁阻塞事件循环。
- **客户端生命周期**：`openai.AsyncOpenAI` 或 `httpx.AsyncClient` 必须在 FastAPI `lifespan` 中初始化并全局共享，**严禁**在每次请求的函数中重新实例化。

### 架构与模块

- **分层**：严格遵循 `router.py`（薄路由/参数校验） + `service.py`（业务逻辑/LLM 编排）。路由层禁止写复杂逻辑。
- **配置热重载**：禁止在 `__init__` 中缓存配置，方法内必须实时调用 `get_config()`。
- **错误处理**：快速失败 → 指数退避重试 → 日志后重抛。复用标准异常（`ValueError`/`RuntimeError`），**禁止自定义异常类**。
- **日志**：使用 `get_logger(__name__)`。LLM 调用、文件读写、API 边界必须记录日志。

### 路径安全（必须遵守）

- **唯一入口**：所有需要根据项目根目录拼接 `file_path` 的代码，**必须**调用 `backend/utils/path_guard.py` 的 `resolve_project_path(root, file_path, ...)` 函数。
- **禁止绕过**：不得直接使用 `root / file_path`、`os.path.join(root, file_path)` 或手动 `resolve()` + `is_relative_to()` 拼接。
- **可选参数**：`allowed_suffixes`（扩展名白名单，如 `(".tex",)`）和 `allow_symlink`（是否允许符号链接，默认 `False`）。
- **符号链接检测**：使用 `os.path.islink()`（非 `Path.is_symlink()`）逐组件遍历检测，兼容路径不存在的情况。
- **禁止行为**：不得在 `__init__` 中缓存路径结果，每次操作必须重新调用 `resolve_project_path()`。

### LaTeX 解析与正则

- 编写 LaTeX 正则时，注意转义特殊字符（如 `%`, `&`, `_`, `#`）。
- **严禁**使用嵌套量词（如 `(a+)+`）或过度宽泛的 `.*`，防止灾难性回溯（Catastrophic Backtracking）。

---

## 前端规范 (React 19 + Zustand)

- **状态管理 (Zustand)**：采用**切片（Slices）模式**，避免单一巨型 store。组件内订阅 state 时，优先使用 `useShallow` 防止不必要的重渲染。
- **组件与样式**：优先使用 shadcn/ui 组件。样式定制通过 Tailwind CSS 4 的 utility classes 或 CSS 变量，**禁止**直接魔改底层 Radix UI 的 DOM 结构或滥用内联 `style`。
- **代码风格**：遵循 Prettier 默认配置。
- **页面文字**：前端页面使用中文。
- **配置同步规范**：修改 `config.json` / `config.example.json` 的结构（增删改配置项、功能开关）时，**必须**先读 `.agents/skills/latexcellence-config-sync/SKILL.md` 并执行其中的同步检查单（设置分区组件、`handleSave`、`WelcomePage.featureGroups`、`AppConfig` 模型四处缺一不可）。

---

## 测试规范

- 使用 **pytest** + `pytest-asyncio`。
- **异步测试**：注意 `loop_scope` 的设置，确保 fixture 和测试函数在同一个事件循环中。
- **Mock 策略**：测试 Service 层时，**必须 Mock LLM 调用**（使用 `pytest-mock` 或 `unittest.mock`），严禁在单元测试中发起真实网络请求。
- **LaTeX 数据**：测试用的 LaTeX 字符串必须使用原始字符串 `r"..."`。

---

## 文档编写

- DEVELOPMENT.md 是开发文档。编码完成新的模块，必须在 DEVELOPMENT.md 文档中添加对模块功能、用途、设计的记录。对已有模块逻辑进行修改，DEVELOPMENT.md 中描述同步更新。
- README.md 是项目简介。当新增重大功能或进行重大修改后，需要同步对 README.md 进行修改。
- DESIGN.md 是设计技术文档。

### 文档中的数量描述规范（重要）

**严禁在文档中硬编码文件/模块/组件的具体数量**（如"13 个功能模块"、"14 个页面"、"7 个 hook"等）。

原因：项目结构会频繁变化，硬编码的数字极易过时，每次增删模块都需要同步修改文档中的数字，维护成本高且容易遗漏。

**正确做法**：

- 使用模糊描述替代具体数字：`多个`、`若干`、`各` 等
- 或直接省略数量描述，只列出类型/目录结构
- 需要精确数量时，应通过脚本动态统计而非手动写死

---

## 绝对禁止事项 (Negative Constraints)

| 禁止行为 | 替代/说明 |
| --- | --- |
| 使用 `os.path` | 用 `pathlib.Path`（文件操作显式 `encoding="utf-8"`） |
| 使用 `print()` 调试 | 用 `logger.debug()` 或 `logger.info()` |
| Prompt 要求 LLM 修改 Marker | 用 Marker 保护策略，Prompt 强调不可见/不可改 |
| 同包内使用绝对导入 | **同包内**用相对导入（如 `from . import service`），**跨包**用绝对导入（如 `from backend.core import get_config`） |
| `__init__` 缓存配置 | 方法内实时 `get_config()` |
| 前端 `prop drilling` | 用 Zustand 或 React Context 传深层状态 |
| Zustand setter 直接存函数值 | `set()` 不支持函数 updater 透传；若需 `setResult(prev=>...)`：setter 检测 `typeof value==="function"` 并转 `set(state=>({key:value(state.key)}))`；否则函数会被当值存，渲染 `.map()`/`.length` 对函数类型会崩溃 |
| 绕过 skill 实现逐段检查 | 逐段检查族（typo/expression/terminology/translate）前后端的实现、展示顺序、保鲜与交互约束，必须先读 `.agents/skills/latexcellence-paragraph-check/SKILL.md` 再动手 |

### 前端错误消息翻译规则

前端**必须**将所有后端返回的英文错误消息翻译为当前语言后再展示，统一走
`errorMessages.js` + `errorPatterns.js` + i18n 三语 `errors` 命名空间维护。

关键点：`request()` 已统一翻译 HTTP 错误；但 **SSE `data.message`** 与**响应体嵌套错误字段**（如 `result.errors[].reason`）必须在消费处手动 `translateError()`；新增后端错误必须同步 `zh.json` / `zh-TW.json` / `en.json` 三语条目（前端 i18nCoverage 单测会拦截漏配）。**禁止**组件内硬编码 `e.reason === "xxx" ? "中文" : e.reason` 式条件翻译。

完整链路与 key 规范见 `.agents/skills/latexcellence-i18n-errors/SKILL.md`。
