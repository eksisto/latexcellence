# 公式识别模块 `formula_ocr/`

通过 LLM Vision API 识别图片中的 LaTeX 公式，再经确定性宏包规则表分析所需宏包（LLM 仅兜底）。不使用 Marker 保护策略（输入为图片，非文本）。

## 输出协议（去 JSON 化）

LaTeX 代码**不放在 JSON 字符串中返回**——JSON 会把未转义的 `\rho` 解释为回车、`\nu` 解释为换行（静默损坏），而 `\sigma` 等非法转义会直接解析失败。改用分隔符协议让反斜杠原样传输：

```text
<<<LATEX>>>
<LaTeX 代码>
<<<END>>>
CONFIDENCE: <0-1>
```

后端用正则提取 `<<<LATEX>>>...<<<END>>>` 块，解析 `CONFIDENCE` 元数据（缺省为 `0.0`），并自动剥离模型误加的 `$$...$$`、`\[...\]`、`\(...\)`、`$...$` 数学包裹。inline/display 判定不再由模型输出，完全由后端确定（见下节）。

## inline / display 判定（结构信号 > 视觉判定 > 默认 display）

Vision 模型对"裁切出来的独立公式图"的 inline/display 判定系统性失效（实测对含求和/积分/极限的行内公式也几乎恒输出 display），因此**模型不再输出 INLINE 判定**。最终结论只由以下来源产生（按优先级）：

1. **结构信号**（LaTeX 文本，确定性）：编号环境 / `\tag` / display-only 环境 / 多行对齐环境（含 `\\`）/ `\displaystyle` → display；`\textstyle` → inline
2. **视觉判定**（`visual_inline.py`）：已决定时以其为准
3. **默认值**：`False`（display），来源记为 `"default"`

`_reconcile_inline()` 的结构信号规则（优先级从高到低）：

- 公式编号（未带 `*` 的 `equation`/`align`/`gather`/`multline`/`eqnarray` 环境、显式 `\tag`）→ display
- 只能用于 display mode 的环境（`equation*`/`align*`/`gather*`/`multline*` 等）→ display
- 多行对齐内层环境（`aligned`/`gathered`/`split` 等且含 `\\` 换行）→ display
- 显式 `\displaystyle` → display；显式 `\textstyle` → inline
- 无结构信号时：视觉判定已决定则以其为准，否则默认 display

`\limits` / `\nolimits` 只决定大型算子上下标的绘制位置（正上下方 / 右侧），**不决定公式是行内还是行间**——行内公式同样可以写成 `$\sum\limits_{i=1}^n x_i$` 或 `$\int\limits_a^b f(x)\,dx$`。因此它们不作为矫正依据，避免把这类常见行内公式误判为 display；显式样式命令（`\displaystyle` / `\textstyle`）才是唯一可由源码判定的样式信号。

矫正扫描前先用 `latex_literals` 屏蔽逐字内容（`\verb` / `\lstinline` / verbatim 环境 / URL）与文本模式包裹（`\text` / `\mbox` 等），避免字面字符串中的 `\displaystyle`、`\begin{equation}` 触发误判。

判定发生在数学包裹剥离**之前**（基于 `<<<LATEX>>>` 原始块），因此模型误加的 `\begin{equation}` 等编号环境也能正确判为 display；包裹剥离后再按判定结果做确定性 `$...$` / `\[...\]` 包裹。

前端对 `inline_source === "default"`（无结构信号且视觉中立）的结果提供行内/行间切换：
默认展示行间公式，用户可切换为行内；切换时同步更新 `inline` 标志与按同一包裹规则
重写的 `latex`（剥 `$...$` / `\[...\]` / 空壳显示环境后重包，独立显示环境原样保留，
宏包注释前缀 `% \usepackage{...}` 原样保留），`<pre>` 展示与复制内容保持一致。
重写逻辑位于 `frontend/src/utils/formulaWrap.js`（与后端 `_wrap_latex_for_paste` /
`_strip_math_wrappers` 语义一致，`frontend/` 下 `npm test` 覆盖回归用例）。
`structure` / `visual` 已判定的结果只读展示标签。

