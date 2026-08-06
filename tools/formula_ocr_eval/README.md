# 公式 OCR 人工评估工具

用途：用 `tests/fixtures/formula_ocr/` 语料评估公式识别效果。这是**人工观察**的评估流程——把原公式和 OCR 识别结果都渲染成图片，左右对比，由人判断识别质量。**不是** pytest 自动测试，不会在 `pytest tests/` 中运行。

## 文件清单

这个功能涉及三块内容：

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `tests/fixtures/formula_ocr/` | 评估语料 | 公式清单 + 每条公式一个 LaTeX 片段（版本化，可提交） |
| `tools/formula_ocr_eval/` | 评估工具 | 渲染语料、跑 OCR、生成报告（维护者脚本） |
| `logs/formula_ocr_eval/` | 运行输出 | 渲染出的 PNG、HTML 报告、JSON 结果（被 `.gitignore` 忽略，不提交） |

### 1. 语料 `tests/fixtures/formula_ocr/`

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 语料清单：难度分层、标题、行内/展示模式、渲染参数（宏包 / 引擎 / DPI） |
| `formula_01.tex` … `formula_30.tex` | 每条公式一个裸 LaTeX 片段（不含 `$` / `\[ \]` 包裹），按难度递增排序 |

难度分层（1 简单 → 5 压力测试）：

| 层 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 1 简单公式 | 基础运算、一元二次、积分、极限、导数 | 勾股定理、求根公式 |
| 2 中等公式 | 嵌套求和/连乘、二项式、泊松分布、3×3 行列式 | 欧拉恒等式、高斯积分 |
| 3 复杂公式 | 多层括号、矩阵、分段、多重积分 | 施瓦茨不等式、泰勒级数 |
| 4 高难度公式 | 多行、嵌套矩阵、连分数、张量 | 麦克斯韦方程组、连分数 |
| 5 压力测试 | 混合上下标、巨矩阵、希腊字母密集 | 巨矩阵、纳维-斯托克斯方程 |

新增公式时：添加 `formula_NN.tex` 片段，并在 `manifest.json` 的 `formulas` 列表追加对应条目即可。

### 2. 评估工具 `tools/formula_ocr_eval/`

| 文件 | 作用 |
| --- | --- |
| `core.py` | 共享逻辑：加载语料、LaTeX → PNG 渲染（pdflatex + preview + pdftocairo）、OCR 输出清洗 |
| `render_corpus.py` | 把语料渲染成评估输入图片（命令行入口） |
| `run_eval.py` | 跑完整评估：逐条调用 `FormulaOCRService.recognize()`，把结果再渲染成图，输出 HTML 报告 |
| `README.md` | 本文档 |

### 3. 运行输出 `logs/formula_ocr_eval/`

```
logs/formula_ocr_eval/
├── input/          # 语料原公式 PNG（评估输入）
├── ocr/            # OCR 结果公式 PNG（评估输出，用于比对）
├── report.html     # 人工比对报告（浏览器打开，左右并排原图/结果图）
└── results.json    # 机器可读结果（识别出的 LaTeX、置信度、状态）
```

`results.json` 中每条记录的 `status`：

| 状态 | 含义 |
| --- | --- |
| `ok` | 识别成功且结果图已渲染 |
| `empty` | 识别结果为空 |
| `error` | 识别过程失败（LLM 重试耗尽等） |
| `render_error` | 识别成功但结果公式渲染失败（如缺少宏包），此时 `latex` 字段仍保留识别出的原文 |

每条记录还包含 `expected_inline`（语料标注的行内/展示模式）与 `result_inline`（识别判定），报告顶部会汇总二者不一致的条数，卡片中不一致的条目会标红提示，用于评估 inline/display 分类效果。

## 流程

1. `render_corpus.py`：把语料中每条公式渲染为 PNG 输入图片
2. `run_eval.py`：逐张调用后端 `FormulaOCRService.recognize()`（进程内复用真实管线，含 LLM 调用）
3. 把 OCR 返回的 LaTeX 再渲染为 PNG
4. 生成 `report.html`，原图与识别结果图并列展示，并附原始 LaTeX 文本

## 用法

```bash
# 1. 渲染语料为输入图片
python tools/formula_ocr_eval/render_corpus.py

# 2. 跑完整评估（会补齐缺失的输入图片，然后逐条调用 LLM）
python tools/formula_ocr_eval/run_eval.py

# 快速冒烟：只跑前 5 条
python tools/formula_ocr_eval/run_eval.py --limit 5

# 只跑某一条
python tools/formula_ocr_eval/run_eval.py --only formula_01

# 复用已有输入图片，不重新渲染
python tools/formula_ocr_eval/run_eval.py --skip-render
```

## 依赖

- 本机 TeX Live（`pdflatex` + `pdftocairo`），用于公式渲染
- `config.json` 中已配置 `llm.api_key`（走真实 LLM 调用，产生 API 费用）
- 每条公式约 2 次 LLM 调用（识别 + 宏包分析）
