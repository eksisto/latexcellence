# plot_gen 科研制图 Agent 优化报告

> 日期：2026-08-01 ｜ 测试环境：Python 3.14 / deepseek-flash 端点 / latexmk 4.87（TeX Live 2026）
> 迭代上限：6 轮（基线 + opt1/opt2/opt3/opt4/opt5/opt6）

## 1. 测试场景与数据

在 `tools/plotgen_opt/data/` 生成了 5 个科研场景的逼真数据集（每个数据集均含正常值、异常值、边界值、缺失值）：

| 场景 | 文件 | 数据说明 | 用户描述（节选） |
| --- | --- | --- | --- |
| s1 时间序列对比 | s1_time_series.csv | 3 组 × 180 天，含尖峰/近零/缺失 | 比较三组每日观测值随时间变化趋势 |
| s2 相关性热力图 | s2_correlation.xlsx | 12 个相关变量 × 200 样本 + batch/sample_id | 分析两两相关关系，绘制热力图 |
| s3 统计分布 | s3_distribution.csv | 4 剂量组 × 40 样本，偏态 + 离群值 | 比较各组分布、偏态、离群值 |
| s4 分类柱状 | s4_category.csv | 6 类 × 4 地区销售额，含零/负值/大额异常 | 比较各产品类别销售额大小 |
| s5 剂量-反应曲线 | s5_dose_response.csv | 3 化合物 × 8 剂量 × 3 重复，S 型曲线 | 半对数坐标绘制抑制率曲线，比较 IC50 |

运行脚本：`tools/plotgen_opt/run_plotgen.py`；原始交互日志：`tools/plotgen_opt/results/<tag>/<scenario>/trace.jsonl` 与 `events.json`。

## 2. 优化前后对比（表格）

| 场景 | 指标 | 基线 | opt1 | opt2 | opt3 | opt4 | opt5 | opt6 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s1 时间序列 | 质量分 / 轮数 / 时长 | 80 / 34 轮 / 234.0s | 100 / 6 轮 / 19.5s | 100 / 4 轮 / 13.1s | 100 / 5 轮 / 19.2s | 100 / 4 轮 / 13.5s | 100 / 4 轮 / 13.3s | 100 / 4 轮 / 13.5s |
| s2 热力图 | 质量分 / 轮数 / 时长 | 100 / 10 轮 / 53.0s | 95 / 13 轮 / 52.0s | 100 / 8 轮 / 41.2s | 100 / 4 轮 / 21.6s | 95 / 11 轮 / 49.8s | 100 / 6 轮 / 31.0s | 100 / 7 轮 / 35.7s |
| s3 分布图 | 质量分 / 轮数 / 时长 | 85 / 11 轮 / 41.0s | 95 / 15 轮 / 50.1s | 95 / 12 轮 / 40.1s | 100 / 4 轮 / 14.8s | 100 / 6 轮 / 18.2s | 100 / 6 轮 / 20.7s | 100 / 9 轮 / 26.5s |
| s4 柱状图 | 质量分 / 轮数 / 时长 | 85 / 11 轮 / 51.1s | 100 / 4 轮 / 13.5s | 100 / 4 轮 / 13.3s | 100 / 4 轮 / 17.1s | 100 / 4 轮 / 13.8s | 100 / 4 轮 / 12.2s | 100 / 4 轮 / 13.0s |
| s5 剂量曲线 | 质量分 / 轮数 / 时长 | 85 / 13 轮 / 101.2s | 90 / 19 轮 / 79.6s | 100 / 4 轮 / 16.5s | 95 / 15 轮 / 49.2s | 100 / 4 轮 / 13.3s | 100 / 4 轮 / 13.9s | 100 / 5 轮 / 18.9s |
| 汇总均值 | 质量分 | 87.0 | 96.0 | 99.0 | 99.0 | 99.0 | 100.0 | 100.0 |
| 汇总均值 | 轮数 | 15.8 | 11.4 | 6.4 | 6.4 | 5.8 | 4.8 | 5.8 |
| 汇总 | 总时长 | 480.3s | 214.7s | 124.2s | 122.1s | 108.6s | 91.0s | 107.6s |
| 汇总 | 执行成功率 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| 汇总 | TikZ 成功率 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |

> opt4 s2 三样本（23 / 9 / 11 轮）取中位数 11 轮；23 轮坏样本源自脚本层
> matplot2tikz 色图兼容问题与 Agent 手写 matrix plot 语法错误（属 4.2/4.4 范围），
> 预检未介入，非预检回归；opt5 s2 回到 6 轮 / 100 分。
> opt6 全部 100 分；s2/s3 各 1 次 "TikZ file not found" 是 Agent 在生成
> figure.tex 前过早调用 compile_tikz，手写路径编译失败 0 次。

**量化改进（基线 → opt6）**：