## 输入图片的确定性视觉判定（`visual_inline.py`）

Vision 模型对"裁切出来的独立公式图"存在系统性倾向：即使上下标明显在算子右侧（行内样式），也常判为 `INLINE: false`（行间）。这类信息（上下标位置、积分字形）只存在于图片中，LaTeX 文本无法表达，因此后端增加一个**不依赖 LLM 的确定性视觉分析**（`analyze_formula_image`），对预处理后的同一张图做连通分量几何分析：

| 信号 | 判定 |
| --- | --- |
| `\sum` / `\prod` / `\lim` 类（宽型算子）上下标成对在右侧 | inline（强） |
| `\sum` / `\prod` / `\lim` 类上下标在正上下方 | display（强） |
| `\lim` 类只有条件在右下方 | inline（弱） |
| `\int` 族上下标在正上下方 | display |
| `\int` 族上下标在右侧且字形宽高比 ≤ 2.2（文本样式） | inline（弱） |

判定原则：

- 只对证据明确的情况投票：无大型算子、积分字形为显示样式（宽高比约 2.5）等不确定情形一律返回 `None`，退回默认 display，**绝不强行翻转**（在语料 40+ 组行内/行间渲染验证中 0 误翻转）。
- 所有阈值均为相对量（图宽/图高的比例、宽高比），与 DPI 无关；分析前超过 1200px 的图等比缩小，只影响速度。
- 优先级：结构信号（编号环境 / `\displaystyle` 等）> 视觉判定 > 默认 display，由 `_inline_decision` 统一合并，并返回来源（`inline_source`：`structure` / `visual` / `default`）供接口排查。

视觉分析异常时静默降级（不影响 OCR 主流程）。

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
- 接口返回另附 `inline_source`（`structure` / `visual` / `default`）字段，标注 inline 判定的最终来源，便于排查与评估

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `recognize(image_data, content_type)` | 主入口：校验格式 → 图片预处理 → 视觉分析 → base64 → LLM Vision（分隔符协议）→ 宏包规则表检测（LLM 兜底）→ 合并结果 |
| `_recognize_with_llm` | 第一次 LLM 调用：分隔符协议识别 + 内容校验 + 错误回喂重试（最多 3 次） |
| `_extract_latex_block` | 正则提取 `<<<LATEX>>>...<<<END>>>` 块 |
| `_strip_math_wrappers` | 剥离 `$$` / `\[ \]` / `\( \)` / `$` 数学包裹 |
| `_parse_confidence` | 解析 `CONFIDENCE` 元数据（缺省 `0.0`） |
| `_inline_decision` | 合并结构 / 视觉两层判定，返回 (inline, source)（优先级：结构 > 视觉 > 默认 display） |
| `_reconcile_inline` | 按 LaTeX 结构 + 视觉判定合并 inline/display（编号 / 多行对齐 / 显式样式命令，先屏蔽逐字与文本内容）；无信号时默认 display |
| `_wrap_latex_for_paste` | 按 `inline` 标志确定性添加 `$...$` / `\[...\]` 包裹（完整显示环境除外） |
| `_preprocess` | 检查文件大小，超 2000px 等比缩放（LANCZOS 重采样） |
| `_to_base64` | PIL Image → JPEG base64 |
| `_validate_content_type` | 校验 MIME 类型为 PNG/JPEG/WEBP |

`visual_inline.py` 提供 `analyze_formula_image(img) -> VisualInlineHint`（inline / confidence / reason），见上方"输入图片的确定性视觉判定"。

## API 路由

Prefix: `/api/formula-ocr`, Tag: `formula_ocr`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/recognize` | 上传图片，返回 LaTeX 公式 |
