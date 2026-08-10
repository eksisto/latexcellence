# plot_gen 后续优化任务书（新对话导入即用）

> 用途：将本文件内容（或本文件路径）导入新的 Codex 对话，即可接续 plot_gen 科研制图 Agent 的优化工作。
> 生成日期：2026-08-01 ｜ 关联报告：[optimization_report.md](optimization_report.md) ｜ 最佳实践：[best_practices.md](best_practices.md)

## 1. 项目上下文速查

| 项 | 值 |
| --- | --- |
| 仓库根目录 | `D:\latexcelence`（Windows，PowerShell） |
| 项目规范 | 先读 `AGENTS.md`、`PROJECT_SUMMARY.md` |
| 目标模块 | `backend/modules/plot_gen/`（科研绘图 Agent） |
| 模块文档 | `docs/modules/plot_gen.md` |
| 核心文件 | `agent.py`（Agent 主循环）、`service.py`（编排）、`recommender.py`（图表推荐）、`data_parser.py`（数据画像）、`executor.py`（执行/TikZ 编译）、`styles.py`（风格预设）、`prompts.py`（提示词，`plot_gen` 命名空间） |
| 配置 | `config.json` → `features.plot_gen`；LLM Key 来自环境变量 `LX_LLM_KEY` |
| 运行测试 | `python -m pytest tests/test_api/test_plot_gen.py tests/test_plot_gen -q`（当前 87 passed；全量 757 passed；需写入系统临时目录与启动子进程，沙箱内需授权） |
| LLM 冒烟 | `python tools/plotgen_opt/smoke_test.py`（验证 chat 与 function calling） |

## 2. 当前优化状态（已完成，勿重复实施）

已实施 11 轮优化（基线 → opt1 → ... → opt11b），5 场景平均分 87 → 100（典型轮）、平均轮数 15.8 → ~6、总时长 480.3s → ~140s，执行/TikZ 成功率保持 5/5：

| 场景 | 基线（分/轮/秒） | opt6（分/轮/秒） | opt7（分/轮/秒） | opt10（分/轮/秒） | opt11b（分/轮/秒） |
| --- | --- | --- | --- | --- | --- |
| s1 时间序列 | 80 / 34 / 234.0 | 100 / 4 / 13.5 | 100 / 4 / 28.8 | 100 / 4 / 14.9 | 100 / 4 / 15.6 |
| s2 热力图 | 100 / 10 / 53.0 | 100 / 7 / 35.7 | 100 / 6 / 33.2 | 100 / 9 / 43.1 | 95 / 11 / 52.0 |
| s3 分布图 | 85 / 11 / 41.0 | 100 / 9 / 26.5 | 100 / 6 / 47.3 | 100 / 8 / 33.8 | 100 / 9 / 38.5 |
| s4 柱状图 | 85 / 11 / 51.1 | 100 / 4 / 13.0 | 100 / 4 / 14.2 | 100 / 6 / 21.9 | 100 / 6 / 20.1 |
| s5 剂量曲线 | 85 / 13 / 101.2 | 100 / 5 / 18.9 | 100 / 4 / 15.4 | 100 / 4 / 14.8 | 100 / 5 / 19.0 |

> opt11/opt11b 视觉审查平均 7.4/10（opt8 6.4、opt11 6.2）；s5 误差棒
> 渲染修复使 fidelity 从 2 升至 7。opt11b LLM 随机性导致 s2 多花 11
> 轮（启发式 95 分），非代码回归。

已改动的文件（opt11/opt11b，勿回退）：

