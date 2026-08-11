# style_modify 优化闭环工具（tools/stylemodify_opt/）

本目录复现并驱动 `style_modify` 项目 Agent 的「生成 → 评估 → 诊断 → 优化 →
再生成」闭环，用于**离线评测与提示词/Agent 逻辑迭代**，是开发调试工具，不参与
生产运行（与 `tools/plotgen_opt/`、`tools/templategen_opt/` 同模式）。

它直接调用 `backend/modules/style_modify/service.py` 的 `StyleModifyService`：
先构造一个可复现的小型 LaTeX 项目并打开到工作区，再对每个场景驱动 Agent 走
「浏览项目 → 定位样式 → stage_replace → shadow 编译 → finish → proposal」完整
流程，并自动采集轮数 / 时长 / proposal 质量 / 需求遵守 / 内容保留 / 事务应用与
撤销等指标，供对比不同版本提示词 / Agent 逻辑。

> 注意：本目录的脚本、场景定义与报告纳入版本库管理；运行产物
> （`results/`、`runtime/`、`data/projects/`、`__pycache__/`）由目录内
> `.gitignore` 忽略，不会提交。

---

## 目录结构

```
tools/stylemodify_opt/
├── scenarios.py        # 5 个 LaTeX 样式场景 + 客观检查点（可复现）
├── smoke_test.py       # LLM 端点 + Function Calling 冒烟测试
├── run_stylemodify.py  # 主运行器：生成项目 → 驱动 Agent → 采集指标 → 质量评分
├── analyze_trace.py    # trace.jsonl 分析：工具序列 / 失败点 / 编译尝试
├── compare.py          # 多 tag 结果对比表格
├── README.md
├── data/projects/      # 运行时生成的场景项目（gitignore）
├── runtime/            # 评测会话目录（gitignore，不污染 style_modify_sessions/）
└── results/<tag>/      # 每次运行的结果根目录（gitignore）
```

## 评测场景（scenarios.py）

`SCENARIOS` 定义了 5 个代表性样式修改场景，每个项目都埋入了必须保留的正文
标记（label / 公式 / 表格数据 / frame 文本），用于检测 Agent 是否“只改样式、
不破坏内容”：

| key | 说明 | 需求要点 |
| --- | --- | --- |
| `s1_theme_color` | article + 自定义 `.sty` | 全局主题色 #333333 → #1F4E79 |
| `s2_section_format` | titlesec 章节样式 | 深蓝加粗标题 + 方块装饰 |
| `s3_table_style` | 普通表格 → booktabs | 三线表 + 表头加粗 + 数值列左对齐 |
| `s4_beamer_color` | beamer 自定义 theme | 主题色 → 深蓝，强调色 → 橙色 |
| `s5_class_options` | 自定义 `.cls` | 11pt → 12pt、A4、边距 2cm |

每个场景的 `checks` 是正则检查点（如新颜色存在、旧颜色移除、命令命中），
`preserve_markers` 是内容保留检查，`allowed_targets` 是“只允许改哪些文件”。

## 环境要求

- 项目根目录存在有效 `config.json`，LLM Key 可通过环境变量 `LX_LLM_KEY` 提供。
- 建议在项目 `.venv` 下运行：`python tools/stylemodify_opt/...`
- 具备 TeX Live（`latexmk`）时 shadow 编译指标才有意义；运行器会记录
  `compile_available`，Agent 仍可完成 proposal。运行器会自动扫描
  `/usr/local/texlive/*/bin/*/latexmk` 并加入 `PATH`（Linux）；Windows 侧则依赖
  TeX Live bin 目录已在系统 `PATH` 中。

## 快速开始

