# 公式识别模块 `formula_ocr/`

通过 LLM Vision API 识别图片中的 LaTeX 公式，自动分析所需宏包。不使用 Marker 保护策略（输入为图片，非文本）。

## 输出协议（去 JSON 化）

LaTeX 代码**不放在 JSON 字符串中返回**——JSON 会把未转义的 `\rho` 解释为回车、`\nu` 解释为换行（静默损坏），而 `\sigma` 等非法转义会直接解析失败。改用分隔符协议让反斜杠原样传输：

```text
<<<LATEX>>>
<LaTeX 代码>
<<<END>>>
INLINE: <true|false>
CONFIDENCE: <0-1>
```

后端用正则提取 `<<<LATEX>>>...<<<END>>>` 块，解析 `INLINE` / `CONFIDENCE` 元数据（缺省为 `false` / `0.0`），并自动剥离模型误加的 `$$...$$`、`\[...\]`、`\(...\)`、`$...$` 数学包裹。

## 复制即贴即显（确定性包裹）

LLM 自身加显示包裹极不可靠（测试时，输出有带 `$$`、有裸公式、有 `gather*`，且与 `inline` 标志矛盾），因此包裹由后端**确定性**添加，不依赖 LLM：

| 公式类型 | 返回的 `latex` |
| --- | --- |
| 行内公式（`inline=true`） | `$...$` |
| 展示公式（`inline=false`） | `\[...\]` |
| 已是完整显示环境（`align*` / `gather*` / `multline*` 等） | 原样保留，不再额外包裹 |

这样用户复制结果即贴即显；粘贴到已有 `align`/`equation` 中时也可自行去掉包裹。

## 内容校验器

`FormulaOCRResult.latex` 通过 Pydantic v2 `@field_validator` 做内容校验，校验失败时把错误反馈给 LLM 重试（最多 3 次），重试耗尽后抛 `RuntimeError`：

| 校验项 | 拦截目标 |
| --- | --- |
| 控制字符（除换行外的 `\x00-\x1f`、`\x7f`） | JSON 转义损坏特征（`\rho`→回车、`\nu`→换行等） |
| `[object Object]` 等垃圾占位符 | 模型幻觉产生的无效 token |
| 花括号不配对（忽略 `\{` / `\}`） | 截断或结构损坏 |
| `$` 数量不配对（忽略 `\$`） | 数学模式包裹损坏 |

## Pydantic 输出模型

- `FormulaOCRResult`（latex, inline, confidence）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → base64 → LLM Vision（分隔符协议）→ 宏包分析 → 合并结果 |
| `_recognize_with_llm` | 第一次 LLM 调用：分隔符协议识别 + 内容校验 + 错误回喂重试（最多 3 次） |
| `_extract_latex_block` | 正则提取 `<<<LATEX>>>...<<<END>>>` 块 |
| `_strip_math_wrappers` | 剥离 `$$` / `\[ \]` / `\( \)` / `$` 数学包裹 |
| `_parse_metadata` | 解析 `INLINE` / `CONFIDENCE` 元数据 |
| `_wrap_latex_for_paste` | 按 `inline` 标志确定性添加 `$...$` / `\[...\]` 包裹（完整显示环境除外） |
| `_preprocess` | 检查文件大小，超 2000px 等比缩放（LANCZOS 重采样） |
| `_to_base64` | PIL Image → JPEG base64 |
| `_validate_content_type` | 校验 MIME 类型为 PNG/JPEG/WEBP |

## API 路由

Prefix: `/api/formula-ocr`, Tag: `formula_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 公式 |
