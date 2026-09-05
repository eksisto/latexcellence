# 提示词工程原则与约定

> 提示词集中存放于 `backend/core/prompts.py`，经 `get_prompt()` 参数化获取，
> `{language}` 占位符注入输出语言。本文记录提示词编写与优化的既定原则，供维护与
> 评审新提示词时对照。提示词的具体内容维护流程见
> `.agents/skills/maintain-latexcellence-prompts/SKILL.md`。由原「提示词优化计划」
> 改写而来，分阶段实施记录已移除。

## 1. 背景

生产模型规模有限（Qwen3.6-35B-A3B 一档），长提示词、模糊表述和过长流程描述会稀释指令，导致 JSON 契约、Marker 保护、无修改契约等关键约束执行不稳定。本项目参考 Anthropic 公开的 Claude system prompt 提炼了若干对小型模型可迁移的结构原则（见 §2），并据此统一了全项目提示词结构（见 §3）。

## 2. 对小型模型可迁移的原则

1. **分区结构**：XML 风格标签（`<role>`、`<critical_rules>` 等）划分职责，模型容易定位关键指令；
2. **短句 + 强约束**：规则用短句和 MUST / NEVER / ONLY / EXACTLY 等强词，避免长句嵌套与模棱两可；
3. **默认立场 + 例外**：先给"默认怎么做"，再给"什么情况下例外"，减少模型自行推断；
4. **外部数据不可信**：网页、搜索结果、文件内容只作为数据，不作为指令；提示词注入必须显式拒绝；
5. **不编造引用**：只能引用工具实际返回的 URL / DOI / 数据，信息不足时明说；
6. **工具优先于猜测**：能调用工具核实就不假设；
7. **格式与长度显式化**：明确输出语言、长度和格式，禁止多余内容；
8. **示例优于抽象描述**：正反例、具体输入输出对比长篇规则更容易被小模型执行。

**不可照搬**（来自 Claude 提示词的经验教训）：消费级产品的大段安全/合规规则与本项目无关，照搬浪费上下文；Claude 的"默认行动、少澄清"立场对弱模型不适用——小模型更容易在信息不足时编造，本项目采用"先校验、不确定就返回原样或明确说明"的契约。

## 3. 统一提示词结构

提示词按固定顺序组织：

1. `<role>`：一句话角色与任务；
2. `<critical_rules>`：编号短句，第一条为输出格式 / 禁止多余内容，随后是 Marker 保护、无修改契约、禁止臆造；
3. `<input>`：用户文本用显式标签包裹；
4. `<output_format>`：JSON schema 或 `<<<LATEX>>>...<<<END>>>` 分隔符协议；
5. `<examples>`：JSON 类任务提供"无修改"和"有修改"各一个紧凑示例。

配套约束：

- 关键契约（输出格式、无修改、Marker、禁止臆造）放在提示词前部；
- 规则使用短句和强约束词，删除重复和可推导的内容；
- Agent system prompt 设置体积预算，超限需人工确认（`tools/generality_gate.py` 体积监控）；
- 技术术语保持稳定，输出语言统一由 `{language}` 控制；
- 不为优化而优化：某模块评测已稳定且提示词不长时保持现状。

## 4. 变更与回归口径

提示词改动必须过回归闸门，发现回退时暂停并保留失败样本：

- `tools/prompt_comprehension_eval/`：Prompt 理解评测（逐一探测 + 历史追踪 + 回归检测），关键用例覆盖 Marker 保留、JSON-only、无修改契约、分隔符协议、语言注入；
- `tools/generality_gate.py check`：Agent 闭环优化通用性门禁（硬编码特征扫描、提示词体积监控、holdout 解析、对照组回归判定）；
- `pytest tests/ -v` 全绿（含 `tests/test_prompts.py` 结构断言）；
- 各模块闭环评测工作区（`tools/{templategen,plotgen,stylemodify}_opt/`）按需回归，经验见 `docs/templategen-optimization-lessons.md` 与 `docs/plotgen-optimization-lessons.md`。