```bash
# 0. 冒烟测试：验证 LLM 端点 + Function Calling 可用
python tools/stylemodify_opt/smoke_test.py

# 1. 运行 Agent 并采集指标（tag 默认 baseline，如用 optN 区分迭代）
python tools/stylemodify_opt/run_stylemodify.py baseline

# 2. 分析 trace，定位失败 / 低效环节
python tools/stylemodify_opt/analyze_trace.py baseline

# 3. 修改提示词 / Agent 逻辑后复测
python tools/stylemodify_opt/run_stylemodify.py opt1

# 4. 汇总对比全部 tag
python tools/stylemodify_opt/compare.py
```

常用参数：

- `run_stylemodify.py <tag> [s1_theme_color,s3_table_style]`：只跑指定场景。
- `run_stylemodify.py <tag> --no-apply`：跳过 proposal 应用/撤销事务测试。

## 输出指标

每个场景输出到 `results/<tag>/<scenario>/`：

| 文件 | 内容 |
| --- | --- |
| `events.json` | 全部 SSE 事件（含相对时间戳） |
| `trace.jsonl` | Agent 运行追踪日志副本 |
| `session/` | 会话快照（`baseline/`、`overlay/`、`meta.json`、`trace.jsonl`） |
| `summary.json` | 指标汇总 + 质量分 + proposal + 检查明细 |
| `trace_analysis.json` | `analyze_trace.py` 生成的逐轮分析 |

### 启发式质量分（`quality.score`，0-100）

| 项 | 满分 | 判定 |
| --- | --- | --- |
| compile_success | 15 | 最近一次 shadow 编译状态为 passed |
| proposal_applicable | 10 | finish 产出可应用的 proposal revision |
| staged_files | 10 | proposal 至少包含 1 个已修改文件 |
| requirements | 30 | 场景检查点命中比例 |
| preservation | 15 | 正文/公式/表格标记保留比例 |
| no_warnings | 5 | SSE 无 warning |
| no_errors | 5 | SSE 无 error |
| rounds | 5 | 轮数 ≤ 10 |
| not_truncated | 5 | 未触发 max_rounds 截断 |

`summary.json` 同时记录 `req.detail`、`preservation.detail`、`allowed_targets`、
`apply_test`（proposal 应用 + 撤销事务验证）、编译尝试与失败工具明细。

## 推荐的优化闭环工作流

```
1. 冒烟测试        python tools/stylemodify_opt/smoke_test.py
2. 跑基线          python tools/stylemodify_opt/run_stylemodify.py baseline
3. 分析 trace      python tools/stylemodify_opt/analyze_trace.py baseline
4. 定位失败环节
   - 需求未命中    → 检查 proposal diff 是否改了错误文件 / 漏改样式源
   - 内容被破坏    → 检查是否整篇重写、丢 label/数据；收紧“只改样式”约束
   - 轮数爆炸      → 看 trace 的重复工具与失败根因（常见：反复 read、无效 stage）
   - 编译失败      → 看 compile 事件的错误片段与修复路径
5. 修改提示词/逻辑  backend/core/prompts.py 或 backend/modules/style_modify/
6. 重跑对比        python tools/stylemodify_opt/run_stylemodify.py optN && \
                   python tools/stylemodify_opt/analyze_trace.py optN && \
                   python tools/stylemodify_opt/compare.py
7. 达标后         将获胜的提示词固化进 backend/core/prompts.py
```

## 历史优化结论（report/）

- `report/optimization_report.md`：一次完整优化闭环的报告（基线 → opt1），
  含逐场景指标、诊断问题与量化收益。

## 常见问题

- **LLM 端点异常**：先运行 `smoke_test.py` 验证连通性，再查 `config.json` 的
  `llm` 配置与 `LX_LLM_KEY`。
- **latexmk 找不到**：先确认当前 Python/Shell 的 `PATH` 是否包含 TeX Live 的
  bin 目录。Linux 运行器会尝试自动发现 `/usr/local/texlive/*/bin/*/latexmk`；
  若自定义安装位置，可设置 `PATH="/path/to/texlive/bin:$PATH"` 后重跑。
- **LLM 随机性**：单次结果波动正常，结论至少看 2~3 次运行，或对比多 tag 均值。
