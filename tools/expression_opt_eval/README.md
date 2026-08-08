# 表达优化人工评估工具

用途：用 `tests/fixtures/expression_opt/` 语料评估表达优化效果。这是**人工观察**的评估流程——语料中预埋了期望达成的优化（含已精炼的对照段落），跑完真实优化管线后，报告把原文与优化后文本、修改明细和期望命中标记并列展示，由人判断优化质量。**不是** pytest 自动测试，不会在 `pytest tests/` 中运行。

与 OCR 评估工具（formula_ocr_eval / table_ocr_eval）的区别：表达优化直接作用于 `.tex` 文本，输入无需渲染成图片，因此本工具没有 `render_corpus.py`，语料本身就是评估输入。

## 文件清单

这个功能涉及三块内容：

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `tests/fixtures/expression_opt/` | 评估语料 | 期望优化清单 + 每条一个 LaTeX 段落片段（版本化，可提交） |
| `tools/expression_opt_eval/` | 评估工具 | 加载语料、跑表达优化、生成报告（维护者脚本） |
| `logs/expression_opt_eval/` | 运行输出 | HTML 报告 + JSON 结果（被 `.gitignore` 忽略，不提交） |

### 1. 语料 `tests/fixtures/expression_opt/`

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 语料清单：难度分层、标题、期望优化（original → optimized）、是否含优化 |
| `expr_01.tex` … | 每条一个 LaTeX 段落片段（**不含空行，恰好一个段落**），按难度递增排序 |

难度分层（1 简单 → 5 压力测试）：

| 层 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 1 简单冗余 | 赘余短语、冗余时间/数量表述、弱动词 | in order to、due to the fact that、in the month of June |
| 2 名词化与弱动词 | 名词化动词、there be 句式、It 引导赘语、the reason is because | make a comparison、has the ability to、There are several factors that |
| 3 结构拖沓与衔接 | 论文导言拖沓句式、平行结构、命令/公式/引用附近赘语 | In this paper, we will discuss、It is not difficult to see that |
| 4 学术语体与措辞 | 口语化表达、弱修饰、模糊影响、平行结构改写、非正式副词 | gets better results、A lot of、really fast、maybe |
| 5 压力测试 | 密集 LaTeX 环境、超长混合段落、命令密集段落；并含**已精炼段落**用于误报检测 | equation 环境内嵌冗余、干净英文学术段落 |

语料中 `has_improvement: false` 的条目是已精炼的干净段落，期望输出为空 `changes`——用于评估误报率（无谓改写/过度改写），人工重点观察模型是否"画蛇添足"。

注意：期望优化的 `original` 必须是**剥离 marker 后可达的纯文本片段**（不能藏在命令参数或公式内部），否则 LLM 在 marker 保护下根本看不到它，不应作为期望。

新增语料时：添加 `expr_NN.tex` 片段（单段落、无空行），并在 `manifest.json` 的 `items` 列表追加对应条目（含期望优化与 `has_improvement`）即可。

### 2. 评估工具 `tools/expression_opt_eval/`

| 文件 | 作用 |
| --- | --- |
| `core.py` | 工具专属常量 + 共享逻辑的向后兼容导出（通用部分复用 `tools/eval_common.py`） |
| `run_eval.py` | 跑完整评估：逐条调用 `ExpressionOptService.optimize()`，生成 HTML 报告（CLI 骨架复用 `tools/eval_common.py`） |
| `README.md` | 本文档 |

### 3. 运行输出 `logs/expression_opt_eval/`

```
logs/expression_opt_eval/
├── report.html     # 人工评估报告（浏览器打开，原文/优化后对照 + 修改明细）
└── results.json    # 机器可读结果（每条的状态、优化后 LaTeX、changes、命中/漏检/误报标记）
```

`results.json` 中每条记录的 `status`：

| 状态 | 含义 |
| --- | --- |
| `issues` | 优化成功且产生修改（changes 非空或优化后文本与原文不同） |
| `clean` | 优化成功且未产生修改 |
| `error` | 优化过程抛出异常（文件/工作区问题等） |

每条记录还包含 `matched` / `missed` / `false_positive_changes`（期望优化命中、漏检与误报修改条目的索引）和 `optimized_latex`（marker 已还原的可读 LaTeX）。

注意：`optimize()` 在 LLM 校验重试耗尽等内部失败时会**静默回退为原文**（status 仍为 `clean`），因此"含优化语料被记为 clean"既可能是漏检，也可能是内部失败；报告卡片会对这种情况标黄提示，人工评估时需结合日志区分。

## 命中标记规则

- 命中：某条 `changes[].original` 与 manifest 中期望优化原文归一化后**互相包含**（宽松子串匹配），或二者共享至少一个显著词元（英文单词长度 ≥ 4，覆盖 change 引用更宽上下文的情况，如期望 `with ease`、change 引用 `applied to datasets with ease`）
- 漏检：期望优化未出现在任何 change 中
- 误报：change 的 original 不包含任何期望优化原文；干净语料（`has_improvement: false`）的任何非空 changes 全部计入误报

这些标记只用于辅助人工观察，不是自动评分；最终结论以人工判断为准。

## 流程

1. `run_eval.py` 在 `tests/fixtures/expression_opt/` 目录上打开评估工作区
2. 逐条调用后端 `ExpressionOptService.optimize()`（进程内复用真实管线，含 LLM 调用与 marker 保护）
3. 把优化文本中的 marker 还原为可读 LaTeX
4. 生成 `report.html`，按难度分层展示原文、优化后文本、修改明细与期望命中标记

## 用法

```bash
# 跑完整评估（逐条调用 LLM）
python tools/expression_opt_eval/run_eval.py

# 快速冒烟：只跑前 5 条
python tools/expression_opt_eval/run_eval.py --limit 5

# 只跑某一条
python tools/expression_opt_eval/run_eval.py --only expr_01
```

## 依赖

- `config.json` 中已配置 `llm.api_key`（走真实 LLM 调用，产生 API 费用）
- 每条语料 1 次 LLM 调用（逐段优化）；无需本机 TeX Live（优化基于文本，不涉及编译/渲染）
