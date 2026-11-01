# plot_gen 科研绘图 Agent 闭环优化复盘与最佳实践

> 本文是 `tools/plotgen_opt/` 闭环优化工作区的唯一文档入口，整合了原
> `tools/plotgen_opt/report/` 下的优化报告、最佳实践与后续任务书（该目录已删除），
> 并按轮次追加复盘、只追加不覆盖。通用性门禁的完整规则与事故复盘见
> [templategen-optimization-lessons.md](templategen-optimization-lessons.md)。

## 1. 评测工具与场景

`tools/plotgen_opt/` 是离线闭环：`run_plotgen.py` 直接驱动
`backend/modules/plot_gen/service.py`，对每个场景走完整 Agent 流程
（数据画像 → 图表方案 → 写脚本 → Python 沙箱 → TikZ 编译 → finish），并采集
轮数 / 时长 / 成功率 / 启发式质量分，供不同版本提示词与 Agent 逻辑对比。

| 脚本 | 作用 |
| --- | --- |
| `gen_data.py` | 固定种子生成 5 个科研场景数据集（含异常/边界/缺失值） |
| `run_plotgen.py <tag>` | 跑分：上传数据 → 驱动 Agent → 写 `results/<tag>/` 指标 |
| `analyze_trace.py <tag>` | trace.jsonl 时间线分析：工具调用 / 失败点 / LLM 决策 |
| `visual_review.py <tag>` | LLM 视觉审查：TikZ PDF 渲染图 → 分维度评分（0-10） |
| `compare.py [tagA tagB]` | 汇总对比 / 双 tag 对照组回归 + 通用性门禁 |
| `smoke_test.py` | LLM 端点 + Function Calling 冒烟测试 |

场景（`data/` 下，固定随机种子可复现）：

| key | 数据 | 说明 |
| --- | --- | --- |
| `s1_time_series` | 3 组 × 180 天每日观测 | 时间序列趋势对比 |
| `s2_correlation` | 12 个环境变量 × 200 样本 | 两两相关热力图 |
| `s3_distribution` | 4 剂量组 × 40 样本 | 分布 / 偏态 / 离群值 |
| `s4_category` | 6 类 × 4 地区销售额 | 分组柱状图，含零/负值/大额异常 |
| `s5_dose_response` | 3 化合物 × 8 剂量 × 3 重复 | 半对数剂量-反应曲线（**holdout**） |

`s5` 是 holdout：日常调优默认不运行，只用于最终回归，防止 Agent 对该领域背答案。

启发式质量分（0-100）：矢量产物 25 + 执行成功 15 + TikZ 成功 15 + 轮数 10 +
无警告 10 + PNG 预览 10 + summary 5 + 未截断 10。

## 2. 最佳实践与既有机制

以下机制已实现于生产代码/提示词中，属于"能力与协议"，不依赖具体数据集：

- **图内文字 ASCII**：默认图内文字用英文 ASCII（单位转写 `ug/mL`、`uM`、
  `degC`、`x`、`+-`），避免 `Missing character` / U+FFFD 破坏 TikZ 编译。
  用户明确要求非 ASCII（如中文）时允许，后端自动切 xelatex，缺失字形回退 ASCII。
- **数据画像冗余提示**：`unique(x) × unique(cat) ≈ rows` 的"单值单元格"组合
  禁止 mean/sd 聚合与误差棒（`aggregation=none`、`error_bar=none`），推荐层
  （LLM + 确定性规则）同步注入。
- **脚本静态校验**：`write_file` 对 `.py` 做 `ast.parse` 语法检查，语法错误
  在写入阶段直接拒绝（带行列号），不落盘、不发子进程。
- **TikZ 编译前预检**：内联 `y error=`、`+- (nan/inf)` 误差增量、线性轴
  |坐标|>1e4 命中即跳过编译直接回传修正建议。非 ASCII 仅警告。latexmk 日志
  过滤 `nullfont` 类 `Missing character` 噪音。`!` 行错误全部上浮。
