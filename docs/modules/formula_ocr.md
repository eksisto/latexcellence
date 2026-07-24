# 公式识别模块 `formula_ocr/`

通过 LLM Vision API 识别图片中的 LaTeX 公式，自动分析所需宏包。不使用 Marker 保护策略（输入为图片，非文本）。

## Pydantic 输出模型

- `FormulaOCRResult`（latex, inline, confidence）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → base64 → LLM Vision → 宏包分析 → 合并结果 |
| `_preprocess` | 检查文件大小，超 2000px 等比缩放（LANCZOS 重采样） |
| `_to_base64` | PIL Image → JPEG base64 |
| `_validate_content_type` | 校验 MIME 类型为 PNG/JPEG/WEBP |

## API 路由

Prefix: `/api/formula-ocr`, Tag: `formula_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 公式 |
