# 公式识别模块 `formula_ocr/`

通过 LLM Vision API 识别图片中的 LaTeX 公式，再经确定性宏包规则表分析所需宏包（LLM 仅兜底）。不使用 Marker 保护策略（输入为图片，非文本）。

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

## inline / display 判定（视觉决策树 + 结构矫正）

`INLINE` 由 Vision 模型按视觉决策树分类，后端再按 LaTeX 结构做确定性矫正（`_reconcile_inline`）——优先级最高的前两条与可源码判定的规则不依赖模型：

| 优先级 | 视觉规则（模型判断） | 结论 |
| --- | --- | --- |
| 1 | 公式带编号（如 `(1)`、`(2.3)`） | display |
| 2 | 多行且等号/箭头上下对齐 | display |
| 3 | `\sum`/`\prod`/`\int`/`\lim`/`\max`/`\min` 上下标在正上下方 | 倾向 display |
| 4 | 上下标在符号右侧（如 `\sum_{i=1}^n` 上下标在右） | 倾向 inline |
| 5 | 积分号大、上下限在正上下方 | 倾向 display |
| 6 | 积分号小、上下限在右侧 | 倾向 inline |
| 7 | 分式大、多层嵌套清晰 | 倾向 display |
| 8 | 分式小、嵌套明显压缩 | 倾向 inline |
| 9 | 矩阵行距自然、大括号舒展 | 倾向 display |
| 10 | 矩阵行距紧凑、大括号拥挤 | 倾向 inline |
| 11 | 特征冲突时考虑作者是否强制 `\displaystyle` / `\textstyle` | 按实际渲染判断 |

后端 `_reconcile_inline()` 的确定性矫正规则（优先级从高到低）：

- 公式编号（未带 `*` 的 `equation`/`align`/`gather`/`multline`/`eqnarray` 环境、显式 `\tag`）→ display
- 只能用于 display mode 的环境（`equation*`/`align*`/`gather*`/`multline*` 等）→ display
- 多行对齐内层环境（`aligned`/`gathered`/`split` 等且含 `\\` 换行）→ display
- 大型运算符显式 `\limits` → display；`\nolimits` → inline
- 显式 `\displaystyle` → display；显式 `\textstyle` → inline
- 其余视觉特征（积分号形态、分式大小、矩阵舒展度等）保留模型判断

`\limits` / `\nolimits` 优先于样式命令：它们直接决定上下标的实际渲染位置（如 `\textstyle \sum\limits` 渲染后上下标仍在正上下方），与"以实际渲染为准"一致。

矫正扫描前先用 `latex_literals` 屏蔽逐字内容（`\verb` / `\lstinline` / verbatim 环境 / URL）与文本模式包裹（`\text` / `\mbox` 等），避免字面字符串中的 `\limits`、`\displaystyle`、`\begin{equation}` 触发误判。

矫正发生在数学包裹剥离**之前**（基于 `<<<LATEX>>>` 原始块），因此模型误加的 `\begin{equation}` 等编号环境也能正确判为 display；包裹剥离后再按矫正结果做确定性 `$...$` / `\[...\]` 包裹。

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
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → base64 → LLM Vision（分隔符协议）→ 宏包规则表检测（LLM 兜底）→ 合并结果 |
| `_recognize_with_llm` | 第一次 LLM 调用：分隔符协议识别 + 内容校验 + 错误回喂重试（最多 3 次） |
| `_extract_latex_block` | 正则提取 `<<<LATEX>>>...<<<END>>>` 块 |
| `_strip_math_wrappers` | 剥离 `$$` / `\[ \]` / `\( \)` / `$` 数学包裹 |
| `_parse_metadata` | 解析 `INLINE` / `CONFIDENCE` 元数据 |
| `_reconcile_inline` | 按 LaTeX 结构确定性矫正 inline/display（编号 / 多行对齐 / 显式上下标位置与样式命令，先屏蔽逐字与文本内容） |
| `_wrap_latex_for_paste` | 按 `inline` 标志确定性添加 `$...$` / `\[...\]` 包裹（完整显示环境除外） |
| `_preprocess` | 检查文件大小，超 2000px 等比缩放（LANCZOS 重采样） |
| `_to_base64` | PIL Image → JPEG base64 |
| `_validate_content_type` | 校验 MIME 类型为 PNG/JPEG/WEBP |

## API 路由

Prefix: `/api/formula-ocr`, Tag: `formula_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 公式 |