- **受控 TikZ 生成（tikzgen）**：脚本只写 `artifacts/data_*.csv` +
  `tikz_spec.json`，后端 Pydantic 校验后确定性渲染 `figure.tex`，替代
  matplot2tikz。手写 PGFPlots 仅作回退，且必须 `col sep=comma`、按列名引用
  CSV、小/相对坐标、裸 `\end{tikzpicture}`。
- **渲染前 CSV 保真校验**：误差列空值/非数值、对数轴非正坐标、柱状图缺索引/
  重复索引/空 y/类别顺序错位、负值柱 `ymin` 注入、单边数据发散色图回退
  viridis、热力图整数索引与 n×n 维度——把"编译成功但图是错的"提前到生成期。
- **Agent 循环保护**：
  - 失败预算：`python_sandbox` / `compile_tikz` 各连续失败 3 次注入换思路提示。
  - 验证状态机：`compile_tikz` 成功后无变更的 `read_file`/`list_files`/
    `python_sandbox`/`compile_tikz` 在执行前拦截（VERIFIED-LOOP BLOCK）。
  - stale finish：修改影响产物文件或重跑脚本后直接 `finish`，第一次执行前
    拦截（STALE FINISH BLOCKED），再次坚持允许但带警告。
  - 被拦截的调用不更新 execution/tikz 状态、不计入失败预算与相同失败检测。
    影响产物文件的判定覆盖 `scripts/` 与 `artifacts/` 下 `.tex/.csv/.json`
    （路径归一化后匹配，支持手写回退的自定义文件名）。
  - 首轮只输出文字无工具调用时注入强约束重试一次。
  - 统计真实性：只有用户明确要求才做统计检验，数值必须来自 `stats_test`
    或脚本内的真实计算，禁止编造。
- **视觉审查**：优先渲染 `tikz_main.pdf`（pdftoppm）审查 TikZ 产物，按
  轴标签 / 图例 / 配色 / 数据一致性 / 总评打分。评审模型用环境变量切换：

  ```bash
  export LX_REVIEW_LLM_MODEL="gpt-5.6-luna"
  export LX_REVIEW_LLM_BASE_URL="https://opencode.ai/zen/go/v1"  # OpenCode Zen，已验证
  export LX_REVIEW_LLM_KEY="sk-..."
  python tools/plotgen_opt/visual_review.py <tag>
  ```

  注意：旧端点 `https://opencode.ai/v1` 缺 `/zen/go` 一级，会返回 404 网页。
  弱视觉模型会漂移和幻觉，视觉结论以强模型为准，且单轮分数只作趋势参考。

## 3. 优化历史

### 3.1 阶段总览（典型轮）

| 阶段 | 平均启发式分 | 平均轮数 | 主要机制 |
| --- | --- | --- | --- |
| baseline | 87.0 | 15.8 | 初版提示词 + matplot2tikz |
| opt1–opt3 | 96–99 | ~6–11 | ASCII 标签、TikZ 回退协议、失败预算、防验证循环、冗余提示 |
| opt4–opt6 | 100 | 4.8–5.8 | figure.tex 预检、Python AST 校验、按图表类型注入已验证 PGFPlots 模板、强制 `col sep=comma` |
| opt7 | 100 | 4.8 | 受控确定性生成器 `tikzgen.py` 替代 matplot2tikz（移除依赖） |
| opt8–opt10 | 99–100 | 6–6.4 | LLM 视觉审查、stats_test 参数校验、`agent_max_rounds` 收紧到 20 |
| opt11/opt11b | 99–100 | 5.8–7 | 渲染前 CSV 保真校验 + 误差棒渲染 bug 修复（视觉 7.4/10） |
| opt12/opt12b | 95–100 | 6.2–6.8 | 验证状态机 + stale finish 拦截 + 负值柱 ymin + 单边色图回退 + 热力图标注字号 |
| opt13 | 100 | 4–6 | 提示词与 tikz_spec 后端契约统一，减少无效 schema/脚本修正轮 |
| final12（含 holdout） | — | — | s5 100 分 / 4 轮，5/5 编译成功 |