- `backend/modules/plot_gen/tikzgen.py`：渲染前 CSV 校验扩展为 `(error, warnings)` 双返回——
  - `_validate_error_column()`：拦截 `y_plus`/`y_minus` 列空单元格（单重复组 NaN sem 的常见产物，PGFPlots 静默丢误差棒）与非数值，给出 `fillna(0)`/删行修法指引；
  - `_validate_log_positive()`：`xmode`/`ymode=log` 轴禁止 0/负坐标（PGFPlots 静默丢点），指引脚本先过滤非正行；
  - `_validate_bar_series()`：柱状图结构校验——缺类别索引 = 静默缺柱、重复索引 = 一槽双柱、`y` 空值 = 缺柱，均阻断并指明行；
  - 系列 CSV 含 `category` 文本列时逐行核对 `category` 与 `bar.xticklabels[x]` 是否一致（此前 s4 标签错位反复出现的根因），错位直接报错；
  - `_derive_bar_labels()`：`bar.xticklabels` 缺失而系列 CSV 带 `category` 列时按 `x` 索引推导回填 spec 并发警告；
  - `_axis_options()`：柱状图标签长度过长（类别 ≥5 且最长标签 ≥6 字符，或任一标签 ≥10 字符）时确定性注入 `xticklabel style={rotate=45,anchor=east}` 自动旋转，避免水平重叠；
- `backend/modules/plot_gen/tikzgen.py`：**误差棒渲染 bug 修复**（确定性 bug）——`_table_opts` 把 `y error plus`/`y error minus` 列名留在 table 选项（位置本就对），新增 `_error_bar_enable()` 在 `\addplot+[...]` 注入 `error bars/.cd, y dir=both, y explicit`；此前漏掉启用声明，PGFPlots 默认不绘制误差棒，导致 TikZ 图长期无误差棒（matplotlib 预览有但审查器审 TikZ PDF，fidelity 被打 2 分）。验证：`\usepgfplotslibrary{errorbars}` 在 TeX Live 2026 找不到库文件（致命），误差棒是 pgfplots 核心原生能力，无需加载额外 library。
- `backend/core/prompts.py`：agent_system 新增 `LAYOUT RULES`（箱线图不叠加误差棒/均值线、单系列去图例、轴范围紧贴数据），`DATE/AXIS SCALE GUIDANCE` 增 `xmin/xmax/ymin/ymax` 紧贴数据范围；bar chart-specific notes 改为强制"每系列每类别索引一行+缺类别写 0+类别顺序跨系列一致+含 category 文本列供校验"；line notes 增"误差列不得有空单元格，单重复 NaN sem 用 fillna(0) 或删行"；refine_system 同步 bar 顺序与误差棒空值约束；bar 模板补 `category` 文本列说明。
- `backend/modules/plot_gen/agent.py`：`generate` 首轮若只输出文字无工具调用，注入强约束 system 消息重试一次（trace 记 warning），避免零产物空会话收尾。
- `tests/test_plot_gen/test_tikzgen.py`：+11 项（误差列空值/非数值、对数轴非正坐标、柱状图缺索引/重复索引/空 y/类别错位、category 列推导、长标签旋转、误差棒渲染断言更新）。
- `tests/test_api/test_plot_gen.py`：+1 项（首轮文字回复被 nudge 重试）。
- `tools/plotgen_opt/compare.py`：标签扩展至 opt11b；`docs/modules/plot_gen.md`、`PROJECT_SUMMARY.md`：文档同步。

### 已改动的文件（opt7，勿回退）

> opt8（4.5 回归）：s1/s3/s4/s5 全部 100 分，s2 95 分（12 轮），TikZ 5/5。
> opt9（4.6 回归）：全部场景 100 分，平均 6 轮 / 150.6s，TikZ 5/5。
> opt10（4.7 回归，max_rounds=20）：全部场景 100 分，平均 6.2 轮 / 129.2s，TikZ 5/5，最高 9 轮（s2），无误截断。

> opt4 s2 三样本（23 / 9 / 11 轮）取中位数 11 轮；23 轮坏样本源自脚本层
> matplot2tikz 色图兼容问题与 Agent 手写 matrix plot 语法错误，预检未介入
> （属于 4.2 静态校验与 4.4 生成器替换的优化范围），非预检回归。
> opt5 s2 回到 6 轮 / 100 分。
> opt6 全部 100 分；s2/s3 各 1 次 "TikZ file not found" 是 Agent 在生成
> figure.tex 前过早调用 compile_tikz（工作流疏漏），手写路径编译失败 0 次。

