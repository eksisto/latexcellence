# template_gen 优化闭环工具（tools/templategen_opt/）

本目录复现并驱动 `template_gen` Agent 的「生成 → 评估 → 诊断 → 优化 → 再生成」
闭环，用于**离线评测与提示词迭代**，是开发调试工具，不参与生产运行
（与 `tools/plotgen_opt/` 同模式）。

它直接调用 `backend/modules/template_gen/service.py` 的 `TemplateGenService`，
在后台对一组预置的「用户口吻」自然语言场景驱动 Agent 生成 LaTeX 工程，并自动采集
可量化指标，供对比不同版本提示词 / Agent 逻辑的质量差异。

> 注意：本目录的脚本、场景定义与参考资料纳入版本库管理；运行产物
> 由目录内 `.gitignore` 忽略，不会提交。

---

## 目录结构

```
tools/templategen_opt/
├── run_templategen.py       # 主运行器：驱动 Agent 生成 + 事件采集 + 需求遵守检查 + 启发式评分
├── analyze_trace.py         # trace.jsonl 分析：工具分布 / 失败工具 / 编译轮次 / 可读时间线
├── visual_review.py         # LLM 视觉审查：PDF 渲染成图 → vision 模型分维度评分（0-10）
├── compare.py               # 两个 tag 的横向对比（启发式分 + 视觉分）
├── descriptions.py          # 评测场景定义：用户口吻描述 + 硬性要求检查表
├── reference_benchmarks.md  # 工业级模板基准特征提取（提示词工程输入）
├── results/<tag>/           # 每次运行的结果根目录（<tag> 即迭代标识，如 base / opt1）
└── results/<tag>/visual_reviews.json  # 该 tag 下全部场景的视觉审查汇总
```

## 评测场景（descriptions.py）

`SCENARIOS` 定义了 4 个场景，每个都埋入了可客观核验的硬性要求
（边距数值、字体、主题色、文件结构、语义命令），用于检验 Agent 是否
「遵循用户输入要求」：

| key | 场景 | 主要硬性要求 |
| --- | --- | --- |
| `thesis` | 通用硕士学位论文 | A4 + 2.5/3cm 边距、宋体黑体、1.5 倍行距、booktabs、XeLaTeX、front/chapters 拆分等 10 条 |
| `beamer` | 现代学术幻灯片 | 扁平化 metropolis 风格、#00539F/#F5A623 配色、进度条、独立 color theme 等 10 条 |
| `resume` | 单页求职简历 | 紧凑边距、无衬线字体、fontawesome 图标、语义命令、单页（PDF ≤2 页）等 10 条 |
| `requirements` | 需求遵守压力测试 | Letter + 1in、双栏、10pt、共享编号定理、natbib author-year、#1F4E79 等 10 条 |

`CATEGORY_LABELS` 提供各场景的中文名；`REQUIREMENT_PATTERNS` 是运行器实际
使用的正则检查表（`run_templategen.py::_requirement_adherence`），
`REQUIREMENT_CHECKS` 由其标签自动派生，供人工复核参考。

## 环境要求

- 项目根目录存在有效 `config.json`（脚本内部 `load_config("config.json")`），
  LLM 端点可访问（`backend/core/llm_client.py`）。
- 具备 TeX Live（`latexmk` / `xelatex`）以便 Agent 编译验证；`pdfinfo`、
  `pdftoppm`（均随 TeX Live 提供）用于页数统计与视觉审查截图。
- Python 依赖同后端（pydantic v2、openai 客户端等），建议在项目 `.venv` 下运行。

## 快速开始

