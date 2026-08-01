# 科研绘图 Agent 模块 `plot_gen/`

根据用户上传的 CSV/TSV/Excel/JSON 数据，自动生成可复现、可编辑的科研矢量图表。模块采用“数据画像 -> 图表方案 -> 代码生成 -> 沙箱执行 -> 错误回修 -> TikZ 编译 -> 用户微调”的 Agent 流程。

## 设计目标

- 输出 PDF/SVG 矢量图、PNG 预览和 TikZ/PGFPlots 源文件
- 默认使用色盲友好色板和简洁学术风格
- 不自动虚构统计结果；显著性检验必须返回方法、统计量、p 值、假设与校正信息
- 数据文件只在上传阶段进入 session 沙箱，绘图脚本不直接访问工作区

## 模块结构

```
backend/modules/plot_gen/
├── router.py        # 上传、SSE 生成/微调、文件预览和下载
├── service.py       # session 创建、图表推荐和业务编排
├── agent.py         # Function Calling Agent 主循环
├── models.py        # Pydantic v2 数据模型
├── data_parser.py   # 数据读取与画像
├── recommender.py   # 确定性图表规则 + LLM 结构化推荐
├── styles.py        # Nature/Science/Cell/IEEE/ACS 风格预设
├── executor.py      # Python 子进程执行、产物扫描、TikZ 编译和统计检验
├── sandbox_guard.py # python_sandbox 审计钩子防护（安全监听器）
├── trace.py         # Agent 运行追踪（trace.jsonl 记录与读取）
└── cleanup.py       # 过期 session 清理
```

## Session 私有工具

工具由 `PlotGenAgent._build_tools_schema()` 动态构建，不注册全局 `ToolRegistry`：

| 工具 | 职责 |
|---|---|
| `write_file` | 在沙箱内写脚本/文档 |
| `read_file` | 读取沙箱内文件 |
| `list_files` | 列出沙箱文件 |
| `python_sandbox` | 执行绘图脚本并扫描产物 |
| `compile_tikz` | 编译 `artifacts/` 内的 TikZ 源文件 |
| `stats_test` | 执行 scipy 统计检验 |
| `list_styles` | 列出风格预设 |
| `apply_style` | 切换当前风格 |
| `finish` | 结束会话并返回产物清单 |

## 沙箱与执行

- 上传数据写入 `plots/<session_id>/data/`
- Agent 脚本写入 `plots/<session_id>/scripts/`
- 产物写入 `plots/<session_id>/artifacts/`
- Python 子进程使用 `-I -B`（隐含 `-E`/`-s`，忽略 `PYTHON*` 环境变量与 user site），设置 `MPLBACKEND=Agg`
- 每次执行前在 `scripts/_run_plot.py` 头部注入 `sandbox_guard.render_guard_bootstrap()` 的防护引导代码，先安装审计钩子再执行风格注入与用户脚本
- 临时目录（`TMPDIR`/`TEMP`/`TMP`）重定向到 `tmp/`、matplotlib 缓存（`MPLCONFIGDIR`）重定向到 `.cache/matplotlib/`，均在沙箱内部
- 超时、stdout 长度、文件大小和文件数量均有配置上限
- 所有相对路径经 `resolve_project_path()` 校验
- `write_file` 禁止覆写 `data/` 目录及 `meta.json`/`trace.jsonl` 控制文件
- 超时后按进程组终止子进程及其子孙（Windows 使用 `taskkill /T`），避免孤儿进程泄漏

## python_sandbox 安全监听器（审计钩子防护）

`executor.py::_run_script` 在运行用户脚本前，通过 `sandbox_guard.py` 生成引导代码注入沙箱子进程，用 `sys.addaudithook()` 安装“监听器”，对危险操作直接抛出 `PermissionError` 终止执行：

| 危险操作 | 拦截方式 |
|---|---|
| 读取沙箱外文件 | `open` / `os.open` 读模式、`os.listdir` / `os.scandir` / `os.readlink` 均要求路径在白名单根内（沙箱、Python 安装目录、系统字体目录） |
| 执行系统命令、开子进程 | `subprocess.Popen`、`os.system`、`os.exec*`、`os.spawn*`、`os.fork*`、`posix.exec*` 一律拦截 |
| 访问网络 | `socket.*`、`urllib.Request`、`http.client`、`ftplib` 等一律拦截 |
| 删除/修改系统文件 | `os.remove`/`unlink`/`rename`/`rmdir`/`mkdir`/`chmod`/`truncate`/`utime`/`symlink` 等仅允许沙箱内路径 |
| ctypes 底层调用 | 拦截进程句柄加载（`CDLL(None)`/`pythonapi`）、按路径加载库、解析危险符号（`system`/`socket`/`open`/`WinExec`/`CreateProcess` 等黑名单）；系统库裸名加载（numpy/scipy/matplotlib 正常需要）放行 |
| 其它 | `os.chdir`、`os.kill`/`os.killpg`、`os.setuid` 等权限/特权操作一律拦截 |

同时把临时目录与 matplotlib 缓存重定向进沙箱，避免正常绘图流程被误拦；`sys.removeaudithook` 在引导代码中被置空，防止沙箱代码卸载钩子。

## 安全边界（重要）

