# 表格 OCR 识别模块 `table_ocr/`

通过 LLM Vision API 识别图片中的表格，返回标准 LaTeX 浮动体代码（`table` 环境 + `tabular` 环境）。
与 `formula_ocr` 共享图片预处理流程（格式校验、等比缩放、base64 编码），区别在于 System Prompt 和输出模型。

## Pydantic 输出模型

```python
class TableOCRResult(BaseModel):
    latex: str              # 识别出的 LaTeX 表格浮动体代码
    confidence: float = 0.0 # 置信度（0-1）
```

## 核心方法

| 方法 | 功能 |
|---|---|
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → base64 编码 → LLM Vision 调用 |
| `_preprocess(image_data)` | 检查文件大小，超 2000px 时等比缩放（LANCZOS 重采样） |
| `_to_base64(img)` | PIL Image → JPEG base64（quality=85），处理 RGBA/LA/P 模式 |
| `_validate_content_type(content_type)` | 校验 MIME 类型为 PNG/JPEG/WEBP |

## System Prompt 核心约束

```
You are a senior academic paper typesetting expert and OCR data extraction specialist.
1. Float structure: Must use table environment with \begin{table}[htbp], \centering, \caption{...}, \label{tab:xxx}.
2. Table style: Three-line table using \toprule, \midrule, \bottomrule from booktabs.
3. Accurately handle \multicolumn, \multirow, and column alignment (l/c/r).
4. 100% faithful to original content. Special symbols must be properly escaped.
5. Include required packages as LaTeX comments at the top.
6. Confidence score (0-1) based on recognition quality.
```

## API 路由

Prefix: `/api/table-ocr`, Tag: `table_ocr`

| 方法 | 路径 | 功能 |
|---|---|---|
| `POST` | `/recognize` | 上传图片（PNG/JPEG/WebP），返回 LaTeX 表格 |