已改动的文件（勿回退）：

- `backend/core/prompts.py`：ASCII 图内文字、日期转日序号、四类产物、TikZ 回退协议、失败预算、防验证循环、推荐层单值单元格规则
- `backend/core/prompts.py`：新增 `pgfplots_templates`（box/line/heatmap 已验证模板）+ `render_pgfplots_reference()`，TIKZ PROTOCOL 新增 `col sep=comma` 强制要求并注入按 chart_type 的模板
- `backend/modules/plot_gen/agent.py`：agent_system 注入 `pgfplots_reference`
- `backend/modules/plot_gen/agent.py`：按工具族连续失败 3 次注入强约束消息（`fail_budget`）
- `backend/modules/plot_gen/executor.py`：新增 7 类 PGFPlots 错误模式、nullfont 噪音过滤、figure.tex 编译前预检（`_precheck_tikz`：内联误差棒 / 线性轴超大坐标硬拦截，非 ASCII 警告）
- `backend/modules/plot_gen/executor.py`：`_write_file` 对 `.py` 做 `ast.parse` 语法校验（`_check_python_syntax`），语法错误写入阶段直接拒绝，不发子进程
- `backend/modules/plot_gen/data_parser.py`：`redundancy_hint_pairs()` + 画像 Redundancy hints
- `backend/modules/plot_gen/recommender.py`：命中冗余组合自动 `aggregation=none` / `error_bar=None`
- `tests/test_plot_gen/test_executor.py`：nullfont 过滤 + 预检规则 + 语法校验测试
- `tests/test_plot_gen/test_prompts.py`：新增模板注入测试（59/59 通过）
- `tools/plotgen_opt/compare.py`：对比标签扩展至 opt6
- `docs/modules/plot_gen.md`、`PROJECT_SUMMARY.md`：文档同步

已改动的文件（opt7，勿回退）：

- 新增 `backend/modules/plot_gen/tikzgen.py`：受控确定性 PGFPlots 生成器（替代 matplot2tikz）。脚本产出 `artifacts/data_*.csv` + 声明式 `artifacts/tikz_spec.json`，后端经 Pydantic v2 校验后确定性渲染 `artifacts/figure.tex`；渲染前做 CSV 内容校验（列存在/数值类型/热力图整数索引与 n×n 网格维度），把编译期错误提前到生成期并给出精确修正消息
- `backend/modules/plot_gen/executor.py`：`python_sandbox` 成功后自动调用 `generate_figure_tex()`（事件新增 `tikz` 字段）；`compile_tikz` 文件缺失时回传"先重跑 python_sandbox 再编译"引导；预检文案不再依赖 matplot2tikz 命名
- `backend/core/prompts.py`：TIKZ PROTOCOL 首选确定性生成（含 tikz_spec.json schema 与各图表类型说明），matplot2tikz 移除；新增 bar 验证模板；热力图模板/说明明确 x/y 为整数索引
- `backend/modules/plot_gen/agent.py`：失败预算文案改为"修正 tikz_spec.json/数据 CSV 或手写 PGFPlots"
- `requirements.txt` / `THIRD_PARTY_NOTICES.md`：移除 matplot2tikz 依赖与声明
- `tests/test_plot_gen/test_tikzgen.py`（新增）、`test_executor.py`、`test_prompts.py`：共 +15 项（74/74 通过）
- `tools/plotgen_opt/compare.py`：标签扩展至 opt7
- `docs/modules/plot_gen.md`：TikZ 生成协议更新

已改动的文件（opt8 ~ opt10，勿回退）：

