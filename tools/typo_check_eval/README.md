# 错字查找人工评估工具

用途：用 `tests/fixtures/typo_check/` 语料评估错字查找效果。这是**人工观察**的评估流程——语料中预埋了期望发现的错字（含干净的对照段落），跑完真实检查管线后，报告把原文与修正后文本、修改明细和期望命中标记并列展示，由人判断查错质量。**不是** pytest 自动测试，不会在 `pytest tests/` 中运行。

与 OCR 评估工具（formula_ocr_eval / table_ocr_eval）的区别：错字检查直接作用于 `.tex` 文本，输入无需渲染成图片，因此本工具没有 `render_corpus.py`，语料本身就是评估输入。

## 文件清单

这个功能涉及三块内容：

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `tests/fixtures/typo_check/` | 评估语料 | 错字清单 + 每条一个 LaTeX 段落片段（版本化，可提交） |
| `tools/typo_check_eval/` | 评估工具 | 加载语料、跑错字检查、生成报告（维护者脚本） |
| `logs/typo_check_eval/` | 运行输出 | HTML 报告 + JSON 结果（被 `.gitignore` 忽略，不提交） |

### 1. 语料 `tests/fixtures/typo_check/`

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 语料清单：难度分层、标题、期望错字（original → corrected）、是否含错字 |
| `typo_01.tex` … | 每条一个 LaTeX 段落片段（**不含空行，恰好一个段落**），按难度递增排序 |

难度分层（1 简单 → 5 压力测试）：

| 层 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 1 简单错字 | 英文常见拼写错误、中文音近错别字、标点与空格 | recieve / 按装、逗号后缺空格 |
| 2 中等错字 | 冠词 a/an、主谓一致、its/it's、affect/effect、中文的/地/得、重复词 | a apple、The the |
| 3 复杂错字 | 数学/引用/命令附近的错字、中英混排、时态语态 | `$x^2$` 旁的 cna、`\textbf{proposed}` 后的 definately |
| 4 高难度错字 | 中文成语错别字、近义词语境辨析、长段落多重错字、专业术语拼写 | 再接再励、stationary/stationery |
| 5 压力测试 | 密集 LaTeX 环境、命令密集段落、歧义语境、超长混合段落；并含**干净段落**用于误报检测 | equation 环境内嵌错字、无错字段落 |

语料中 `has_typo: false` 的条目是干净段落，期望输出为空 `changes`——用于评估误报率，人工重点观察模型是否"无中生有"。

新增语料时：添加 `typo_NN.tex` 片段（单段落、无空行），并在 `manifest.json` 的 `items` 列表追加对应条目（含期望错字与 `has_typo`）即可。

### 2. 评估工具 `tools/typo_check_eval/`

| 文件 | 作用 |
| --- | --- |
| `core.py` | 共享逻辑：加载语料、在工作区打开语料目录、marker 还原、期望错字命中判定 |
| `run_eval.py` | 跑完整评估：逐条调用 `TypoCheckService.check_typos()`，生成 HTML 报告 |
| `README.md` | 本文档 |

### 3. 运行输出 `logs/typo_check_eval/`

```
logs/typo_check_eval/
├── report.html     # 人工评估报告（浏览器打开，原文/修正后对照 + 修改明细）
└── results.json    # 机器可读结果（每条的状态、修正后 LaTeX、changes、命中/漏检/误报标记）
```

`results.json` 中每条记录的 `status`：

| 状态 | 含义 |
| --- | --- |
| `issues` | 检查成功且发现修改（changes 非空或修正后文本与原文不同） |
| `clean` | 检查成功且未发现修改 |
| `error` | 检查过程抛出异常（文件/工作区问题等） |

每条记录还包含 `matched` / `missed` / `false_positive_changes`（期望错字命中、漏检与误报修改条目的索引）和 `corrected_latex`（marker 已还原的可读 LaTeX）。

注意：`check_typos()` 在 LLM 校验重试耗尽等内部失败时会**静默回退为原文**（status 仍为 `clean`），因此"含错字语料被记为 clean"既可能是漏检，也可能是内部失败；报告卡片会对这种情况标黄提示，人工评估时需结合日志区分。

## 命中标记规则

- 命中：某条 `changes[].original` 与 manifest 中期望错字原文归一化后**互相包含**（宽松子串匹配），或二者共享至少一个显著词元（英文单词长度 ≥ 4，覆盖 change 引用更宽上下文的情况，如期望 `affect on`、change 引用 `has little affect`）
- 漏检：期望错字未出现在任何 change 中
- 误报：change 的 original 不包含任何期望错字原文；干净语料（`has_typo: false`）的任何非空 changes 全部计入误报

这些标记只用于辅助人工观察，不是自动评分；最终结论以人工判断为准。

## 流程

1. `run_eval.py` 在 `tests/fixtures/typo_check/` 目录上打开评估工作区
2. 逐条调用后端 `TypoCheckService.check_typos()`（进程内复用真实管线，含 LLM 调用与 marker 保护）
3. 把修正文本中的 marker 还原为可读 LaTeX
4. 生成 `report.html`，按难度分层展示原文、修正后文本、修改明细与期望命中标记

## 用法

```bash
# 跑完整评估（逐条调用 LLM）
python tools/typo_check_eval/run_eval.py

# 快速冒烟：只跑前 5 条
python tools/typo_check_eval/run_eval.py --limit 5

# 只跑某一条
python tools/typo_check_eval/run_eval.py --only typo_01
```

## 依赖

- `config.json` 中已配置 `llm.api_key`（走真实 LLM 调用，产生 API 费用）
- 每条语料 1 次 LLM 调用（逐段检查）；无需本机 TeX Live（检查基于文本，不涉及编译/渲染）
