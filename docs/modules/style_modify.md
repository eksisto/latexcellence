# LaTeX 样式修改模块 `style_modify/`

接收用户粘贴的 LaTeX 源码和自然语言修改描述，调用 LLM 直接修改 LaTeX 源码后返回。不使用 Marker（LLM 需要理解 LaTeX 结构）。

| 维度 | 样式修改 | 其他 LLM 模块 |
| --- | --- | --- |
| 是否修改源文件 | 否（仅返回结果，用户手动复制） | 是/否 |
| 是否使用 Marker | 否 | 是（逐段检查模块） |

## 输出协议

LaTeX 不放在 JSON 字符串中传输（JSON 会把未转义的 `\rho` 解释为回车等静默损坏），采用与 OCR 模块一致的分隔符协议：LLM 在 `<<<LATEX>>>...<<<END>>>` 之间输出完整修改后的 LaTeX 源码，后端正则提取后用 `latex_json_guard.py` 做内容校验（控制字符 / 垃圾占位符 / 花括号与 `$` 配平），失败时回喂 LLM 重试（最多 3 次）。响应结构保持不变：`{ latex: "..." }`。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `modify(latex, description)` | 校验非空 → 调用 LLM 修改 → 返回结果 |

## API 路由

Prefix: `/api/style-modify`, Tag: `style_modify`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/modify` | 根据描述修改 LaTeX 代码 |