- 新增 `tools/plotgen_opt/visual_review.py`：LLM vision 离线视觉审查（`summary.json` 写 `visual_review`，含轴标签/图例/配色/数据一致性 0~10 评分与 issues/suggestions；优先审查 `tikz_main.pdf` 渲染图）
- `backend/modules/plot_gen/executor.py`：`_validate_stats_inputs()` 按测试类型校验 stats_test 必填参数（group/y、x/y 数值列、chi2 分类列），缺失/类型错误给出明确消息
- `backend/core/config.py` / `config.json` / `config.example.json`：`PlotGenConfig.agent_max_rounds` 默认收紧至 20（template_gen 保持 30）
- `tests/test_plot_gen/test_visual_review.py`（新增）、`test_executor.py` stats_test 校验用例、`tests/test_config_security.py` 默认值同步：plot_gen 相关 87/87 通过，全量 757/757 通过
- `tools/plotgen_opt/compare.py`：标签扩展至 opt10；`PROJECT_SUMMARY.md` / `docs/modules/plot_gen.md` / `docs/modules/plan.md`：文档同步

## 3. 测试基础设施（继续优化的标准流程）

| 工具 | 用法 | 说明 |
| --- | --- | --- |
| 数据生成 | `python tools/plotgen_opt/gen_data.py` | 5 个场景数据集写入 `tools/plotgen_opt/data/`（固定随机种子，可复现） |
| 跑分 | `python tools/plotgen_opt/run_plotgen.py <tag> [s1,...]` | 全场景跑一轮并输出指标（需要 LLM 网络访问授权） |
| 日志分析 | `python tools/plotgen_opt/analyze_trace.py <tag> [scenario,...]` | 逐轮工具调用/失败/LLM 决策摘要 |
| 轮次对比 | `python tools/plotgen_opt/compare.py` | baseline/opt1/opt2/opt3 汇总对比 |
| 视觉审查 | `python tools/plotgen_opt/visual_review.py <tag> [scenario,...]` | LLM vision 审查 figure 并写入 summary.json 的 `visual_review`（轴标签/图例/配色/数据一致性，各 0~10 + issues/suggestions） |
| 产物预览 | `pdftoppm -png -r 100 <pdf> <out>` | PDF → PNG 目检 |

结果存放：`tools/plotgen_opt/results/<tag>/<scenario>/`（`trace.jsonl` + `events.json` + `summary.json`），沙箱在 `plots/<session_id>/`。

质量评分维度（满分 100）：矢量产物 25、执行成功 15、TikZ 成功 15、轮数 ≤10 得 10、无警告 10、PNG 预览 10、summary 5、未截断 10。实现见 `tools/plotgen_opt/run_plotgen.py::quality_score`。

## 4. 后续优化建议（按优先级排序）

### 4.1 后端预检 figure.tex（P1，最高优先）— ✅ 已实施（opt4，2026-08-01）

- **背景**：matplot2tikz 输出是主要故障源，Agent 在 compile 失败后才切换手写路径，仍会浪费 2~3 轮。
- **方案**：在 `executor.py::_compile_tikz` 编译前加轻量预检（纯文本规则，不调用 LLM）：
  - 内联 `y error=` / `+-` 误差棒语法 → 提示"改为手写 PGFPlots 或去掉误差棒"；
  - 绝对值 > 1e4 的坐标（日期序号）→ 提示"转换为日序号/相对坐标"；
  - 非 ASCII 字符（`[^\x00-\x7F]`）→ 提示"标签改为 ASCII"。
  - 预检结果附加到 `compile_tikz` 回传文本，可考虑新增 `precheck` 事件字段。
- **验证结果**：`_precheck_tikz()` 命中内联 `y error=`、`+- (nan, inf)` 增量与线性轴 |坐标|>1e4 时跳过编译直接回传修正建议（对数轴大坐标放行，非 ASCII 仅警告）；事件新增 `precheck` / `precheck_blocked` 字段；补充 7 项单测（51/51 通过）。s1 5→4、s5 15→4 轮，s5 波动消除。

### 4.2 write_file 阶段 Python 静态校验（P1）— ✅ 已实施（opt5，2026-08-01）