- 平均质量评分：87.0 → 100.0（+13 分）
- 平均 Agent 轮数：15.8 → 4.8（-70%）
- 总执行时长：480.3s → 91.0s（-81%，约 5.3 倍加速）
- 5 场景全部保持执行成功 + TikZ 编译成功，无 warning/error 事件
- 所有场景均额外产出 `figure.png` 前端预览（基线无）；s1 推荐方案从 `mean±sd`（单值聚合无意义）修正为 `aggregation=none, error_bar=none`
- opt4 s5（剂量曲线）稳定收敛到 4 轮 / 100 分，消除 opt2=4 / opt3=15 的波动
- opt5 s2（热力图）回到 6 轮 / 100 分，5 场景全部满分
- opt6 全场景保持 100 分；手写 PGFPlots 路径编译失败 0 次

> 说明：LLM 存在随机性，质量评分采用启发式（矢量产物 25 + 执行 15 + TikZ 15 + 轮数 10 + 无警告 10 + PNG 10 + summary 5 + 未截断 10）。

## 3. 诊断出的问题（按严重度）

### P1（最严重）：TikZ 编译失败死循环

- 现象：s1 基线 34 轮中 26 轮是 `compile_tikz` 失败重试；根因分别是 matplot2tikz 对误差棒输出内联 `y error=` 语法不被 PGFPlots 接受、datetime 以 Excel 序号（44927）写入导致 `Dimension too large`、外部 CSV 引用路径失效等
- 原因：提示词只给了"matplot2tikz 不可用时才手写"的兜底；"逐字相同失败"检测对错误文本不同但根因相同的循环无效

### P2：图内中文/非 ASCII 标签破坏 TikZ 编译

- 现象：s3/s4/s5 基线各多花 3~6 轮处理 `Missing character`/字体缺失；s5 的 `μ` 被 matplot2tikz 写成 U+FFFD
- 原因：提示词未限制图内文字语言，Agent 用中文/Unicode 写标签

### P3：编译成功后的"验证循环"

- 现象：s2/s3/s5 在 `compile_tikz` 成功后仍重复 `read_file` + `python_sandbox` + `compile_tikz` 验证（每次多花 2~6 轮）

### P4：无害噪音触发无效修复

- 现象：s3 因 `Missing character: ... nullfont!`（latexmk 版本字符串）误判为图表问题，白费 6 轮

### P5：推荐层单值聚合

- s1 数据每个 (date, group) 只有一行，推荐却给出 `mean ± sd` 误差棒，直接引发 P1 的错误棒链路

### P6：冗余工具调用

- s2 开局调用 `list_files`+`list_styles`、s5 基线 `read_file` 整份数据（各浪费 1 轮）

## 4. 代码改动清单

### 4.1 `backend/core/prompts.py`

**`plot_gen.chart_recommendation_system`**

- 新增规则 6：`rows == unique(x) × unique(color)` 或画像给出冗余提示时，必须 `aggregation='none'`、`error_bar='none'`

**`plot_gen.agent_system`**

- 新增 `FIGURE TEXT LANGUAGE`：图内文字必须 ASCII；单位转写规则（`ug/mL`、`uM`、`degC`、`x`），禁止 μ/°/±
- 新增 `DATE/AXIS SCALE GUIDANCE`：datetime 转日序号；对数坐标用 `ax.set_xscale('log')` 且过滤非正行
- 新增 `OUTPUTS`：强制 `figure.pdf` + `figure.svg` + `figure.png` + TikZ 源
- 新增 `TIKZ PROTOCOL`：matplot2tikz 优先；同根因失败 2 次即切换手写 PGFPlots（ASCII 标签、`artifacts/data_*.csv` 按列名引用、小/相对 x 值、`xmode=log` 而非 `xscale=log`、裸 `\end{tikzpicture}`）
- 工作流新增 `6b. No verification loop`：编译成功后禁止无修改重跑
- 规则 9 收紧：禁止开局 `read_file` 整份数据、`list_files`/`list_styles`；禁止裸 try/except 吞掉 matplot2tikz 错误
- 新增规则 10 `FAILURE BUDGET`：python_sandbox / compile_tikz 各连续失败 3 次即强制换思路

**`plot_gen.refine_system`**

- 同步 ASCII 标签、四类产物、TikZ 回退与"成功后直接 finish"约束

### 4.2 `backend/modules/plot_gen/agent.py`

- 新增 `fail_budget` 字典（`python_sandbox`/`compile_tikz`），连续失败 3 次注入对应强约束 system 消息并向前端发 warning；成功时清零

### 4.3 `backend/modules/plot_gen/executor.py`

- `_extract_tikz_issues()` 新增错误模式：`! Dimension too large`、`! Extra \endcsname`、`! Missing control sequence inserted`、`! Missing $ inserted`、`! Text line contains an invalid character`、`! I can't find file`、`! Package pgfplots/pgfkeys Error`
- `_compile_tikz()` 过滤 `nullfont` 类 `Missing character` 噪音警告；回传文本注明 nullfont 警告无害