```bash
# 0) （可选）确认单场景跑通，观察进度
python tools/templategen_opt/run_templategen.py smoke beamer

# 1. 运行 Agent 生成并采集指标（tag 用于区分基线/迭代，如 base / opt1 / opt3）
python tools/templategen_opt/run_templategen.py opt3 thesis,beamer,resume,requirements

# 2. 分析 trace，定位质量缺失发生在哪个环节
python tools/templategen_opt/analyze_trace.py opt3

# 3. LLM 视觉审查（pdftoppm 渲染 PDF 前几页 → vision 模型分维度评分 0-10）
python tools/templategen_opt/visual_review.py opt3

# 4. 对比两个 tag（启发式分 + 视觉分）
python tools/templategen_opt/compare.py base opt3
```

各命令的完整用法见下文。

---

## 命令详解

### 1. `run_templategen.py <tag> [scenario1,scenario2,...]`

核心运行器。参数：

- `<tag>`（必填）：结果目录名，用于区分基线 / 迭代，如 `base`、`opt1`、`opt3`。
- `scenario1,...`（可选）：逗号分隔的场景 key，省略时运行全部场景。

对每个场景执行：

1. 调用 `TemplateGenService.generate(description, "zh", llm=llm)`，逐条消费 SSE 事件
   （`events.json` 中每条带 `ts_ms` 相对时间戳）；
2. 单场景 **1800 秒超时保护**，超时或异常会写入一条 `error` 事件并继续下一场景；
3. 从 Agent 沙箱拷贝 `trace.jsonl` 副本，并**复制整个沙箱快照**（自包含，不受
   cleanup 影响，便于事后回看产物）；
4. 计算指标：轮数、编译是否成功、警告数、是否截断、文件清单、PDF 页数；
5. 在沙箱快照上做**需求遵守启发式检查**（关键词级检索硬性要求是否落地）；
6. 汇总启发式质量评分（0-100）。

输出（每个场景）：

```
results/<tag>/<scenario>/
├── events.json    # 全部 SSE 事件（含 ts_ms 时间戳）
├── trace.jsonl    # Agent 运行追踪日志副本
├── sandbox/       # Agent 产物快照（自包含，cleanup 后仍可回看）
└── summary.json   # 指标汇总 + req 需求检查明细 + quality 启发式评分
```

全部场景结束后，合并写入 `results/<tag>/_all.json`（已存在时合并，支持部分重跑），
并打印该 tag 的平均启发式分。

部分重跑会合并 `_all.json`，不会丢失其它场景的指标。

### 2. `analyze_trace.py <tag> [scenario1,scenario2,...]`

分析 trace 定位质量缺失环节。参数同上，`[scenario...]` 省略时分析该 tag 下全部场景。

对每个场景读取 `trace.jsonl`，统计并输出：

- 轮数、LLM 无工具调用轮数；
- 工具调用分布（各工具被调用次数）；
- 失败的工具调用（工具名 + 参数摘要 + 结果前 300 字符）；
- 编译尝试 / 成功 / 失败次数、`write_file` 重写次数；
- 每轮 LLM 文本摘要 + 工具调用序列（可读 timeline）。

结果写入 `results/<tag>/<scenario>/trace_analysis.json`。

### 3. `visual_review.py <tag> [scenario1,scenario2,...]`

LLM 视觉质量审查。对每个场景：

1. 从 `sandbox/` 中挑选主 PDF（优先 `main/resume/cv/thesis` 开头命名）；
2. 用 `pdftoppm` 渲染前若干页为 PNG（thesis/beamer/requirements 渲染前 3 页，
   resume 渲染前 2 页，100 DPI）；
3. 图片缩放到 1024px 内 → base64 → 连同场景描述发给 LLM（vision，经
   `retry_with_validation()` 容错校验重试）；
4. 按维度打分，写入 `summary.json` 的 `visual_review` 字段。

评分维度（各 0~10）：`typography`（字体层级）、`layout`（边距/版式）、
`aesthetics`（配色/视觉）、`requirement_adherence`（需求遵守）、`overall`（总评），
外加 `issues` / `suggestions` 问题与建议列表。

汇总写入 `results/<tag>/visual_reviews.json`，并打印平均 `overall`。

