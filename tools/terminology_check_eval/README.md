# 术语一致性人工评估工具

用途：用 `tests/fixtures/terminology_check/` 语料评估术语一致性检查效果。这是**人工观察**的评估流程——语料中预埋了期望发现的术语不一致簇（含术语一致的干净对照文档），跑完真实检查管线后，报告把每段的原文与修正后文本、修改明细和期望术语簇命中标记并列展示，由人判断术语统一质量。**不是** pytest 自动测试，不会在 `pytest tests/` 中运行。

与 OCR 评估工具（formula_ocr_eval / table_ocr_eval）的区别：术语检查直接作用于 `.tex` 文本，输入无需渲染成图片，因此本工具没有 `render_corpus.py`，语料本身就是评估输入。

## 与 typo/expression 评估工具的关键区别

术语一致性是**跨段落**现象：同一概念在不同段落以不同写法出现（大小写、缩写、中英混用、连字符变体、近义表述），需要全文语义聚类才能发现。因此：

- 每条语料是一个**多段落** LaTeX 文档（段落间以空行分隔），而不是单段落片段
- 期望标注是**术语簇**（`recommended` + `variants`），而不是单条 `original → corrected`
- 每条语料调用 LLM 的次数为 **N+1**（N 段术语提取 + 1 次全文语义聚类），成本高于逐段检查

## 文件清单

这个功能涉及三块内容：

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `tests/fixtures/terminology_check/` | 评估语料 | 术语簇清单 + 每条一个多段落 LaTeX 文档（版本化，可提交） |
| `tools/terminology_check_eval/` | 评估工具 | 加载语料、跑术语检查、生成报告（维护者脚本） |
| `logs/terminology_check_eval/` | 运行输出 | HTML 报告 + JSON 结果（被 `.gitignore` 忽略，不提交） |

### 1. 语料 `tests/fixtures/terminology_check/`

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 语料清单：难度分层、标题、期望术语簇（recommended + variants）、是否含不一致 |
| `term_01.tex` … | 每条一个多段落 LaTeX 文档（**段落间以空行分隔**），按难度递增排序 |

难度分层（1 简单 → 5 压力测试）：

| 层 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 1 大小写与格式混用 | 大小写不一致、专有名词大小写 | Deep Learning / deep learning、BERT / Bert / bert |
| 2 缩写与全称 | 全称与缩写混用、中文全称与英文缩写 | Convolutional Neural Network / CNN、图神经网络 / GNN |
| 3 中英混用 | 同一概念中英文混用、三变体混合 | 深度学习 / deep learning / DL、过拟合 / overfitting |
| 4 拼写变体与近义统一 | 连字符变体、写法变体 + 缩写、中文近义表述 | pre-training / pretraining、state-of-the-art / SOTA、特征表示 / 表征 |
| 5 压力测试 | 多簇混合文档、命令与公式密集段落、中文长文档多簇、超长中英混合；并含**术语一致的干净文档**用于误报检测 | 三簇同文档、`$O(n^2)$` 与 `\ref{}` 旁的不一致、干净文档 |

语料中 `has_inconsistency: false` 的条目是术语一致的干净文档，期望输出为空 `changes`——用于评估误报率（过度聚类/无谓替换）。干净文档中特意放置**相似但不同义**的术语（如 准确率/精确率/召回率、PointNet/DGCNN），考察模型是否会错误合并不同概念。

语料不变量（`run_eval.py --validate` 自动校验，不通过会阻止评估）：

- 每条文档至少 **2 个段落**（术语一致性依赖跨段落聚类）
- 期望变体必须出现在**剥离 marker 后的纯文本**中——藏在命令参数或公式内部会被 marker 保护吞掉，LLM 根本看不到，不能作为期望
- 期望簇的 `recommended` 必须是 `variants` 之一（服务侧聚类契约）
- `has_inconsistency: false` 的条目不得声明 `expected_clusters`

新增语料时：添加 `term_NN.tex` 文档（多段落、空行分隔），在 `manifest.json` 的 `items` 列表追加对应条目（含期望术语簇与 `has_inconsistency`），并运行 `--validate` 校验即可。

### 2. 评估工具 `tools/terminology_check_eval/`

