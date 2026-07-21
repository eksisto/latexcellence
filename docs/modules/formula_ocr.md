# 公式识别模块 `formula_ocr/`

通过 LLM Vision API 识别图片中的 LaTeX 公式，并自动分析所需宏包。不使用 Marker 保护策略（输入为图片，非文本）。

## Pydantic 输出模型

```python
class FormulaOCRResult(BaseModel):
    latex: str              # 识别出的 LaTeX 公式
    inline: bool = False    # 是否为行内公式
    confidence: float = 0.0 # 置信度（0-1）
```

## 核心方法

| 方法 | 功能 |
|---|---|
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → base64 编码 → LLM Vision 调用 → 宏包分析 → 返回合并结果 |
| `_preprocess(image_data)` | 检查文件大小，超 2000px 时等比缩放（LANCZOS 重采样） |
| `_to_base64(img)` | PIL Image → JPEG base64（quality=85），处理 RGBA/LA/P 模式 |
| `_validate_content_type(content_type)` | 校验 MIME 类型为 PNG/JPEG/WEBP |

## System Prompt 核心约束

```
You are a LaTeX formula OCR engine. Rules:
1. Output ONLY the LaTeX expression, no surrounding text.
2. Use standard LaTeX math commands.
3. Classify as inline ($...$) or display ($$...$$ or \[...\]).
4. Provide a confidence score (0-1).
```

## API 路由

Prefix: `/api/formula-ocr`, Tag: `formula_ocr`

| 方法 | 路径 | 功能 |
|---|---|---|
| `POST` | `/recognize` | 上传图片（PNG/JPEG/WebP），返回 LaTeX 公式 |
