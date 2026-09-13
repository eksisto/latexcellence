# 常规提示词骨架

## 标准顺序

`<role>` → `<task>` → `<critical_rules>` → `[<input>]` → `<output_format>` → `<examples>`

- 标签可缺席，但出现时相对顺序必须遵守上述顺序。
- `<input>` 仅用于输入嵌入 system 的场景；逐段检查类模块的正文一律放 user 提示词。
- expression_opt 允许在 `<examples>` 后追加 `<custom_instructions>`。

## 骨架（逐段检查类，如 typo_check.system）

```text
<role>
You are a LaTeX document proofreader.
</role>
<task>
Find and fix typos, spelling errors, and grammar mistakes in the given text.
</task>
<critical_rules>
CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:
1-3. MARKER_PROTECTION_RULES
4. no-change contract（无问题时主字段与原文逐字节一致，changes 为空）
5. changes 一致性（有修改时非空且逐项与主字段一致）
6. JSON_ONLY_RULE + GENERATION_FIDELITY_RULE
</critical_rules>
<output_format>
JSON format (strictly follow this schema):
{{
  "corrected": "<the corrected text with all markers unchanged>",
  "changes": [ ... ]
}}
</output_format>
<examples>
No-change example: ...（返回原文逐字节，changes: []）
Change example: ...（一条修改对应一个 changes 条目）
</examples>
```

## 骨架（user 提示词）

```text
Please check the following text for typos and errors.

<input>
{text}
</input>
```

正文放 user，不放 system；`{text}` 由调用方传入。

## 变体

### expression_opt

- 主字段 `optimized`；在 `<examples>` 之后追加：

```text
<custom_instructions>
CUSTOM INSTRUCTIONS FROM USER:
{custom_prompt}
Above custom instructions take priority over the general guidelines.
</custom_instructions>
```

### code_review

- `<output_format>` 区先拼接 `LATEX_JSON_ESCAPE_RULES`，再给 JSON schema。
- `original` / `suggested` 必须带足够上下文，使 `original` 在源码中恰好出现一次。
- 不使用 marker 保护（审阅真实格式化 LaTeX，建议从不写回文件）。

### bib_manager.format_analysis

- 输入嵌入 system：`<input>` 位于 `<critical_rules>` 之后、`<output_format>` 之前。
- JSON 承载 BibTeX 源码时拼接 `BIBTEX_JSON_ESCAPE_RULES`（不是 LaTeX 版）。

### 输出协议非 JSON 的模块

- writing_chat 编辑输出、OCR 类模块使用分隔符协议 `<<<LATEX>>>...<<<END>>>` 直接传输纯文本，不套 JSON schema。
- compile_error_translator 也使用分隔符协议。

## 注意事项

- JSON 示例中的字面花括号必须写 `{{ }}`（`get_prompt` 会执行 `str.format()`）。
- 新增模块按同一骨架写，不要发明新标签或新结构。
- 共享约束（marker 保护、JSON-only、no-invention/generation fidelity、JSON 转义）一律引用顶部常量，禁止复制粘贴。

## Agent 提示词分区（计划 E）

Agent（template_gen / plot_gen / style_modify）不套 output_format / examples，输出由
工具调用驱动。核心/常驻 system 只保留 `<role>` / `<task>` / `<critical_rules>`，
`<critical_rules>` 内规则按职责拆成成对闭合的 XML 分区：

| 分区 | 职责 | 示例位置 |
| --- | --- | --- |
| `<workflow>` | 主循环步骤（写文件→编译→修复→finish） | 三个 Agent 的 agent_system |
| `<quality_gate>` | finish 前的通过条件（压缩版，细节放阶段块） | template_gen / plot_gen agent_system |
| `<failure_budget>` | 轮次/文件预算、何时放弃并 finish | 三个 Agent 的 agent_system |
| `<tool_protocol>` | 工具调用约束（内容完整、路径、禁用实验） | 三个 Agent 的 agent_system |

每个阶段块（template_gen 的 `planning_system` / `compile_phase_system` /
`validation_phase_system`，plot_gen / style_modify 的 `refine_system`）顶部必须带
`<stage_contract>`，统一记录五字段：

```text
<stage_contract>
- entry: <进入条件>
- input: <该阶段可用的输入>
- output: <该阶段产出的内容>
- done: <离开该阶段的完成条件>
- fallback: <失败回退方式>
</stage_contract>
```

其他规则：阶段提示词保持延迟注入，禁止合并回单个超长 system；改 Agent 提示词后
运行 `tools/generality_gate.py size`（预算 template_gen ≤ 22000 / plot_gen ≤
15000 / style_modify ≤ 5000 字符）与 `check --full`；`tests/test_prompts.py` 的
计划 E 测试会校验分区闭合与 stage_contract 五字段，改动后必须通过。