- **背景**：s3 等场景多次因 `ax.boxplot` 等 API 用法错误导致 python_sandbox 失败重试。
- **方案**：`executor.py::_write_file` 对 `.py` 文件做 `ast.parse` 语法检查（错误立即回传，不发子进程），并对照数据画像做列名引用抽查（可先只做语法检查，低风险）。
- **验证结果**：语法错误在写入阶段被拒绝（消息含行/列号），合法脚本不受影响；4 项新单测（55/55 通过）。opt5 全场景 avg 100 分 / 4.8 轮 / 91.0s；s2、s3 各残留 1 次 `python_sandbox` 语义型失败（`numpy.ndarray.iloc`、`PathPatch.get_xdata`），Agent 各 1 轮自愈——这类 API 误用是后续 4.4 生成器替换与静态 API 核对的优化范围。

### 4.3 参考脚本注入（按图表类型）（P2）— ✅ 已实施（opt6，2026-08-01）

- **背景**：手写 PGFPlots 仍偶发语法错误（如 `xscale=log` 已修，但坐标/列名错误仍存在）。
- **方案**：在 `backend/core/prompts.py` 的 `plot_gen.agent_system` 中按 `chart_plan.chart_type` 注入 1 段已验证的最小 PGFPlots 模板（箱线图/折线图/热力图各一，ASCII、CSV 按列名引用、`xmode=log`、显式误差列），替换现在的纯文字说明。
- **验证结果**：三个模板先在 standalone 包装（executor 同款）下经 latexmk 人工编译通过；验证中发现并修复 PGFPlots 默认空格分隔符陷阱——模板全部显式 `col sep=comma`（pandas `to_csv` 默认逗号分隔，此前手写路径反复因列名找不到失败）。TIKZ PROTOCOL 同步强制要求 `col sep=comma`。4 项新单测（59/59 通过）。opt6 全场景 100 分，手写路径编译失败 0 次。

### 4.4 评估替换 matplot2tikz（P2）— ✅ 已实施（opt7，2026-08-01）

- **背景**：matplot2tikz 0.5.4 对误差棒、非 ASCII、日期兼容性差（本次所有 TikZ 故障的源头）。
- **方案**：评估 `tikzplotlib` 或受控手写生成器，比较 5 场景的 TikZ 编译一次通过率与文件体积；替换需同时更新提示词中的 API 说明与 `executor.py` 相关校验。
- **验证**：ab 对比 baseline/新生成器各跑 2~3 次取中位数。
- **评估结论（2026-08-01）**：tikzplotlib 0.10.1 与当前 matplotlib 3.11.1 不兼容（`from matplotlib.backends.backend_pgf import common_texification` 已移除，import 即 ImportError），且上游已不再维护，排除；采用**受控手写生成器**（后端确定性渲染）。
- **验证结果（opt7）**：opt7 主跑 5/5 TikZ 一次通过，全场景典型轮 100 分，平均 4.8 轮 / 138.9s；s2 三样本（100/6、35/1 退化样本、95/12）取中位数 95 分（退化样本为 LLM 首轮只输出文字未调用工具，非代码回归）。figure.tex 体积 0.8~4.5KB，较 matplot2tikz（4~15KB）缩小 50%~85%；s2 热力图由"内嵌 PNG + 文本覆盖"变为真正矢量 `matrix plot` + `\node` 显著性标注。opt7b 全场景除 s2 退化样本外均 100 分。

### 4.5 LLM 视觉质量审查门禁（P3）— ✅ 已实施（opt8，2026-08-01）

- **背景**：当前质量评分为启发式（产物存在性/轮数），无法评估图面美观与数据保真。
- **方案**：新增审查脚本，把 `figure.png` 或 PDF 渲染图 + chart_plan 交给 LLM（vision）做结构化评分（轴标签、图例、配色、数据一致性），结果写入 `summary.json` 的 `visual_review` 字段；可先离线用于评估，再接为回归门禁。
- **验证结果（opt8）**：`visual_review.py` 优先渲染 `tikz_main.pdf`（pdftoppm）审查 TikZ 编译产物，缺失时回退 `figure.png`；Pydantic 校验 + `retry_with_validation` 解析结构化评分。opt7 平均 7.0/10、opt8 平均 6.4/10。审查发现启发式 100 分掩盖的真实问题：s4 柱状图类别标签顺序与数据不一致（Agent 把 CSV 按原始类别序、xticklabels 按字母序，柱体标签错位）；s5 误差棒数据在剂量边缘缺失（`sem` 为空/NaN，TikZ 不渲染误差棒）；s1 日期轴右侧留白。新增 5 项单测（79/79 通过）。