| 文件 | 作用 |
| --- | --- |
| `core.py` | 工具专属逻辑：期望术语簇命中判定与语料校验（通用部分复用 `tools/eval_common.py`） |
| `run_eval.py` | 跑完整评估：逐条调用 `TerminologyCheckService.check_terminology()`，生成 HTML 报告（CLI 骨架复用 `tools/eval_common.py`） |
| `README.md` | 本文档 |

### 3. 运行输出 `logs/terminology_check_eval/`

```
logs/terminology_check_eval/
├── report.html     # 人工评估报告（浏览器打开，逐段原文/修正后对照 + 修改明细 + 期望术语簇）
└── results.json    # 机器可读结果（每条的状态、修正后 LaTeX、changes、命中/漏检/误报标记）
```

`results.json` 中每条记录的 `status`：

| 状态 | 含义 |
| --- | --- |
| `issues` | 检查成功且发现修改（changes 非空或任一段修正后文本与原文不同） |
| `clean` | 检查成功且未发现修改 |
| `error` | 检查过程抛出异常（文件/工作区问题等） |

每条记录还包含 `matched` / `missed` / `false_positive_changes`（**展开后**的期望变体替换索引：每簇中每个非推荐变体计一条期望）、`cluster_stats`（每簇的 `matched_variants` / `missed_variants` 与 `status`：hit 全部命中 / partial 部分命中 / missed 全漏）和 `paragraphs`（逐段的原文、marker 还原后的修正 LaTeX、提取到的术语与 changes）。

注意：`check_terminology()` 在 LLM 提取或聚类失败时（含无 API Key 的规则降级）会**静默回退为空 changes**（status 仍为 `clean`），因此"含不一致语料被记为 clean"既可能是漏检，也可能是内部失败；报告卡片会对这种情况标黄提示，人工评估时需结合日志区分。

## 命中标记规则

- 期望展开：每个簇中，每个**非推荐变体**对应一条期望 `{original: 变体, corrected: 推荐项}`（大小写变体按原始字符串区分，如 `Deep Learning` 仍是期望变体）
- 命中：某条 `changes[].original` 与期望变体归一化后**互相包含**或共享显著词元（英文单词长度 ≥ 4），且 `changes[].corrected` 与簇推荐项归一化后**互相包含**或共享显著词元——大小写方向的统一（如统一到 `Deep Learning` 还是 `deep learning`）都算命中，方向优劣由人工判断；缩写与全称的推荐方向不匹配时不计命中
- 漏检：期望变体替换未出现在任何 change 中
- 误报：change 的 original/corrected 不匹配任何期望变体替换；干净语料（`has_inconsistency: false`）的任何非空 changes 全部计入误报

这些标记只用于辅助人工观察，不是自动评分；最终结论以人工判断为准。

## 流程

1. `run_eval.py` 先做语料校验（多段落 / 变体可见性 / 簇契约），通过后在 `tests/fixtures/terminology_check/` 目录上打开评估工作区
2. 逐条调用后端 `TerminologyCheckService.check_terminology()`（进程内复用真实管线，含 LLM 术语提取 + 语义聚类与 marker 保护）
3. 把每段修正文本中的 marker 还原为可读 LaTeX
4. 生成 `report.html`，按难度分层展示每段原文、修正后文本、修改明细与期望术语簇命中标记

## 用法

```bash
# 只校验语料（不调用 LLM，新增语料后必跑）
python tools/terminology_check_eval/run_eval.py --validate

# 跑完整评估（逐条调用 LLM，每条 N+1 次调用）
python tools/terminology_check_eval/run_eval.py

# 快速冒烟：只跑前 5 条
python tools/terminology_check_eval/run_eval.py --limit 5

# 只跑某一条
python tools/terminology_check_eval/run_eval.py --only term_01
```

## 依赖

- `config.json` 中已配置 `llm.api_key`（走真实 LLM 调用，产生 API 费用；无 Key 时规则降级不产生修改建议，所有条目记为 `clean`）
- 每条语料 N+1 次 LLM 调用（N 段术语提取 + 1 次全文聚类）；无需本机 TeX Live（检查基于文本，不涉及编译/渲染）
