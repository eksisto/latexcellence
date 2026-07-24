# 表格 OCR 识别模块 `table_ocr/`

通过 LLM Vision API 识别图片中的表格，返回标准 LaTeX 浮动体代码（`table` + `tabular`）。与 `formula_ocr` 共享图片预处理流程。

处理流程：图片输入 → 格式校验 → 等比缩放 → base64 → 第一次 LLM（Vision 识别表格）→ 第二次 LLM（宏包分析）→ 合并结果。

## Pydantic 输出模型

- `TableOCRResult`（latex, confidence）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `recognize(image_data, content_type)` | 主入口：两次 LLM 调用 + 宏包分析合并 |
| `_recognize_with_llm` | Vision API 识别表格内容 |
| `_preprocess` / `_to_base64` / `_validate_content_type` | 图片预处理（与 formula_ocr 共享模式） |

**降级策略**：第二次 LLM 调用（宏包分析）失败时，记录警告并直接返回第一次 LLM 结果。

## API 路由

Prefix: `/api/table-ocr`, Tag: `table_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 表格代码 |