### 4.6 stats_test 参数校验增强（P3）— ✅ 已实施（opt9，2026-08-01）

- **背景**：`executor.py::_run_stats_test` 对 `group`/`y`/`x` 校验松散（如 `y` 缺失时 `df.loc[df[group]==g, None]` 报 KeyError）。
- **方案**：按测试类型做必填参数校验（t-test/wilcoxon 要求 group+y；pearson/spearman 要求 x+y 数值列；chi2 要求 x+y 分类列），错误消息指明缺失参数；补单测。
- **验证结果（opt9）**：`_validate_stats_inputs()` 按测试类型校验必填参数，缺失时指明参数名与数据文件可用列；数值/分类类型不符（含 chi2 高基数数值列）给出明确消息，不再抛 KeyError。新增 8 项单测（87/87 通过）；opt9 全场景 100 分、TikZ 5/5，无行为回归。

### 4.7 轮数上限与超时收敛（P4）— ✅ 已实施（opt10，2026-08-01）

- **背景**：`config.json` 中 `agent_max_rounds=50` 偏宽松，配合失败预算后多数场景 4~6 轮收敛。
- **方案**：将默认上限收紧到 20（`backend/core/config.py` 的 `PlotGenConfig` 与 `config.json`/`config.example.json` 同步，前端无需改动），极端场景靠失败预算尽早结束；观察是否出现误截断。
- **验证结果（opt10）**：`PlotGenConfig.agent_max_rounds` 默认与 `config.json`/`config.example.json` 统一为 20（`template_gen` 保持 30 不变）；opt10 全场景 100 分、TikZ 5/5，轮数 4~9（平均 6.2），未触发截断（`truncated=False`）。87/87 测试通过。

### 4.8 TikZ 生成期数据保真校验 + 误差棒渲染修复（P2）— ✅ 已实施（opt11/opt11b，2026-08-10）

- **背景**：opt8 视觉审查发现启发式 100 分掩盖的静默数据保真问题——s5 剂量曲线 TikZ 图无误差棒、s4 柱状图类别标签与柱体错位、误差列空值被 PGFPlots 静默吞掉。这些问题编译都成功但图是错的，生成期不阻断。
- **方案**：
  1. `tikzgen.py::_validate_csvs` 扩渲染前 CSV 校验：误差列空值/非数值、对数轴非正坐标（PGFPlots 静默丢点）、柱状图缺索引/重复索引/空 y/category 与 xticklabels 顺序错位、xticklabels 缺失时按 category 列推导回填、长标签自动旋转 45°。
  2. **误差棒渲染确定性 bug 修复**：table 里的 `y error plus/minus` 只声明列，PGFPlots 默认不绘制，必须在 `\addplot+[...]` 加 `error bars/.cd, y dir=both, y explicit` 才会画——此前漏掉启用声明，TikZ 图长期无误差棒。
  3. 提示词新增 LAYOUT RULES 与 bar/line 误差空值、类别顺序强约束；首轮只输出文字无工具调用时注入强约束重试。
- **验证结果**：`_validate_error_column`/`_validate_log_positive`/`_validate_bar_series`/`_derive_bar_labels` 在生成期阻断静默保真问题并给出精确修法；误差棒 bug 修复后 s5 TikZ PDF 36KB→37KB，PNG 渲染出现误差棒像素。opt11b 视觉审查平均 7.4/10（opt8 6.4、opt11 6.2），s5 fidelity 2→7；opt11b 5 场景执行/TikZ 5/5，全量 1371 测试通过（+11 tikzgen、+1 agent）。

