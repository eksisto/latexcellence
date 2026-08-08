# 逐段翻译人工评估工具

用途：用 `tests/fixtures/paragraph_translate/` 语料评估逐段翻译效果。这是**人工观察**的评估流程——语料中预埋了整段参考译文与关键期望短语，跑完真实翻译管线后，报告把参考译文、原文与模型译文、修改明细和期望短语命中标记并列展示，由人判断翻译质量。**不是** pytest 自动测试，不会在 `pytest tests/` 中运行。

与 OCR 评估工具（formula_ocr_eval / table_ocr_eval）的区别：逐段翻译直接作用于 `.tex` 文本，输入无需渲染成图片，因此本工具没有 `render_corpus.py`，语料本身就是评估输入。

## 与 typo/expression 评估工具的区别

翻译的评判维度和 typo/expression 不同：

- **没有"干净/含错"概念**。翻译关注关键内容是否译对、是否漏译、是否有多余改动，因此语料标注**整段参考译文**（供人工对照）与**期望短语**（original → translated，自动打命中标记）
- **输出字段是 `translated`**。服务成功路径已把 marker 还原为干净 LaTeX，回退路径为 marker 空间文本，工具统一幂等还原后展示
- 每条语料 1 次 LLM 调用（逐段翻译，N 段 = N 次）；语料是**单段落**片段
- 含**已在目标语言**的对照条目（`has_translation: false`），用于评估模型是否遵守「已在目标语言则原样返回」规则

## 文件清单

这个功能涉及三块内容：

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `tests/fixtures/paragraph_translate/` | 评估语料 | 参考译文与期望短语清单 + 每条一个单段落 LaTeX 片段（版本化，可提交） |
| `tools/paragraph_translate_eval/` | 评估工具 | 加载语料、跑逐段翻译、生成报告（维护者脚本） |
| `logs/paragraph_translate_eval/` | 运行输出 | HTML 报告 + JSON 结果（被 `.gitignore` 忽略，不提交） |

### 1. 语料 `tests/fixtures/paragraph_translate/`

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 语料清单：难度分层、源/目标语言、整段参考译文、期望短语、是否含翻译 |
| `trans_01.tex` … | 每条一个单段落 LaTeX 片段（**不含空行，恰好一个段落**），按难度递增排序 |

难度分层（1 简单 → 5 压力测试）：

| 层 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 1 简单直译 | 术语直译、方法/结果陈述句、中英双向 | graph neural network → 图神经网络 |
| 2 学术套话与句式 | 学术套话、长句与被动语态、定义句 | It is worth noting that、To this end |
| 3 复杂句式与上下文 | 复合从句、命令/引用/公式上下文、中英混排、让步与条件句 | `\ref{}`/`$O(n^2)$` 旁文本 |
| 4 专业领域与语体 | 机器学习/统计学术语、正式学术语体 | overfitting、confidence intervals |
| 5 压力测试 | 密集 LaTeX 长段落、超长中英文段落、已在目标语言的对照条目 | 公式+脚注段落、BLEU/术语准确率表述 |

语料中 `has_translation: false` 的条目（如 trans_20）是已在目标语言的段落，期望译文与原文一致——用于评估模型是否会画蛇添足。

语料不变量（`run_eval.py --validate` 自动校验，不通过会阻止评估）：

- 每条片段为**单段落**（逐段翻译按段落独立处理）
- 源/目标语言使用合法名称（English / Chinese），含翻译时二者不同
- 期望短语原文必须出现在**剥离 marker 后的纯文本**中——藏在命令参数或公式内部会被 marker 保护吞掉，LLM 根本看不到，不能作为期望
- 期望短语译文必须含于参考译文（保证参考译文确实覆盖了短语）
- `has_translation: false` 的条目不得声明 `expected_phrases`

新增语料时：添加 `trans_NN.tex` 片段（单段落、无空行），在 `manifest.json` 的 `items` 列表追加对应条目（含参考译文与期望短语），并运行 `--validate` 校验即可。

