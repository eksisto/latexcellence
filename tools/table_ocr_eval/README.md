# 表格 OCR 人工评估工具

用途：用 `tests/fixtures/table_ocr/` 语料评估表格识别效果。这是**人工观察**的评估流程——把原表格和 OCR 识别结果都渲染成图片，左右对比，由人判断识别质量。**不是** pytest 自动测试，不会在 `pytest tests/` 中运行。

## 文件清单

这个功能涉及三块内容：

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `tests/fixtures/table_ocr/` | 评估语料 | 表格清单 + 每条表格一个 LaTeX 浮动体片段（版本化，可提交） |
| `tools/table_ocr_eval/` | 评估工具 | 渲染语料、跑 OCR、生成报告（维护者脚本） |
| `logs/table_ocr_eval/` | 运行输出 | 渲染出的 PNG、HTML 报告、JSON 结果（被 `.gitignore` 忽略，不提交） |

### 1. 语料 `tests/fixtures/table_ocr/`

| 文件 | 说明 |
| --- | --- |
| `manifest.json` | 语料清单：难度分层、标题、渲染参数（宏包 / 引擎 / DPI） |
| `table_01.tex` … `table_30.tex` | 每条一个完整的 LaTeX 表格片段（`table` / `table*` / `sidewaystable` / `longtable` 等），按难度递增排序 |

难度分层（1 简单 → 5 压力测试）：

| 层 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 1 简单表格 | 基础 tabular、booktabs 规则、`\hline` 网格、标题标签 | 三列结果表、数值网格 |
| 2 中等表格 | `\multicolumn` 合并表头、siunitx `S` 列、`p{}` 换行、`\cmidrule` | 分组子表头、超参数范围表 |
| 3 复杂表格 | `\multirow` / `\makecell` 合并单元格、数学单元格、tabularx、`table*` | 跨行跨列组合、X 列自适应 |
| 4 高难度表格 | booktabs 混合规则、`>{...}` 列格式、密集多年份数据、子表头 | 八列数据表、粗体列声明 |
| 5 压力测试 | longtable、tabularray `talltblr`、sidewaystable 横向、超大密集混合 | 长表、旋转表、mixed 全要素表 |

新增表格时：添加 `table_NN.tex` 片段，并在 `manifest.json` 的 `tables` 列表追加对应条目即可。

### 2. 评估工具 `tools/table_ocr_eval/`

| 文件 | 作用 |
| --- | --- |
| `core.py` | 共享逻辑：加载语料、LaTeX → PNG 渲染（pdflatex + preview + pdftocairo）、剥浮动体外壳、OCR 输出清洗 |
| `render_corpus.py` | 把语料渲染成评估输入图片（命令行入口） |
| `run_eval.py` | 跑完整评估：逐条调用 `TableOCRService.recognize()`，把结果再渲染成图，输出 HTML 报告 |
| `README.md` | 本文档 |

渲染细节：语料片段是完整浮动体，直接放进 `preview` 会导致紧裁失效且 `\caption` 无处安放，因此渲染前统一**剥掉浮动体外壳**（`table` / `table*` / `wraptable` 取内部内容，`sidewaystable` 取内部内容并包 `\begin{sideways}`），`\caption` 改写为 `\captionof{table}`；裸 `longtable` 等环境直接渲染。

### 3. 运行输出 `logs/table_ocr_eval/`

```
logs/table_ocr_eval/
├── input/          # 语料原表格 PNG（评估输入）
├── ocr/            # OCR 结果表格 PNG（评估输出，用于比对）
├── report.html     # 人工比对报告（浏览器打开，左右并排原图/结果图）
└── results.json    # 机器可读结果（识别出的 LaTeX、置信度、状态）
```

`results.json` 中每条记录的 `status`：

| 状态 | 含义 |
| --- | --- |
| `ok` | 识别成功且结果图已渲染 |
| `empty` | 识别结果为空（图片被判定为非表格） |
| `error` | 识别过程失败（LLM 重试耗尽等） |
| `render_error` | 识别成功但结果表格渲染失败（如缺少宏包），此时 `latex` 字段仍保留识别出的原文 |

每条记录还包含 `confidence` 与 `low_confidence`（置信度 < 0.7 时为 true），报告顶部汇总低置信度条数，卡片中低置信度条目会标黄提示。

## 流程

1. `render_corpus.py`：把语料中每条表格渲染为 PNG 输入图片
2. `run_eval.py`：逐张调用后端 `TableOCRService.recognize()`（进程内复用真实管线，含 LLM 调用）
3. 把 OCR 返回的 LaTeX 再渲染为 PNG
4. 生成 `report.html`，原图与识别结果图并列展示，并附原始 LaTeX 文本

## 用法

```bash
# 1. 渲染语料为输入图片
python tools/table_ocr_eval/render_corpus.py

# 2. 跑完整评估（会补齐缺失的输入图片，然后逐条调用 LLM）
python tools/table_ocr_eval/run_eval.py

# 快速冒烟：只跑前 5 条
python tools/table_ocr_eval/run_eval.py --limit 5

# 只跑某一条
python tools/table_ocr_eval/run_eval.py --only table_01

# 复用已有输入图片，不重新渲染
python tools/table_ocr_eval/run_eval.py --skip-render
```

## 依赖

- 本机 TeX Live（`pdflatex` + `pdftocairo`），用于表格渲染；语料用到的宏包：booktabs、array、caption、multirow、makecell、siunitx、tabularx、tabularray、rotating、longtable 等
- `config.json` 中已配置 `llm.api_key`（走真实 LLM 调用，产生 API 费用）
- 每条表格约 2 次 LLM 调用（识别 + 宏包分析）