## 5. 已知边界与注意事项

- **LLM 随机性**：同一场景同版本多次运行轮数差异可达 4~15 轮；结论至少取 2~3 次运行中位数。
- **提示词模板转义**：`get_prompt` 使用 `str.format()`，提示词内字面花括号必须写 `{{ }}`（如 `\addplot table {{file.csv}}`、`\end{{tikzpicture}}`），改完必须跑测试（模板未转义会抛 `KeyError`）。
- **测试环境**：pytest 需写系统临时目录并启动子进程，沙箱内需授权运行；改动后跑 `tests/test_api/test_plot_gen.py` + `tests/test_plot_gen` 全套（当前 74 项）。
- **文档同步**：改动提示词/流程后同步更新 `docs/modules/plot_gen.md`；新增/删除模块需更新 `PROJECT_SUMMARY.md`；文档中禁止硬编码模块数量。
- **安全边界**：`python_sandbox` 为 Python 层审计钩子防护，非操作系统级沙箱；新代码不得放宽 `sandbox_guard.py` 的拦截规则。
- **产物一致性**：手写 PGFPlots 与 matplotlib 产物可能随时间分叉，微调会话需同时重新生成两者。

## 6. 本次优化遗留的观察项

- ✅ s5（剂量曲线）opt4 稳定收敛到 4 轮 / 100 分，此前 opt2=4 / opt3=15 的波动消除。
- ✅ s2（热力图）opt5 回到 6 轮 / 100 分；opt4 波动（23 / 9 / 11 轮）源于脚本层语义错误（`p_values.iloc`、matplot2tikz 自定义色图），语法校验与预检无法覆盖，待 4.4 生成器替换后再观察。
- ✅ "TikZ file not found" 工作流疏漏：`compile_tikz` 找不到 figure.tex 时已回传"先重跑 python_sandbox（后端据 tikz_spec.json 重新生成）或手写 figure.tex"的引导（opt7）。
- ✅ s2 热力图生成器替换：opt7 产物为真正矢量 `matrix plot`（RdBu_r 色标 + `\node` 显著性标注），不再内嵌 `figure-000.png`；`figure-000.png` 随 matplot2tikz 移除不再产生。
- ✅ s2 视觉补审（4.5 上线后）：opt7/opt8 的 s2 均 7/10；主要问题为显著性标注文字过小/与单元格错位、对角线未隐藏、相关性普遍偏高导致色差小（数据特性）。
- ✅ s4 柱状图标签顺序：opt11 起 `_validate_bar_series` 在生成期逐行核对系列 CSV 的 `category` 与 `bar.xticklabels[x]`，错位直接报错并指明行；提示词强制"类别顺序跨系列一致 + 含 category 列供校验"。opt11/opt11b 未再出现标签错位。
- ✅ s5 误差棒边缘缺失：opt11 起 `_validate_error_column` 拦截 `y_plus`/`y_minus` 列空值，指引 `fillna(0)` 或删行；同时修复误差棒渲染 bug（`\addplot` 加 `error bars/.cd, y dir=both, y explicit`），TikZ 图现在真正画出误差棒，fidelity 2→7。
- ✅ opt7b s2 退化样本（首轮仅输出文字未调用工具）：opt11 起 generate 首轮无工具调用时注入强约束 system 消息重试一次，不再零产物收尾。
- ⏳ s2 热力图显著性标注位置与对角线处理：属 LLM 脚本层 `cell_text` 坐标问题与数据特性（相关性普遍偏高导致色差小），非 tikzgen 确定性 bug，后续可在提示词或 `_validate_csvs` 加 cell_text 坐标边界校验。
- ⏳ s4 柱状图异常值（Furniture-South=240000）把 y 轴拉满，其余柱被视觉压扁被误判为"缺数据"：纯数据特性，非代码 bug；后续可在提示词提示大异常值时用对数 y 轴或截断轴。
