# 表格 OCR 识别模块 `table_ocr/`

通过 LLM Vision API 识别图片中的表格，返回标准 LaTeX 浮动体代码（`table` + `tabular`）。与 `formula_ocr` 共享图片预处理流程。

处理流程：图片输入 → 格式校验 → 等比缩放 → base64 → 第一次 LLM（Vision 识别表格）→ 统一格式化 → 确定性宏包规则表（LLM 兜底）→ 合并结果。

## 输出协议（去 JSON 化）

LaTeX 代码**不放在 JSON 字符串中返回**——JSON 会把未转义的 `\begin` 解释为退格、`\toprule` 解释为制表符（静默损坏），而 `\midrule` 等非法转义会直接解析失败。改用分隔符协议让反斜杠原样传输：

```text
<<<LATEX>>>
<LaTeX 表格浮动体代码>
<<<END>>>
CONFIDENCE: <0-1>
```

后端用正则提取 `<<<LATEX>>>...<<<END>>>` 块，解析 `CONFIDENCE` 元数据（缺省为 `0.0`）。

## 内容校验器

`TableOCRResult.latex` 通过 Pydantic v2 `@field_validator` 做内容校验，校验失败时把错误反馈给 LLM 重试（最多 3 次），重试耗尽后抛 `RuntimeError`：

| 校验项 | 拦截目标 |
| --- | --- |
| 控制字符（除换行外的 `\x00-\x1f`、`\x7f`） | JSON 转义损坏特征（`\begin`→退格、`\toprule`→制表符等） |
| `[object Object]` 等垃圾占位符 | 模型幻觉产生的无效 token |
| 花括号不配对（忽略 `\{` / `\}`） | 截断或结构损坏 |
| `$` 数量不配对（忽略 `\$`） | 数学模式包裹损坏 |
| `\begin{X}` / `\end{X}` 环境栈式配平 | 截断或环境错配 |
| 非空输出必须包含 `\begin{table}` / `\begin{table*}` 浮动体 | 模型只输出 `tabular` 而未按协议输出 `table` 环境 |
| 非空输出必须包含 `\begin{tabular}` / `\begin{tabularx}` | table 浮动体内缺少 tabular 实体（如裸表格行），输出不可编译 |
| 三线表风格：禁止 `\hline` / `\cline` / `\vline`，禁止 tabular / tabularx / tabular* 列格式中的竖线 `|` | 网格表输入未归一化为 booktabs 三线表（`\toprule` / `\midrule` / `\bottomrule` / `\cmidrule`），失败时把风格说明反馈给 LLM 重试 |
| 空输出（`confidence` 必须为 `0.0`） | 图片不是表格时的协议约定结果，放行（`low_confidence` 标记） |

## 统一格式化层

识别出的 LaTeX 浮动体在宏包分析**之前**，先经 `latex_formatter.format_latex_source()` 统一格式化（latexindent.pl 优先，内置格式化器兜底，每层 4 个空格），整理 `table` / `tabular` 等环境的缩进与对齐。

该格式化层可通过配置 `features.table_ocr.format_latex`（默认 `true`）关闭。关闭后识别结果不再重排缩进/换行，直接进入宏包分析；设置界面（设置 → 功能 → 表格识别）提供对应开关。

## Pydantic 输出模型

- `TableOCRResult`（latex, confidence）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → LLM Vision（分隔符协议）→ 统一格式化（可配置开关）→ 宏包规则表检测（LLM 兜底）→ 合并结果 |
| `_recognize_with_llm` | 第一次 LLM 调用：分隔符协议识别 + 内容校验 + 错误回喂重试（最多 3 次） |
| `_extract_latex_block` | 正则提取 `<<<LATEX>>>...<<<END>>>` 块 |
| `_parse_confidence` | 解析 `CONFIDENCE` 元数据 |
| `_check_three_line_style` | 三线表风格校验：拒绝 `\hline` / `\cline` / `\vline` 与竖线列格式，违规反馈 LLM 重试 |
| `_preprocess` / `_to_base64` / `_validate_content_type` | 图片预处理（与 formula_ocr 共享模式） |

**降级策略**：规则表未命中且 LLM 兜底失败时，记录警告并直接返回第一次 LLM 结果。

## API 路由

Prefix: `/api/table-ocr`, Tag: `table_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 表格代码 |

## 人工评估

`tests/fixtures/table_ocr/` 是表格 OCR 的人工评估语料（每条表格一个完整 LaTeX 浮动体片段 + `manifest.json`，难度 1 简单 → 5 压力测试），配套维护者工具位于 `tools/table_ocr_eval/`：`render_corpus.py` 把语料渲染为输入图片，`run_eval.py` 逐条调用本模块 `TableOCRService.recognize()` 并把结果渲染成左右对比 HTML 报告。该流程依赖真实 LLM 调用与人工判断，不属于 `pytest tests/` 自动测试范围，详见 `tools/table_ocr_eval/README.md`。
