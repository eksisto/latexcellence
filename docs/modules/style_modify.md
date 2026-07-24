# LaTeX 样式修改模块 `style_modify/`

## 设计特点

LaTeX 样式修改模块接收用户粘贴的 LaTeX 源码和自然语言修改描述，调用 LLM 直接修改 LaTeX 源码后返回。

| 维度 | 样式修改 | 其他 LLM 模块 |
| --- | --- | --- |
| 输入 | 原始 LaTeX 源码 + 自然语言描述 | LaTeX 项目文件 / 图片 |
| 是否使用 Marker | 否（LLM 需要理解 LaTeX 结构来执行修改） | 是（typo_check/expression_opt/code_review/terminology_check）
| 是否修改源文件 | 否（仅返回修改结果，用户手动复制） | 是 / 否 |
| 典型场景 | 表格对齐、文字样式、图片排版、位置调整 | 文本校对、表达优化、源码格式化 |

## `service.py` — 样式修改业务逻辑

### Pydantic 输出模型

```python
class StyleModifyResult(BaseModel):
    latex: str     # 修改后的完整 LaTeX 代码
```

### 核心方法

| 方法 | 功能 |
| --- | --- |
| `modify(latex, description)` | 异步：校验非空 → 调用 LLM 修改 → 返回结果 |
| `_modify_with_llm(llm, latex, description)` | 构建 user message（latex 源码 + 描述）→ retry_with_validation → 返回 StyleModifyResult |

### LLM System Prompt 核心约束

```
- Make ONLY the changes requested by the user, nothing more
- Preserve all content that the user did not ask to change
- Return the COMPLETE modified LaTeX code, not just the changed portion
- Do NOT add markdown fences or extra commentary in the JSON response
```

## `router.py` — API 路由

Prefix: `/api/style-modify`, Tag: `style_modify`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/modify` | 根据描述修改 LaTeX 代码 |

### 请求体

```json
{ "latex": "\\begin{tabular}...", "description": "第一列居中对齐" }
```

### 返回

```json
{ "latex": "修改后的完整 LaTeX 代码" }
```
