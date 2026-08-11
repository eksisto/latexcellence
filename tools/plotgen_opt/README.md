# plot_gen 优化闭环工具（tools/plotgen_opt/）

本目录复现并驱动 `plot_gen` Agent 的「生成 → 评估 → 诊断 → 优化 → 再生成」
闭环，用于**离线评测与提示词迭代**，是开发调试工具，不参与生产运行
（与 `tools/templategen_opt/` 同模式）。

它直接调用 `backend/modules/plot_gen/service.py` 的 `PlotGenService`：先用一套预置的
科研数据集上传建会话，再对每个场景驱动 Agent 走「数据画像 → 图表方案 → 写脚本 →
Python 沙箱执行 → TikZ 编译 → finish」完整流程，并自动采集轮数 / 时长 / 成功率 /
启发式质量评分等指标，供对比不同版本提示词 / Agent 逻辑。

> 注意：本目录的脚本、数据集与报告纳入版本库管理；运行产物
> （`results/`、`previews/`、`__pycache__/`）由目录内 `.gitignore` 忽略，不会提交。

---

## 目录结构

```
tools/plotgen_opt/
├── gen_data.py       # 生成 5 个科研制图场景的逼真测试数据集（含正常/异常/边界/缺失值）
├── smoke_test.py     # LLM 端点 + Function Calling 冒烟测试
├── run_plotgen.py    # 主运行器：上传数据 → 驱动 Agent → 事件采集 → 启发式评分
├── analyze_trace.py  # trace.jsonl 分析：每轮工具调用序列 / 失败点 / LLM 决策摘要
├── visual_review.py  # LLM 视觉审查：TikZ PDF（或 PNG 回退）→ vision 模型分维度评分
├── test_visual_review.py  # visual_review.py 的单元测试（模型校验 / base64 / 用户消息构建）
├── compare.py        # baseline / opt1..opt10 全量对比表格
├── data/             # 测试数据集（gen_data.py 生成）+ manifest.json 清单
├── previews/         # matplotlib 与 TikZ 渲染预览 PNG
├── results/<tag>/    # 每次运行的结果根目录（<tag> 如 baseline / opt1）
└── report/           # 历次优化结论：optimization_report.md / best_practices.md / next_steps.md
```

## 评测场景与数据集（gen_data.py）

`SCENARIOS` 定义了 5 个代表性科研制图场景，每个数据集都注入正常值、异常值、
边界值与缺失值，考验 Agent 的数据处理能力：

| key | 文件 | 数据说明 | 用户描述（要点） |
| --- | --- | --- | --- |
| `s1_time_series` | s1_time_series.csv | 3 组 × 180 天每日观测，含尖峰/近零/缺失 | 三组随时间变化趋势对比 |
| `s2_correlation` | s2_correlation.xlsx | 12 个环境变量 × 200 样本 + batch/sample_id，含缺失与离群 | 两两相关关系热力图 |
| `s3_distribution` | s3_distribution.csv | 4 剂量组 × 40 样本，偏态 + 离群值 + 零边界 | 各组分布 / 偏态 / 离群值 |
| `s4_category` | s4_category.csv | 6 类 × 4 地区销售额，含零销量/负值/大额异常 | 各类别销售额对比 |
| `s5_dose_response` | s5_dose_response.csv | 3 化合物 × 8 剂量 × 3 重复 S 型曲线，含越界/缺失 | 半对数剂量-反应曲线，比较 IC50 |

`gen_data.py` 使用固定随机种子（`seed_all(42)`），数据可复现；运行后写入
`data/` 各文件并生成 `manifest.json`（含行数 / 列数 / 缺失数统计）。

## 环境要求

- 项目根目录存在有效 `config.json`（脚本内部 `load_config("config.json")`），
  LLM 端点可访问。
- 具备 TeX Live（`latexmk` 等）以支持 TikZ 编译；`pdftoppm`（随 TeX Live）用于
  视觉审查截图。
- 评测脚本会自动调用共享的 `tools/tex_path.py`：扫描
  `/usr/local/texlive/*/bin/*/latexmk` 与 `/opt/texlive/*/bin/*/latexmk` 并加入
  `PATH`，也支持通过环境变量 `TEXLIVE_BIN` 显式指定 bin 目录；非交互 shell
  即使没有加载 `~/.bashrc` 也能找到 latexmk。
