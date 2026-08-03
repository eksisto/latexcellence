# LaTeX 样式修改模块 `style_modify/`

接收用户粘贴的 LaTeX 源码和自然语言修改描述，调用 LLM 直接修改 LaTeX 源码后返回。不使用 Marker（LLM 需要理解 LaTeX 结构）。

| 维度 | 样式修改 | 其他 LLM 模块 |
| --- | --- | --- |
| 是否修改源文件 | 否（仅返回结果，用户手动复制） | 是/否 |
| 是否使用 Marker | 否 | 是（逐段检查模块） |

## Pydantic 输出模型

- `StyleModifyResult`（latex，字段为 `LatexText`，经 `latex_json_guard.py` 内容校验）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `modify(latex, description)` | 校验非空 → 调用 LLM 修改 → 返回结果 |

## API 路由

Prefix: `/api/style-modify`, Tag: `style_modify`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/modify` | 根据描述修改 LaTeX 代码 |