### 2. 评估工具 `tools/paragraph_translate_eval/`

| 文件 | 作用 |
| --- | --- |
| `core.py` | 工具专属逻辑：期望短语命中判定与语料校验（通用部分复用 `tools/eval_common.py`） |
| `run_eval.py` | 跑完整评估：逐条调用 `ParagraphTranslateService.translate()`，生成 HTML 报告（CLI 骨架复用 `tools/eval_common.py`） |
| `README.md` | 本文档 |

### 3. 运行输出 `logs/paragraph_translate_eval/`

```
logs/paragraph_translate_eval/
├── report.html     # 人工评估报告（浏览器打开，参考译文/原文/译文对照 + 修改明细 + 期望短语）
└── results.json    # 机器可读结果（每条的状态、模型译文、changes、命中/漏检标记）
```

`results.json` 中每条记录的 `status`：

| 状态 | 含义 |
| --- | --- |
| `translated` | 翻译成功且译文与原文不同 |
| `unchanged` | 翻译成功但译文与原文相同（可能未翻译，也可能是 LLM 调用失败被静默回退） |
| `error` | 检查过程抛出异常（文件/工作区问题等） |

每条记录还包含 `matched` / `missed`（期望短语命中/漏检索引）、`unmatched_changes`（未被期望短语覆盖的 change 索引，参考信息而非误报）和 `translated_latex`（还原后的可读 LaTeX）。

注意：`translate()` 在 LLM 校验重试耗尽或 marker 校验失败等内部失败时会**静默回退为原文**（status 仍为 `unchanged`），因此"含翻译语料记为 unchanged"既可能是未翻译，也可能是内部失败；报告卡片会对这种情况标黄提示，人工评估时需结合日志区分。

## 命中标记规则

- 命中：某条 `changes[].original` 与期望原文归一化后**互相包含**或共享显著词元，且 `changes[].translated` 与期望译文归一化后**互相包含**或共享显著词元
- 段落级兜底：未被任何 change 覆盖的期望短语，若其译文整体出现在模型译文中，也算命中（LLM 未必逐条输出 changes）
- 漏检：期望短语既未被任何 change 覆盖，模型译文整体也不含其译文
- `unmatched_changes`：与任何期望短语都不匹配的 change。翻译是整段行为，这类 change 不是误报，仅提示人工关注

这些标记只用于辅助人工观察，不是自动评分；最终结论以人工判断为准。

## 流程

1. `run_eval.py` 先做语料校验（单段落 / 短语可达性 / 参考译文覆盖），通过后在 `tests/fixtures/paragraph_translate/` 目录上打开评估工作区
2. 逐条调用后端 `ParagraphTranslateService.translate()`（进程内复用真实管线，目标语言取自语料条目，含 LLM 调用与 marker 保护）
3. 把译文中的 marker 还原为可读 LaTeX（成功路径幂等，回退路径真正还原）
4. 生成 `report.html`，按难度分层展示参考译文、原文、模型译文、修改明细与期望短语命中标记

## 用法

```bash
# 只校验语料（不调用 LLM，新增语料后必跑）
python tools/paragraph_translate_eval/run_eval.py --validate

# 跑完整评估（逐条调用 LLM）
python tools/paragraph_translate_eval/run_eval.py

# 快速冒烟：只跑前 5 条
python tools/paragraph_translate_eval/run_eval.py --limit 5

# 只跑某一条
python tools/paragraph_translate_eval/run_eval.py --only trans_01
```

## 依赖

- `config.json` 中已配置 `llm.api_key`（走真实 LLM 调用，产生 API 费用；无 Key 时翻译回退为原文，所有条目记为 `unchanged`）
- 每条语料 1 次 LLM 调用（逐段翻译）；无需本机 TeX Live（翻译基于文本，不涉及编译/渲染）