- 生成数据需 `numpy` / `pandas` / `openpyxl`（写 xlsx）；绘图 Agent 依赖
  `matplotlib` / `scipy`。
- Python 依赖同后端，建议在项目 `.venv` 下运行。

## 快速开始

```bash
# 0. （可选）冒烟测试：验证 LLM 端点 + Function Calling 可用
python tools/plotgen_opt/smoke_test.py

# 1. （可选）重新生成测试数据集（已存在可跳过）
python tools/plotgen_opt/gen_data.py

# 2. 运行 Agent 生成并采集指标（tag 默认 baseline，如用 optN 区分迭代）
python tools/plotgen_opt/run_plotgen.py opt3

# 3. 分析 trace，定位失败环节
python tools/plotgen_opt/analyze_trace.py opt3

# 4. LLM 视觉审查（TikZ 编译 PDF → pdftoppm → PNG → vision 评分 0-10）
python tools/plotgen_opt/visual_review.py opt3

# 5. 汇总对比 baseline / opt1..opt10 全量结果
python tools/plotgen_opt/compare.py
```

各命令的完整用法见下文。

---

## 命令详解

### 1. `smoke_test.py`

冒烟测试：先验证 `llm.chat` 普通对话，再验证 `llm.chat_with_tools` Function Calling
（调用一个 `ping` 工具）。任何一步失败说明 LLM 端点或配置有问题，后续评测无意义。

### 2. `gen_data.py`

按固定种子生成全部场景数据集到 `data/`，并写入 `manifest.json`。参数可选
（无参数运行全部场景）。通常只在首次使用或需要重置数据时运行。

### 3. `run_plotgen.py [tag] [scenario1,scenario2,...]`

核心运行器。参数：

- `[tag]`（可选）：结果目录名，**默认 `baseline`**；迭代时用 `opt1`、`opt2`…
- `scenario1,...`（可选）：逗号分隔的场景 key，省略时运行全部场景。

对每个场景执行：

1. 读取 `data/<file>`，调用 `PlotGenService.create_session_from_upload()` 上传建会话；
2. 调用 `generate()` 流式消费 SSE 事件（每条带 `ts_ms` 相对时间戳），统计
   `upload / recommendation / agent / total` 各阶段耗时；
3. 从 Agent 沙箱（`plots/<session_id>/`）拷贝 `trace.jsonl` 副本；
4. 统计工具调用分布、警告数、错误数、执行成功 / TikZ 编译成功标志、轮数与是否
   截断（达到 `features.plot_gen.agent_max_rounds`）；
5. 汇总启发式质量评分（0-100）。

输出（每个场景）：

```
results/<tag>/<scenario>/
├── events.json    # 全部 SSE 事件（含 ts_ms 时间戳）
├── summary.json   # 指标汇总 + 质量评分 + chart_plan + 产物清单
└── trace.jsonl    # Agent 运行追踪日志副本
```

全部场景结束后写入 `results/<tag>/all_summary.json`，并在终端打印各场景
`score / rounds / exec / tikz / warn / err / time`。

### 4. `analyze_trace.py [tag] [scenario1,scenario2,...]`

分析 trace 定位失败环节。参数：`[tag]` 默认 `baseline`；`[scenario...]` 省略时
分析该 tag 下全部场景。

对每个场景读取 `trace.jsonl`，打印可读时间线：每轮 LLM 的工具调用名与文本摘要、
每个工具结果的成功 / 失败标志（失败时附带参数摘要）、warning / error、`run_end`
的轮数与成败标志。纯控制台输出，无写入文件。

### 5. `visual_review.py <tag> [scenario1,scenario2,...]`

LLM 视觉质量审查。对每个场景：

1. 优先选择沙箱 `artifacts/tikz_main.pdf`，用 `pdftoppm` 渲染第一页为 PNG；
   缺失或渲染失败时回退到 matplotlib 产物 `artifacts/figure.png`；
2. 图片缩放到 1024px 内 → base64 → 连同数据描述与 `chart_plan` 发给 LLM
   （vision，经 `retry_with_validation()` 容错校验重试）；
3. 按维度打分，写入 `summary.json` 的 `visual_review` 字段。

评分维度（各 0~10）：`axis_labels`（轴标题/刻度/单位）、`legend`（图例）、
`colors`（配色）、`data_fidelity`（与数据/图计划的一致性）、`overall`（总评），
外加 `issues` / `suggestions` 问题与建议列表。

