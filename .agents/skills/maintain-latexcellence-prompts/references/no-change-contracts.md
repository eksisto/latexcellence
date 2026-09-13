# 无修改契约

检查类模块必须保证：输入没有问题（或原文已在目标语言）时，主字段与原文逐字节一致且 changes 为空；有任何修改时 changes 非空、每条修改都在主字段中有对应体现。

## 契约明细

| 模块 | 主字段 | 无问题时 | 有修改时 | 其他规则 |
| --- | --- | --- | --- | --- |
| typo_check | `corrected` | `corrected` 与原文逐字节一致 + `changes: []` | `changes` 非空，`corrected` 反映每项 | marker 保护；JSON-only |
| expression_opt | `optimized` | `optimized` 与原文逐字节一致 + `changes: []` | `changes` 非空，`optimized` 反映每项 | 可追加 custom_instructions |
| paragraph_translate | `translated` | 原文已在目标语言时逐字节一致 + `changes: []` | `changes` 非空，`translated` 反映每项 | marker 保护；术语保持一致 |
| code_review | `suggestions` | `needs_review: false` + `suggestions: []` | `needs_review: true`，`suggestions` 非空 | 不用 marker；`original` 必须在源码中唯一出现；建议从不写回文件 |

## 一致性要求

- 主字段与 `changes` 的一致性由 service 层校验或工具生成（如 code_review 的 `new_text` 由工具格式化生成）。
- 修改契约文本时，必须同步检查对应 Pydantic 模型与前端展示字段，不允许出现「有 changes 但主字段没变」或「主字段变了但 changes 为空」的矛盾。
- 契约中「逐字节一致」指 JSON 解码后的文本字符串完全相等，不是视觉近似。

## 常见错误

- 把 no-change 契约误写成「返回空字符串」或「省略 changes 字段」。
- 在 typo/expression 里使用 `GENERATION_FIDELITY_RULE` 替代 `NO_INVENTION_RULE`，导致模型发明不存在的错误。
- code_review 里给 `original` 写模糊片段，导致源码中多处匹配、无法定位。
