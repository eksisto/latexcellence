# 表格 OCR 识别模块 `table_ocr/`

通过 LLM Vision API 识别图片中的表格，返回标准 LaTeX 浮动体代码（`table` + `tabular`）。与 `formula_ocr` 共享图片预处理流程。

处理流程：图片输入 → 格式校验 → 等比缩放 → base64 → 第一次 LLM（Vision 识别表格）→ 第二次 LLM（宏包分析）→ 合并结果。

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
| 空输出（`confidence` 必须为 `0.0`） | 图片不是表格时的协议约定结果，放行（`low_confidence` 标记） |

## Pydantic 输出模型

- `TableOCRResult`（latex, confidence）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → LLM Vision（分隔符协议）→ 宏包分析 → 合并结果 |
| `_recognize_with_llm` | 第一次 LLM 调用：分隔符协议识别 + 内容校验 + 错误回喂重试（最多 3 次） |
| `_extract_latex_block` | 正则提取 `<<<LATEX>>>...<<<END>>>` 块 |
| `_parse_confidence` | 解析 `CONFIDENCE` 元数据 |
| `_preprocess` / `_to_base64` / `_validate_content_type` | 图片预处理（与 formula_ocr 共享模式） |

**降级策略**：第二次 LLM 调用（宏包分析）失败时，记录警告并直接返回第一次 LLM 结果。

## API 路由

Prefix: `/api/table-ocr`, Tag: `table_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 表格代码 |