汇总写入 `results/<tag>/visual_reviews.json`，并打印平均 `overall` 与所用图源。

> 该审查为离线评估，不参与 `run_plotgen.py` 的启发式评分，两者互补。

### 6. `compare.py`

汇总对比 `baseline` 与 `opt1`..`opt10` 中已存在的 tag：逐场景打印
`质量分 / 轮数 / 时长`，并汇总各 tag 的平均分、平均轮数、总时长、执行 / TikZ
成功率与总警告 / 错误数。纯控制台输出，无参数。

### 7. `test_visual_review.py`

`visual_review.py` 的离线单元测试：覆盖 `VisualReview` 模型校验（含越界拒绝）、
`_image_to_base64`（data URI 格式与超大图缩）与 `_build_user_content`
（多模态消息结构），不依赖 LLM。运行：

```bash
python -m pytest tools/plotgen_opt/test_visual_review.py -q
```

---

## 评分体系

### 启发式质量分（`summary.json → score`，0-100）

| 项 | 满分 | 判定 |
| --- | --- | --- |
| vector_outputs | 25 | PDF+SVG 齐全 25 分；仅 PDF 15 分 |
| execution_success | 15 | Python 脚本执行成功 |
| tikz_success | 15 | TikZ 编译成功 |
| rounds | 10 | ≤10 轮 10 分；≤16 轮 5 分 |
| no_warnings | 10 | 事件流无 warning |
| png_preview | 10 | 存在 PNG 预览产物 |
| summary | 5 | 最终 summary 非空 |
| not_truncated | 10 | 未触发 max_rounds 截断 |

各细分项见 `summary.json` 的 `detail`。另记录 `tool_calls` 分布、
`chart_plan`、`artifacts` 清单与各阶段耗时，便于人工诊断。

### 视觉审查（`summary.json → visual_review`）

各维度 0-10，`overall` 为总评，`issues` / `suggestions` 为具体问题与改进建议。

---

## 推荐的优化闭环工作流

```
1. 冒烟测试        python tools/plotgen_opt/smoke_test.py
2. 跑基线          python tools/plotgen_opt/run_plotgen.py baseline
3. 视觉审查        python tools/plotgen_opt/visual_review.py baseline
4. 分析 trace      python tools/plotgen_opt/analyze_trace.py baseline
5. 定位失败环节
   - 轮数爆炸      → 看 trace 的重复失败工具与根因（常见：TikZ 编译死循环、验证循环）
   - 执行失败      → 看 trace 中 python_sandbox 的报错与修复路径
   - 视觉差        → 看 visual_reviews.json 的 issues（轴标签/图例/配色/数据一致性）
6. 修改提示词/逻辑  backend/core/prompts.py 或 backend/modules/plot_gen/
7. 重跑对比        python tools/plotgen_opt/run_plotgen.py optN && \
                   python tools/plotgen_opt/visual_review.py optN && \
                   python tools/plotgen_opt/compare.py
8. 达标后         将获胜的提示词固化进 backend/core/prompts.py
```

## 历史优化结论（report/）

- `report/optimization_report.md`：一次完整优化闭环的报告（基线 → opt1..opt6），
  含逐场景改进表格、诊断出的问题分级（P1-P6）与量化收益，可作后续优化的参照基线。
- `report/best_practices.md`：可复用的最佳实践——图内文字必须 ASCII、两级 TikZ
  逃生通道（受控生成 / 手写 PGFPlots）、按工具族失败预算、防验证循环、固定种子
  数据集等。
- `report/next_steps.md`：后续可探索的优化方向。

## 常见问题

- **数据集缺失**：先运行 `python tools/plotgen_opt/gen_data.py` 重新生成。
- **LLM 端点异常**：先运行 `python tools/plotgen_opt/smoke_test.py` 验证连通性，再查
  `config.json` 的 `llm` 配置。
- **TikZ 编译失败**：查看 `trace.jsonl` 中 `compile_tikz` 的报错文本与失败次数；
  参考 `report/best_practices.md` 的手写 PGFPlots 细节与失败预算策略。
- **`pdftoppm` 不可用**：视觉审查回退到 matplotlib PNG，或跳过该场景。
- **LLM 随机性**：单次结果波动正常，结论至少看 2~3 次运行，或以 `compare.py`
  汇总均值判断。