LLM 采样随机性大（同一场景同版本轮数可差 4~15 轮），结论至少看 2~3 次运行
或中位数。单轮启发式分回退需结合编译成功与视觉趋势判断。

### 3.2 第 12 轮（2026-08-16）：验证状态机 + 渲染保真

基线 `base4`（当前代码完全重跑，非 holdout）：

| 场景 | 轮数 | 编译 | 启发式分 |
| --- | --- | --- | --- |
| s1 时间序列 | 4 | ✓ | 100 |
| s2 热力图 | 11 | ✓ | 95 |
| s3 分布 | 8 | ✓ | 100 |
| s4 柱状图 | 4 | ✓ | 100 |

平均 **98.75**。s2 在编译成功后仍 `list_files` + 两次 `read_file` 空转 3 轮。

诊断出的机制缺口：

1. 成功后的验证循环只靠提示词软约束（提示词 6b 未覆盖 `list_files`，无确定性拦截）。
2. 最后一次编译后改写脚本/spec 再直接 `finish` 无警告，交付物与源码不一致。
3. PGFPlots `ybar` 强制从 0 起轴并忽略负 `ymin`，s4 的 `-1200` 负值柱被静默裁掉。
4. 单边数据用发散色图只剩一半色阶，热力图难以区分强度。
5. 热力图显著性标注 `\tiny` 过小，12×12 网格下不可读。
6. 评测工具对评审端点返回 HTML/404 无容错，倾倒整页 HTML 且无配置提示。

本轮改动（均为通用机制，无评测数据流入生产）：

| 改动 | 文件 |
| --- | --- |
| 产物验证状态机 `verified`：成功后无变更的 read/list/rerun/compile 在执行前拦截 | `backend/modules/plot_gen/agent.py` |
| stale finish 拦截：修改影响产物文件或重跑脚本后直接 finish，第一次拦截、再次允许但带警告 | 同上 |
| 柱状图负值自动注入 `ymin = min_y * 1.1`（尊重显式 ymin） | `backend/modules/plot_gen/tikzgen.py` |
| 单边数据发散色图回退 viridis + 警告；热力图标注/刻度字号 `\scriptsize` | 同上 |
| 提示词：6b 补 `list_files`；热力图相关矩阵允许脚本内计算全矩阵；柱状图禁止丢弃/替换负值 | `backend/core/prompts.py` |
| `visual_review.py` 对 HTML/404 响应给出配置提示 | `tools/plotgen_opt/visual_review.py` |

对照轮 `opt12b`：

| 场景 | base4 轮数/分 | opt12b 轮数/分 |
| --- | --- | --- |
| s1 时间序列 | 4 / 100 | 4 / 100 |
| s2 热力图 | 11 / 95 | 9 / 100 |
| s3 分布 | 8 / 100 | 10 / 100 |
| s4 柱状图 | 4 / 100 | 4 / 100 |

平均 **98.75 → 100**，4/4 编译成功、0 warning。`compare.py base4 opt12b`
对照组回归 PASS。holdout `final12`：s5 100 分 / 4 轮，5/5 执行与 TikZ 编译成功。
s3 单轮 85 分（14 轮）为 LLM 采样波动，失败预算正常触发。

强视觉模型复核（`gpt-5.6-luna`，端点 `https://opencode.ai/zen/go/v1`）：

| 场景 | base4 | opt12b | final12 |
| --- | --- | --- | --- |
| s1 时间序列 | 8 | 8 | 8 |
| s2 热力图 | 4 | 5 | 3 |
| s3 分布 | 7 | 7 | 7 |
| s4 柱状图 | 7 | 6 | 6 |
| s5 剂量曲线（holdout） | — | — | 7 |
| 平均 | 6.5 | 6.5 | 6.2 |

