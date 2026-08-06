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
- `write_file` 对 `.py` 内容做 `ast.parse` 语法校验（`_check_python_syntax()`）：语法错误在写入阶段直接拒绝并回传行/列号，不落盘、不发子进程，避免白跑一轮 `python_sandbox`
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
- **质量信号提取**：`_extract_tikz_issues()` 从日志中提取 error/warning（`! LaTeX Error`、`Undefined control sequence`、`Missing character`、`LaTeX Warning` 等）。即使 latexmk 退出码为 0，日志含真实错误时 `success` 降级为 `False`；`Missing character` 等渲染警告会以 `TIKZ ISSUES DETECTED` 段落回传 LLM 并在事件中携带 `warnings`/`issues` 字段，让 Agent 察觉图中存在未渲染文本。错误模式覆盖 `! Dimension too large`、`! Extra \endcsname`、`! Missing control sequence`、`! Text line contains an invalid character` 等常见 PGFPlots 错误；`Missing character` 中提及 `nullfont` 的条目（来自 latexmk 版本字符串）被视为无害噪音过滤，避免 Agent 因误报做无效修复
- **编译前预检**：`_precheck_tikz()` 在启动 latexmk 前对 TikZ 源做纯文本扫描（不调用 LLM），针对 matplot2tikz 0.5.4 的高频故障：
  - 内联 `y error=`（非法 key，合法形式是 `y error plus`/`y error minus`）与 `+-` 误差增量含 `nan`/`inf` 时判定为**必然失败**，跳过编译直接回传修正建议；
  - 线性轴上 |坐标| > 1e4（如 Excel 日期序号 44927）判定为**必然失败**（`Dimension too large`），对数轴（`xmode=log`/`ymode=log`）上的大数值坐标放行；
  - 非 ASCII 字符仅给**警告**不拦截（xelatex 可正常渲染中文，避免误伤合法流程）。
  - 预检结果以 `TIKZ PRECHECK` 段落附加到 `compile_tikz` 回传文本，事件新增 `precheck`（命中规则列表）与 `precheck_blocked`（是否跳过编译）字段

## Agent 循环保护

`agent.py` 主循环内置"连续相同失败"检测：同一工具调用产生完全一致的失败结果达到 3 次时，向 LLM 注入系统提示要求停止盲目重试（改为换思路诊断或带警告调用 `finish`），并向前端发出 warning 事件。

除逐字相同的失败检测外，还按工具族（`python_sandbox` / `compile_tikz`）维护**失败预算**：同一工具族连续失败 3 次即注入强约束提示——`python_sandbox` 连续失败时要求简化图表（去掉误差棒/3D/花哨标注）并核对列名；`compile_tikz` 连续失败时要求先修正 `tikz_spec.json`/数据 CSV（后端自动重新渲染 figure.tex），必要时改用手写 PGFPlots。该机制可拦截"错误信息文字不同但根因相同"的隐性死循环。

System prompt 还包含以下约束，防止 Agent 在成功路径上产出低质量结果：

- **统计真实性**：只有用户明确要求时才运行统计检验，且必须调用 `stats_test` 工具并原样引用其返回值；禁止在脚本内联计算统计量作为替代，禁止在 summary 中编造/改写任何 p 值或统计量（图中标注与 summary 数值必须一致）

`stats_test` 参数校验（`executor.py::_validate_stats_inputs`）：按测试类型强制必填参数并在缺失/类型不符时给出明确消息——t-test/wilcoxon/anova/kruskal 要求 `group` + 数值列 `y`；pearson/spearman 要求数值列 `x` + `y`；chi2 要求分类列 `x` + `y`（高基数数值列会被拒绝）。错误消息包含缺失参数名与数据文件可用列，避免原先 `df.loc[df[group]==g, None]` 抛晦涩 KeyError。

- **风格约束**：禁止覆盖预置 rcParams，见"风格注入"一节
- **图内文字语言**：图内文字（标题/坐标轴/图例/标注）必须使用纯英文 ASCII；单位采用 ASCII 转写（`ug/mL`、`uM`、`degC`、`x`），禁止 μ/°/± 等 Unicode 符号，避免 TikZ 编译失败或渲染为 U+FFFD
- **日期/坐标指导**：datetime 列先转成日序号（day index）再绘图或写 TikZ，避免大绝对值坐标（如 44927）触发 PGFPlots "Dimension too large"；对数坐标需求显式使用 `xmode=log` 并过滤非正剂量
- **无验证循环**：`compile_tikz` 成功后不得重复 `read_file`/`python_sandbox`/`compile_tikz` 做无意义验证，直接调用 `finish`
- **省轮次**：数据画像已含列类型/样例/分位数/预览，优先依据画像写代码，避免 `read_file` 整份数据；修复刚写入的脚本时直接 `write_file` 覆盖，无需回读

## TikZ 生成与回退协议

提示词内置受控生成 + 手写回退两层策略：

