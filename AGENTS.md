# AGENTS.md — LateXcellence AI Coding Guidelines

LateXcellence 是一个 AI 驱动的 LaTeX 文档助手。后端 FastAPI + 前端 React/Vite，通过 LLM 对 `.tex` 文件进行错字查找、表达优化等操作。

## 全局规则

- **每次开始新对话**，先读 PROJECT_SUMMARY.md 了解项目结构和功能。
- **项目结构变更**（新增/删除模块、核心文件路径变更），必须同步更新 PROJECT_SUMMARY.md。

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
```

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
- **还原逻辑**：调用 `LatexParser.restore_from_markers()` 时，**必须按 marker 长度从长到短还原**，防止短的误匹配长的
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
- **配置同步规范**：当修改 `config.json` 配置文件时（如新增配置项、删除配置项、修改配置结构），**必须**同步更新前端相关代码：
  1. 更新 `frontend/src/components/SettingsDialog.jsx` 中的设置 UI，确保用户可以通过界面修改新配置项。
  2. 更新 `SettingsDialog.jsx` 的 `handleSave` 函数中 `setEnabledFeatures` 调用，确保新增的功能开关能实时同步到全局状态。
  3. 更新 `frontend/src/pages/WelcomePage.jsx` 中的 `featureGroups`，确保新功能在首页有点线目录条目展示。
  4. 更新后端 `backend/core/config.py` 中的 `AppConfig` 模型，确保 Pydantic 校验与 config.json 结构一致。

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
| 逐段检查后刷新全部预览 | 应用单段修改后**不得**调用刷新全部预览（会清空）；从结果列表过滤掉已应用段落；检查结果需纳入 Zustand persist `partialize` 以支持刷新保留 |
| 逐段检查结果显示顺序 | `changes` 非空优先；无建议在后；同类内部按 `paragraph_index` 升序；排序完成后再做过滤，保持有序 |
| 逐段检查功能实现模式 | 必须用 `useParagraphCheck` hook 封装：恢复/文件选择/预览/检查优化/排序/应用/refresh 持久化；页面只传 API 模块与 store slice 配置；禁止在页面重复实现 |
| 逐段检查前端交互约束 | 1）结果页用 `<MarkerText>` 渲染含 Marker（如 `__MATH_0000__`）的字符串展示友好标签；2）展开“查看原文”用 `<ParagraphContext>`（显示当前段落前后上下文） |

### 前端错误消息翻译规则

前端**必须**将所有后端返回的英文错误消息翻译为当前语言后再展示给用户：

- **HTTP 请求层**：`api/index.js` 的 `request()` 已统一通过 `translateError()` 翻译错误。
- **SSE 流**：`useParagraphCheck` / `useWritingChat` 等 hook 中 SSE 返回的 `data.message` 必须手动调用 `translateError()` 翻译。
- **响应体字段**：API 响应 JSON 中嵌套的错误字段（如 `result.errors[].reason`）**不经过 `request()` 拦截**，必须在消费处手动调用 `translateError()` 翻译。
- **新增后端错误时**：必须同步在 `frontend/src/i18n/locales/zh.json` 和 `en.json` 的 `errors` 命名空间中添加对应翻译条目（精确匹配用扁平 key，模式匹配用描述性 key + `{{变量}}` 占位符）。
- **禁止硬编码中文兜底**：不要在组件中写 `e.reason === "xxx" ? "中文" : e.reason` 这样的条件翻译，统一走 `errorMessages.js` + i18n 维护。
