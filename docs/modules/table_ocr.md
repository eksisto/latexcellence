# 表格 OCR 识别模块 `table_ocr/`

通过 LLM Vision API 识别图片中的表格，返回标准 LaTeX 浮动体代码（`table` 环境 + `tabular` 环境）。
与 `formula_ocr` 共享图片预处理流程（格式校验、等比缩放、base64 编码），区别在于 System Prompt 和输出模型。

## 处理流程

```
图片输入 → 格式校验 → 等比缩放 → base64 编码
    ↓
第一次 LLM 调用（Vision API）：识别表格内容，输出 LaTeX 代码 + 置信度
    ↓
第二次 LLM 调用（文本分析）：分析 LaTeX 代码依赖的宏包
    ↓
合并：宏包注释（LaTeX 注释格式）前置到 LaTeX 代码前
    ↓
返回完整结果
```

第二步使用的 LaTeX 宏包分析器为独立组件 `backend/utils/latex_package_analyzer.py`，不局限于表格处理场景，其他模块也可复用。

## Pydantic 输出模型

```python
class TableOCRResult(BaseModel):
    latex: str              # 识别出的 LaTeX 表格浮动体代码（含前置宏包注释）
    confidence: float = 0.0 # 置信度（0-1）
```

## 核心方法

| 方法 | 功能 |
|---|---|
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → base64 编码 → 第一次 LLM 调用 → 第二次 LLM 宏包分析 → 合并结果 |
| `_recognize_with_llm(llm, base64_image, content_type)` | 第一次 LLM 调用：Vision API 识别表格内容 |
| `_preprocess(image_data)` | 检查文件大小，超 2000px 时等比缩放（LANCZOS 重采样） |
| `_to_base64(img)` | PIL Image → JPEG base64（quality=85），处理 RGBA/LA/P 模式 |
| `_validate_content_type(content_type)` | 校验 MIME 类型为 PNG/JPEG/WEBP |

## System Prompt 核心约束（第一次 LLM 调用）

```
You are a senior academic paper typesetting expert and OCR data extraction specialist.
1. Float structure: Must use table environment with \begin{table}[htbp], \centering, \caption{...}, \label{tab:xxx}.
2. Table style: Three-line table using \toprule, \midrule, \bottomrule from booktabs.
3. Accurately handle \multicolumn, \multirow, and column alignment (l/c/r).
4. 100% faithful to original content. Special symbols must be properly escaped.
5. Confidence score (0-1) based on recognition quality.
```

## 处理结果示例

对于使用了 `\toprule`、`\midrule`、`\bottomrule`、`\multirow` 的表格代码，返回结果：

```latex
% \usepackage{booktabs}
% \usepackage{multirow}
\begin{table}[htbp]
\centering
\caption{示例表格}
\label{tab:example}
\begin{tabular}{lcr}
\toprule
...
```

不需要额外宏包时，直接返回原始 LaTeX 代码（无前置注释）。

## 优雅降级

若第二次 LLM 调用（宏包分析）失败，记录警告并直接返回第一次 LLM 的结果，不影响核心识别功能。

## API 路由

Prefix: `/api/table-ocr`, Tag: `table_ocr`

| 方法 | 路径 | 功能 |
|---|---|---|
| `POST` | `/recognize` | 上传图片（PNG/JPEG/WebP），返回 LaTeX 表格（含宏包注释） |