**`python_sandbox` 执行的是宿主机上以当前用户权限运行的任意 Python 代码**，`-I` 只做环境隔离。上述审计钩子属于 **Python 层纵深防御**，不提供操作系统级沙箱（无容器、无文件系统/网络强制隔离、不限制 CPU/内存）。

- 绘图代码由 LLM 生成，而上传的数据文件会进入 LLM 上下文——恶意数据存在 **prompt 注入** 风险，可能诱导 LLM 写出读取敏感文件、外传数据等代码并以当前用户权限执行
- 缓解措施：路径经 `resolve_project_path()` 校验、`write_file` 无法覆写 `data/` 与控制文件、审计钩子拦截越界读写/子进程/网络/ctypes 危险用法、子进程超时/大小限制兜底
- 已知局限：审计钩子只覆盖 CPython 已接入审计机制的调用（如 `os.stat`/`os.lstat` 不触发审计事件，无法拦截纯元数据探测）；ctypes 允许加载系统库，通过系统库函数绕过钩子仍属理论可行；第三方库中未经过审计的系统调用无法阻止
- 建议：将 `features.plot_gen.python_interpreter` 指向权限受限的专用解释器/虚拟环境；如需更强隔离应在系统层用容器或专用账号运行该服务
- 使用本模块即视为接受上述信任边界

## TikZ 编译

`executor.py::_compile_tikz` 通过 `run_tex_compile()` 调用 latexmk 校验 `artifacts/` 内的 TikZ 源文件：

- 统一在 `artifacts/` 目录下以 `tikz_main.tex` 为主文件编译，产物输出为 `artifacts/tikz_main.pdf`，避免与 matplotlib 生成的 `figure.pdf` 冲突
- 自动识别目标 `.tex` 类型：含 `\documentclass` 的完整 standalone 文档直接作为主文件；纯 `tikzpicture` 片段则包装进 `\documentclass{standalone}` 主文件并 `\input` 引用
- 编译引擎（pdflatex/xelatex/lualatex）基于目标图内容检测（而非包装文件），避免中文标签被误判为 pdflatex
- 目标文件不在 `artifacts/` 下时先复制进去，保证 `\input` 相对编译目录可解析

## Agent 循环保护

`agent.py` 主循环内置"连续相同失败"检测：同一工具调用产生完全一致的失败结果达到 3 次时，向 LLM 注入系统提示要求停止盲目重试（改为换思路诊断或带警告调用 `finish`），并向前端发出 warning 事件。

## 数据入口

首版只支持上传文件，扩展名白名单：

- `.csv`
- `.tsv`
- `.xlsx`
- `.xls`
- `.json`

## 风格注入

`styles.py` 提供 frozen dataclass 风格预设。后端在每次 `python_sandbox` 执行前把 `render_setup_code(style_key)` 注入脚本开头，LLM 不需要重复编写 `rcParams`。

## 运行追踪（分析/改进用）

`agent.py` 在每次运行（生成/微调）时把 Agent loop 的完整交互按时间顺序逐条追加到 session 沙箱的 `trace.jsonl`，用于分析、改进 prompt 与复现调试。

- 由 `trace.py` 提供追加与读取：`append_trace()`（沙箱级锁 + 线程内写文件，防并发交错）、`read_trace()`
- `trace.jsonl` 随沙箱一起被 `cleanup.py` 清理，并包含在下载的 ZIP 中
- 事件类型：
  - `run_start`：运行类型、system prompt、初始 messages、最大轮数
  - `llm`：LLM 回复全文与工具调用参数（未做摘要）
  - `tool_result`：工具名、完整参数、回传 LLM 的 result 文本与前端事件
  - `warning` / `error`：运行中的警告与错误
  - `run_end`：总结、实际轮数、最近执行/TikZ 编译是否成功
- 每次运行的 `run_id` 与 `ts` 用于区分多次生成/微调

## API

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/plot-gen/upload` | multipart 上传数据并创建 session |
| POST | `/api/plot-gen/agent/generate` | SSE 启动 Agent 生成 |
| POST | `/api/plot-gen/sessions/{session_id}/refine` | SSE 自然语言微调 |
| GET | `/api/plot-gen/sessions/{session_id}/files` | 文件列表 |
| GET | `/api/plot-gen/sessions/{session_id}/file` | 读取单个文件 |
| GET | `/api/plot-gen/sessions/{session_id}/preview` | 预览图片/PDF |
| GET | `/api/plot-gen/sessions/{session_id}/trace` | 读取运行追踪日志（JSON） |
| GET | `/api/plot-gen/sessions/{session_id}/trace/download` | 导出运行追踪日志（JSON 附件） |
| GET | `/api/plot-gen/sessions/{session_id}/download` | 下载 ZIP |
| GET | `/api/plot-gen/styles` | 风格列表 |

## 配置

配置位于 `features.plot_gen`，包含功能开关、沙箱执行超时、最大数据大小、TikZ 编译开关、默认风格和清理策略。

## 前端

- 路由：`/plot-gen`
- 页面：`frontend/src/pages/PlotGenPage.jsx`
- Hook：`frontend/src/hooks/usePlotGen.js`
- API：`frontend/src/api/index.js` 的 `plotGen` 命名空间
- 支持数据上传、Agent 时间线、数据画像、图表方案、产物预览、文件查看、ZIP 下载和自然语言微调
- 支持查看与导出 Agent 运行追踪日志（`pages/plotGen` 的 `trace*` 文案）