1. **首选受控确定性生成（`tikzgen.py`）**：脚本只负责计算数据，把每个系列/分组写入 `artifacts/data_*.csv`（ASCII 列名、`to_csv(index=False)`），并写一份声明式 `artifacts/tikz_spec.json`（chart_type / 轴标签 / xmode / series 列映射 / box/bar/heatmap 子对象）。`python_sandbox` 成功后由后端 `generate_figure_tex()` 用 Pydantic v2 校验 spec 并确定性渲染 `artifacts/figure.tex`，不调用 LLM、不依赖 matplot2tikz；spec 校验失败或引用 CSV 缺失时，错误消息直接回传 Agent 修正。
2. **手写 PGFPlots 回退**：仅当 spec 生成对同一根因失败 2 次（或需要生成器不支持的图形特性）时，才改为脚本内手写简单 PGFPlots 源文件：
   - 标签保持 ASCII；
   - 数据写入 `artifacts/data_*.csv`，按列名引用（`table[x=..., y=..., y error plus=..., y error minus=...]`），避免超大内联坐标；
   - 使用小/相对 x 值（日序号或 0..n）；
   - 误差棒通过显式 `y error plus/minus` 列实现，否则省略；
   - 文件以裸 `\end{tikzpicture}` 结尾（不含 `\documentclass`）。
   - **必须写 `col sep=comma`**：pandas `to_csv` 输出逗号分隔，而 PGFPlots 默认按空格/制表符解析，漏写会导致"找不到列"错误（这是手写路径最常见的隐性失败根因）。
3. **参考模板注入**：`prompts.py::render_pgfplots_reference(chart_type)` 按 `chart_plan.chart_type` 向 agent system 注入已验证的最小 PGFPlots 模板（箱线图/折线图/柱状图/热力图各一）。模板在 backend 的 standalone 包装下经 latexmk 编译验证；箱线图模板包含 `\usepgfplotslibrary{statistics}`，热力图模板使用 `matrix plot` + `mesh/cols`，柱状图模板使用整数索引列 `x_index` + `xticklabels` 类别展示。

`tikzgen.py` 支持的声明类型：`line` / `scatter` / `bar` / `box` / `heatmap` / `histogram` / `kde`（violin 回退为 boxplot 渲染）；热力图支持 `RdBu_r`/`RdBu` 内建色标与 `colormap_stops` 自定义色标，单元格显著性标注通过 `cell_text` 声明为 `\node` 叠加。`python_sandbox` 事件新增 `tikz` 字段（`generated`/`error`/`warnings`），spec 缺失时回传文本明确提示 Agent 补写 spec 或手写 figure.tex。

输出约定：每次成功运行必须产出 `figure.pdf`、`figure.svg`、`figure.png`（前端预览）和 TikZ 源文件。

## 数据画像与图表推荐

- `data_parser._infer_type` 将中等基数的字符串列判为 `categorical`（基数列数阈值放宽到 `unique <= max(2, min(n*0.5, 30))`），避免 `Treatment` 这类分组列被误判为 `unknown` 而丢失分组信息；`id` 判定要求行数较多（`n >= 10`）且全唯一，避免小样本分类列被误判为 id
- `build_profile_context()` 生成给 LLM 的完整画像上下文（原始摘要 + 列明细含 `top_values`/`quantiles`/`sample` + 前 5 行预览），供图表推荐和 Agent system prompt 共用，帮助 LLM 理解列语义、减少读取原始数据的需要
- `redundancy_hint_pairs()` 识别"每单元格只有一行"的列组合（`unique(x) × unique(cat) ≈ rows`），在画像上下文中输出 `Redundancy hints`，提示 LLM 与确定性规则：此类组合不要做 mean/sd 聚合或加误差棒（单值聚合无意义且会让 TikZ 输出脆弱）
- `recommender` 在用户关注分布形态（分布/偏态/异常值/离群等中英文关键词）且存在分类列 + 数值列时，优先推荐带叠加散点的箱线图，并优先选择描述中提及的列
- LLM 图表推荐的消息中注入同样的冗余提示，且 `deterministic_chart_plan` 命中冗余组合时自动把 `aggregation` 改为 `none`、`error_bar` 改为 `None`

## 数据入口

首版只支持上传文件，扩展名白名单：

- `.csv`
- `.tsv`
- `.xlsx`
- `.xls`
- `.json`

## 风格注入

`styles.py` 提供 frozen dataclass 风格预设。后端在每次 `python_sandbox` 执行前把 `render_setup_code(style_key)` 注入脚本开头，LLM 不需要重复编写 `rcParams`。

System prompt 中的 `STYLE NOTE` 约束 LLM：风格已由后端预置，**禁止**在脚本中调用 `matplotlib.rcParams` / `plt.rcParams` 或 `list_styles` / `apply_style`（除非用户明确要求换风格）；需要微调轴样式时使用注入的 `apply_plot_style(ax)` 辅助函数，避免 LLM 手写样式覆盖预置风格。

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