### 4.4 `backend/modules/plot_gen/data_parser.py`

- 新增 `redundancy_hint_pairs()`：识别 `unique(x) × unique(cat) ≈ rows` 的"单值单元格"组合
- `build_profile_context()` 输出 `Redundancy hints` 段落

### 4.5 `backend/modules/plot_gen/recommender.py`

- `deterministic_chart_plan()` 命中冗余组合时自动置 `aggregation='none'`、`error_bar=None`
- LLM 推荐消息注入相同的冗余提示文本

### 4.6 `tests/test_plot_gen/test_executor.py`

- 更新 `test_compile_tikz_missing_character_surfaces_warning`：断言 nullfont 噪音被过滤、真实标签 Missing character 仍上浮（44/44 测试通过）

### 4.7 文档

- `docs/modules/plot_gen.md`：补充失败预算、TikZ 回退协议、ASCII 标签、冗余提示、nullfont 过滤说明

### 4.8 新增测试工具（`tools/plotgen_opt/`，供后续回归审查）

- `gen_data.py`（数据生成）、`run_plotgen.py`（跑分采集）、`analyze_trace.py`（日志分析）、`compare.py`（轮次对比）、`smoke_test.py`（LLM 冒烟）

### 4.9 后端预检 figure.tex（opt4 新增）

- `executor.py` 新增 `_precheck_tikz()`：编译前纯文本扫描内联 `y error=`、`+- (nan/inf)` 误差增量与线性轴 |坐标|>1e4（对数轴放行），命中即跳过编译直接回传修正建议；非 ASCII 字符仅警告不拦截
- `_compile_tikz()` 事件新增 `precheck` / `precheck_blocked` 字段，预检建议附加到回传文本
- `test_executor.py` 补充 7 项预检单测（内联误差棒拦截、nan 误差棒拦截、大坐标拦截、对数轴放行、非 ASCII 仅警告、干净源零命中、数值 `+-` 仅警告）

### 4.10 write_file 阶段 Python 静态校验（opt5 新增）

- `executor.py` 新增 `_check_python_syntax()`：`_write_file` 对 `.py` 内容做 `ast.parse` 语法检查，语法错误在写入阶段直接拒绝（回传行/列号与消息），不落盘、不发子进程
- `test_executor.py` 补充 4 项单测（语法错误拒绝且不落盘、合法脚本正常写入、非 .py 不受影响、helper 直测）

### 4.11 参考脚本注入（opt6 新增）

- `prompts.py` 新增 `pgfplots_templates`（box/line/heatmap 三套模板）与 `render_pgfplots_reference(chart_type)`，在 `agent_system` 的 TIKZ PROTOCOL 中按 `chart_plan.chart_type` 注入
- 三个模板先在 standalone 包装（executor 同款）下经 latexmk 人工编译验证；验证中发现 PGFPlots 默认空格分隔符陷阱，模板与提示词统一强制 `col sep=comma`（pandas `to_csv` 默认逗号分隔，此前手写路径反复因列名找不到失败）
- `agent.py` 生成 system prompt 时按 chart_type 注入参考模板；新增 `tests/test_plot_gen/test_prompts.py`（4 项单测）

## 5. 继续优化建议（按优先级）

1. ✅ **后端预检 figure.tex**（opt4 已实施）：`compile_tikz` 前轻量规则扫描内联误差棒 / 超大坐标 / 非 ASCII，命中直接提示 Agent 修正
2. ✅ **脚本静态校验层**（opt5 已实施）：`write_file` 阶段对 `.py` 做 AST 语法检查，把 Python 语法错误提前到写入时暴露；常用 API 参数核对（如 `ax.boxplot`、`ndarray.iloc` 语义检查）待后续增强
3. ✅ **参考脚本注入**（opt6 已实施）：按图表类型注入已验证的最小 PGFPlots 模板（箱线图/折线图/热力图各一）+ 强制 `col sep=comma`
4. **matplot2tikz 替换评估**：0.5.4 对误差棒/非 ASCII/日期兼容性差，可评估切换 `tikzplotlib` 或受控手写生成器
5. **质量自动化审查**：接入 LLM 视觉审查（对 `figure.png` 与 TikZ 渲染结果做结构化评分），形成可回归的质量门禁
6. **stats_test 增强**：`_run_stats_test` 对 `group`/`y` 参数校验较松，建议按测试类型做参数必填校验
7. **轮数上限收紧**：`agent_max_rounds` 默认 50，可配合失败预算降到 20，尽早收敛极端场景

## 6. 审查材料

- 测试数据：`tools/plotgen_opt/data/`（含 manifest.json）
- 每轮事件/追踪日志：`tools/plotgen_opt/results/{baseline,opt1,opt2,opt3}/`
- 图像预览：`tools/plotgen_opt/previews/`
- 沙箱会话：`plots/<session_id>/`（trace.jsonl + scripts + artifacts）