> 这是质量验收的主要依据——启发式检查只验证「关键词是否存在」，视觉审查验证
> 「渲染出来好不好看、是否符合需求」。

### 4. `compare.py <tagA> <tagB>`

对比两个 tag 的评测结果：

- 逐场景对比启发式分与需求遵守率（`req_ratio`）；
- 汇总两个 tag 的平均启发式分（0-100）与平均视觉总评（0-10，依赖
  `visual_reviews.json`，未运行视觉审查则显示为空）。

---

## 评分体系

### 启发式质量分（`summary.json → quality.score`，0-100）

| 项 | 满分 | 判定 |
| --- | --- | --- |
| compile_success | 20 | 最终编译成功 |
| requirements | 40 | 需求遵守率 × 40（关键词级检查，误判率偏高） |
| custom_style | 10 | 存在 `.sty` / `.cls` 自定义样式 |
| readme | 5 | 存在 README |
| multi_file | 5 | `.tex` 文件数 ≥ 3 |
| bib | 5 | 存在 `.bib` 文件 |
| no_warnings | 5 | 事件流中无 warning |
| not_truncated | 5 | 未因超时 / 异常截断 |
| summary | 5 | 有运行总结 |

各细分项见 `quality.detail`。

### 需求遵守检查（`summary.json → req`）

- `passed` / `total` / `ratio`：通过条数 / 总数 / 比例；
- `detail.<检查项>`：`matched` 是否命中、`patterns` 正则表、`hits` 命中的具体模式。

各场景检查表在 `run_templategen.py::_requirement_adherence` 中定义。**这是关键词级
启发式检查，false negative 常见**（例如换一种写法未命中正则），需结合沙箱产物与
视觉审查人工复核。

### 视觉审查（`summary.json → visual_review`）

各维度 0-10，`overall` 为总评，`issues` / `suggestions` 为具体问题与改进建议。

---

## 推荐的优化闭环工作流

```
1. 跑基线          python tools/templategen_opt/run_templategen.py base
2. 视觉审查        python tools/templategen_opt/visual_review.py base
3. 分析 trace      python tools/templategen_opt/analyze_trace.py base
4. 定位缺失环节
   - 编译失败频繁  → 看 trace_analysis.json 的 compile 事件与失败工具
   - 需求不遵守    → 看 summary.json 的 req.detail，逐条核对漏项
   - 视觉差        → 看 visual_reviews.json 的 issues
5. 修改提示词/逻辑  backend/core/prompts.py 或 backend/modules/template_gen/
6. 重跑对比        python tools/templategen_opt/run_templategen.py optN ... && \
                   python tools/templategen_opt/visual_review.py optN && \
                   python tools/templategen_opt/compare.py base optN
7. 达标后         将获胜的提示词固化进 backend/core/prompts.py
```

## 提示词工程参考

`reference_benchmarks.md` 汇总了优秀主题的宏包选择、自定义命令体系、布局
与视觉美学特征，是 `backend/core/prompts.py` 中 `template_gen.agent_system` 的
需求映射清单、分类玩法、字体安全与质量门禁的设计来源。修改提示词前建议先读它。

## 常见问题

- **单场景超时**：runner 内置 1800s 保护，超时后继续下一场景；对卡住的场景单独
  重跑 `python tools/templategen_opt/run_templategen.py <tag> <scenario>` 便于观察。
- **`pdfinfo`/`pdftoppm` 不可用**：页数统计与视觉审查会跳过，其余指标不受影响；
  请确保 TeX Live 已安装且在 PATH 中。
- **LLM 端点异常**：先确认 `config.json` 的 `llm` 配置与网络；可用
  `backend` 侧冒烟脚本（参见 `tools/plotgen_opt/smoke_test.py`）验证端点连通性。
- **视觉审查为离线评估**：不参与 `run_templategen.py` 的启发式评分，两者互补使用。
