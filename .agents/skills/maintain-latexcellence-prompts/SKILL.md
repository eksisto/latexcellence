---
name: maintain-latexcellence-prompts
description: "Maintains the centralized LLM prompt templates for the LateXcellence project, all stored in backend/core/prompts.py. Use whenever creating, editing, restructuring, or reviewing prompts (typo/expression/translate check modules, code_review, writing_chat, bib_manager, OCR modules, or the template_gen / plot_gen / style_modify agents), and when running prompt tests, the comprehension eval, or the generality size/check gates. Enforces the unified prompt structure, shared rule constants, marker protection, no-change contracts, placeholder rules, and the regression workflow."
---

# 维护 LateXcellence 提示词

## 概览

LateXcellence 的所有 LLM 提示词集中存放在 `backend/core/prompts.py` 的 `_PROMPTS` 中，统一通过 `get_prompt(module, key, **kwargs)` 获取。修改提示词时必须遵守本 skill 的格式、共享常量、安全约束与回归流程；禁止在 service/router 中硬编码长提示词。

## 唯一入口

- 新增或修改提示词一律改 `_PROMPTS`，调用方保持 `get_prompt()`。
- 删除键或模块前先确认无引用（`rg -n 'module", "key' backend frontend`）。
- 新增模块时同步更新 `tests/test_prompts.py` 与相关调用方。
- 改动结构设计时同步更新 `prompts.py` 顶部 docstring（结构分区说明）。

## 统一结构

常规模块 system 提示词按固定顺序组织：

`<role>` → `<task>` → `<critical_rules>` → `[<input>]` → `<output_format>` → `<examples>`

- `<input>` 仅在输入嵌入 system 时出现（如 bib_manager.format_analysis）；逐段检查类（typo_check / expression_opt / paragraph_translate）的正文由 user 提示词以 `<input>` 包裹。
- expression_opt 可在 `<examples>` 之后追加 `<custom_instructions>`（内含 `{custom_prompt}`，注明用户指令优先）。
- Agent 提示词（template_gen / plot_gen / style_modify）只保留 `<role>` / `<task>` / `<critical_rules>`，不套用 output_format / examples（输出由工具调用驱动）。
- 骨架与示例见 [references/structure.md](references/structure.md)。

## 共享常量，禁止复制

同一约束必须复用文件顶部的共享常量，禁止跨模块复制粘贴：

- `MARKER_PROTECTION_RULES` — 任何处理含 marker 文本的提示词必须拼接，明令禁止修改/删除/拆分 marker。
- `JSON_ONLY_RULE` — 要求 JSON 输出时拼接。
- `NO_INVENTION_RULE`（检查类）与 `GENERATION_FIDELITY_RULE`（生成类）语义不同，按任务性质二选一。
- `UNTRUSTED_DATA_RULE` — 处理用户文本/文件/网页等不可信输入时拼接。
- `LATEX_JSON_ESCAPE_RULES` / `BIBTEX_JSON_ESCAPE_RULES` — JSON 字符串承载 LaTeX / BibTeX 源码时拼接；BibTeX 场景必须用 BIBTEX 版（`%`、花括号、`$` 的语义不同）。

## 无修改契约

检查类模块必须保留「无问题时主字段与原文逐字节一致、changes 为空」的契约：

- typo_check：`corrected` + `changes`
- expression_opt：`optimized` + `changes`
- paragraph_translate：`translated` + `changes`
- code_review：`needs_review: false` + `suggestions: []`

有修改时 `changes` 非空且逐项与主字段一致。契约明细见 [references/no-change-contracts.md](references/no-change-contracts.md)。

## Marker 保护

- 处理含 `__CMD_0000__` / `__MATH_0000__` 等 marker 的文本时，提示词必须包含 `MARKER_PROTECTION_RULES`，禁止模型修改、删除、拆分、合并或重排任何 marker。
- 唯一例外：code_review 审阅格式化后的真实 LaTeX，建议只读展示、从不写回文件，因此不使用 marker 保护。

## Agent 提示词阶段拆分

- template_gen：`agent_system` 为常驻核心；`planning_system` 开局注入一次；`compile_phase_system` 首次编译尝试后注入一次；`validation_phase_system` 首次成功编译后注入一次（工具链不可用时前置到开局）。
- finish 必须在 validation 质量门禁注入之后才能被接受；同一轮注入 validation 时调用 finish 会被拒绝并延迟到下一轮。
- style_modify / plot_gen：`agent_system` + `refine_system`。
- 禁止把阶段提示词合并回单个超长 system。时序细节见 [references/template-gen-phases.md](references/template-gen-phases.md)。
- Agent 规则用一致的 XML 分区：`<workflow>` / `<quality_gate>` / `<failure_budget>` / `<tool_protocol>` 成对闭合；每个阶段块顶部带 `<stage_contract>`（entry / input / output / done / fallback 五字段），见 [references/structure.md](references/structure.md)。分区与 stage_contract 由 `tests/test_prompts.py` 的计划 E 测试校验。

## 通用性门禁

修改 Agent 提示词（template_gen / plot_gen / style_modify）后必须运行：

```bash
python tools/generality_gate.py size
python tools/generality_gate.py check --full
```

体积预算：template_gen 22000 / plot_gen 15000 / style_modify 5000 字符，超预算非零退出。禁止在提示词中混入学校模板、评测场景数值、OS 路径等部署事实。

## 占位符与花括号

- `get_prompt()` 总是执行 `str.format()`：字面花括号写 `{{ }}`，占位符用 `{name}`。
- 新增占位符必须同步所有调用方；漏传参数抛 KeyError 是预期快速失败，禁止把 `{language}` 字面量发给 LLM。
- JSON 示例中的花括号必须写 `{{ }}`。

## 回归流程

每次提示词改动按顺序执行：

1. `python -m pytest tests/test_prompts.py -q`（结构/占位符/转义规则）
2. `python tools/prompt_comprehension_eval/run_eval.py --dry-run`；有 key 时用 `--only` / `--limit` 跑真实用例
3. Agent 提示词改动追加「通用性门禁」两个命令
4. `python -m pytest tests/ -q`

修改测试时，测试用的 LaTeX 字符串必须使用原始字符串 `r"..."`。

## 资源

- `scripts/check_prompt_structure.py` — 静态校验 `_PROMPTS` 模块清单与标签顺序，运行：`python .agents/skills/maintain-latexcellence-prompts/scripts/check_prompt_structure.py`
- `references/structure.md` — 常规提示词骨架与示例
- `references/no-change-contracts.md` — 各检查模块契约明细
- `references/template-gen-phases.md` — Agent 阶段注入时序与 finish 延迟机制