默认视觉容差 1.0 下 `compare.py base4 opt12b` PASS（s2 +1、s1/s3 持平、s4 -1）。

经验教训：

- 提示词里的"成功后不要再验证"必须配 `verified` 状态机，在执行前拦截才能真正省轮次。
- 只拦"修改后不编译"不够，还要拦"修改后 finish"——交付物必须对应最后一次编译的源码快照。
- PGFPlots `ybar` 会忽略负 `ymin`，"编译成功"不等于"图是对的"。
- 视觉评审必须用强模型，并检查评审端点配置。工具自身缺陷同样会污染评测结论。

### 3.3 第 13 轮（2026-08-18）：提示词契约与确定性 TikZ schema 统一

#### 背景

项目完成提示词统一结构和 Agent 阶段拆分后，需要重新跑完整 plot_gen 闭环，不能直接沿用重构前的评测结论。当前基线四个默认场景均能完成，但热力图和箱线图的 trace 暴露出提示词示例与 `tikzgen.py` 的真实 Pydantic 契约不一致，分别造成多轮无效修正。

#### 基线/诊断出的机制缺口

- 热力图示例使用 `corr` 列名，而后端要求矩阵 CSV 使用 `value`。`cell_text` 只写成省略号，未说明对象字段。
- 箱线图说明只要求 `y` 列，但 `SeriesSpec` 的 `x` 字段是必填，导致 Agent 先生成无法校验的 spec。一次随机修正还产生了统计数据长度错误。

#### 本轮改动

| 改动 | 文件 | 为什么通用 |
| --- | --- | --- |
| 箱线图模板和 schema 明确每个系列包含整数 `index` x 列 | `backend/core/prompts.py` | 与所有 `SeriesSpec` 的必填字段保持一致，避免模型生成不可校验 spec |
| 热力图模板统一使用 `value` 列 | `backend/core/prompts.py` | 与确定性渲染器的真实数据契约一致，避免 schema/CSV 列名漂移 |
| 给出 `cell_text` 对象数组的完整最小示例 | `backend/core/prompts.py` | 让模型能直接生成 Pydantic 可校验结构，减少格式试错 |
| 将 `opt13` 纳入对照工具 tag 列表 | `tools/plotgen_opt/compare.py` | 让后续汇总和回归工具自动识别本轮结果 |

#### 结果

当前重构后的基线（四个默认场景）均为 100 分，但 s2/s3 分别为 8/8 轮。修正后 `opt13` 为：

| 场景 | 轮数 | 编译 | 需求/启发式分 |
| --- | --- | --- | --- |
| s1 时间序列 | 4 | ✓ | 100 |
| s2 热力图 | 6 | ✓ | 100 |
| s3 分布 | 4 | ✓ | 100 |
| s4 柱状图 | 4 | ✓ | 100 |

holdout `final13_s5`：6 轮、100 分、执行与 TikZ 编译成功。全量 pytest：**1678 passed, 5 skipped**。提示词体积门禁和完整通用性门禁均 PASS。

视觉审查已补跑成功（`gpt-5.6-luna`）：默认场景平均 **6.2/10**，其中
s1=8、s2=4、s3=7、s4=6。holdout `s5` 为 **8.0/10**。
主要视觉风险集中在 s2 热力图的数据/渲染保真度、s4 负值柱显示，以及 s5 的
IC50/点标记缺失。这些属于后续视觉质量轮次的候选问题，不能仅靠提示词 schema
契约修正推断已解决。

#### 经验教训

- 提示词重构后必须重新核对所有确定性后端 schema。“语义上正确”的示例仍可能在字段级别不可校验。
- 评测 trace 中的 schema validation 失败应优先回溯生产提示词与 Pydantic 模型，而不是继续增加 Agent 失败预算或场景专项规则。
- 视觉审查的认证失败应单独记录为评测基础设施问题。恢复有效评审端点后，
  应优先核查低分场景的图像与数据保真度，而不是只看启发式编译分。

#### 链接

- 评测 tag：`baseline_current`（重构后基线，因超时未生成总汇总）、`opt13`、`final13_s5`
- 视觉审查结果：`tools/plotgen_opt/results/opt13/visual_reviews.json`
  与 `tools/plotgen_opt/results/final13_s5/visual_reviews.json`

## 4. 通用性门禁与闭环纪律

与 template_gen 共用 `tools/generality_gate.py`，plot_gen 优化同样必须遵守：

- 生产提示词只放通用能力/策略/协议，禁止硬编码具体学校、模板、字体名、
  OS 路径、评测场景内容。环境能力一律运行时探测或按缺失资源协议处理。
- 评测场景与基准只存在于 `tools/`，禁止被 `backend/` 运行时引用。
- 每次优化先问"特定实例还是系统性能力缺口"，机制优先。
- 日常调优默认排除 holdout（`s5`），只用于最终回归。
- 每轮结束跑 `generality_gate.py check` + `compare.py` 对照组回归，并按
  本节格式追加复盘（只追加、不覆盖历史轮次）。

## 5. 已知边界与遗留项

- **LLM 随机性**：同一版本多次运行轮数差异可达 4~15 轮。结论取多次运行中位数。
- **提示词模板转义**：`get_prompt` 使用 `str.format()`，提示词内字面花括号必须
  写 `{{ }}`，改完必须跑测试。
- **安全边界**：`python_sandbox` 是 Python 层审计钩子防护，非操作系统级沙箱。
  不得放宽 `sandbox_guard.py` 的拦截规则。
- **产物一致性**：手写 PGFPlots 与 matplotlib 产物可能随时间分叉，微调会话需
  同时重新生成两者。
- **评审端点**：本机环境变量应配置为 `https://opencode.ai/zen/go/v1`
  （旧值 `https://opencode.ai/v1` 已失效）。
- **视觉遗留（强模型反馈）**：
  - s2 热力图：含正负相关的数据应固定用 0 中点发散色图（如 RdBu_r），
    显著性标注两段式（相关系数 + 星号）避免重叠，变量标签可缩写。
  - s4 柱状图：240000 异常值把其他柱压扁、负值柱视觉上几乎不可见，
    根治需截断轴 + 数值标注或对数/符号变换（设计决策）。
  - s3 箱线图：缺图例与单位说明（颜色与 x 轴组名重复）。
  - s1 折线图：图例遮挡数据。
  - s5（holdout）：IC50 未标注、误差棒一致性、x 轴范围过宽。

## 6. 接续优化工作流

```bash
# 1. 冒烟 + 基线（默认排除 holdout）
python tools/plotgen_opt/smoke_test.py
python tools/plotgen_opt/run_plotgen.py baseN
# 2. 视觉审查（强模型）
export LX_REVIEW_LLM_MODEL="gpt-5.6-luna"
export LX_REVIEW_LLM_BASE_URL="https://opencode.ai/zen/go/v1"
python tools/plotgen_opt/visual_review.py baseN
# 3. 分析 trace，诊断机制缺口
python tools/plotgen_opt/analyze_trace.py baseN
# 4. 实施通用机制修复（禁止评测数据流入生产）
# 5. 对照轮 + 门禁 + holdout 回归
python tools/plotgen_opt/run_plotgen.py optN
python tools/plotgen_opt/visual_review.py optN
python tools/plotgen_opt/compare.py baseN optN
python tools/generality_gate.py check
python tools/plotgen_opt/run_plotgen.py finalN --include-holdout
# 6. 按本文 §3 格式追加复盘，并同步顶部章节
```

结果存放：`tools/plotgen_opt/results/<tag>/<scenario>/`
（`trace.jsonl` + `events.json` + `summary.json`）。沙箱在
`data/plots/<session_id>/`。`results/`、`previews/` 由目录内 `.gitignore` 忽略，
不提交。
