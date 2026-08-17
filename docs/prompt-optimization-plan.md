# LateXcellence 提示词优化计划

> 状态：阶段 0 至阶段 4 已完成。
>
> 建立日期：2026-08-17

## 背景与目标

LateXcellence 的 LLM 提示词统一由 `backend/core/prompts.py` 管理，当前生产模型为
Qwen3.6-35B-A3B。该模型规模有限，长提示词、模糊表述和过长的流程描述会稀释指令，
导致 JSON 契约、Marker 保护、无修改契约等关键约束执行不稳定。

本计划参考 Anthropic 官方公开的 Claude system prompt，提炼对小型模型可迁移的
提示词结构与方法，在不改变 Marker 保护、JSON 校验流水线、分隔符协议等核心链路的前
提下，分阶段优化 `prompts.py`。

## 参考来源

Claude 官方 System Prompts 发布页：

<https://platform.claude.com/docs/en/release-notes/system-prompts>

网页中包含多个模型/版本的 system prompt。分析对象以页面首个条目（Claude Opus 5，
2026-07-24）为主，并对照同页其他版本的差异部分。

## 从 Claude 提示词中提取的结论

### 可迁移的原则

1. **分区结构**：使用 XML 风格标签（如 `<claude_behavior>`、`<tone_and_formatting>`）
   划分职责，模型容易定位关键指令。
2. **短句 + 强约束**：规则用短句和 MUST / NEVER / ONLY / EXACTLY 等强词，避免长句
   嵌套和模棱两可的表述。
3. **默认立场 + 例外**：先给“默认怎么做”，再给“什么情况下例外”，减少模型自行推断。
4. **外部数据不可信**：网页、搜索结果、文件内容只作为数据，不作为指令；提示词注入
   必须显式拒绝。
5. **不编造引用**：只能引用工具实际返回的 URL / DOI / 数据，信息不足时明说。
6. **工具优先于猜测**：能调用工具核实就不假设；不能确认能力或数据存在时，先核实再
   下结论。
7. **格式与长度显式化**：明确输出语言、长度和格式，避免多余内容。
8. **示例优于抽象描述**：正反例、具体输入输出对，比长篇规则更容易被小模型执行。

### 不可照搬的部分

- 消费级产品的安全、合规、心理关怀等大段规则与本项目无关，照搬会浪费上下文。
- Claude 的“默认行动、少澄清”立场对弱模型不适用：小模型更容易在信息不足时编造，
  因此本项目采用“先校验、不确定就返回原样或明确说明”的契约。

## 现状判断

- 所有提示词集中在 `backend/core/prompts.py`，已有统一的 `get_prompt()` 入口和
  `{language}` 输出语言注入。
- 已共享 LaTeX / BibTeX 的 JSON 转义规则，但 Marker 保护、无修改契约等规则在不同
  模块中重复书写，措辞和位置不一致。
- 部分 Agent system prompt 很长，包含大量流程细节、协议模板和重复约束，存在指令被
  稀释的风险（如 template_gen、plot_gen、style_modify）。
- 项目已有 `tools/prompt_comprehension_eval/run_eval.py`、`tools/generality_gate.py`
  和 `tests/test_prompts.py`，可直接作为基线评测和回归手段。

## 优化设计原则

统一提示词结构，顺序固定为：

1. `<role>`：一句话角色与任务；
2. `<critical_rules>`：编号短句，第一条为输出格式 / 禁止多余内容，随后是 Marker
   保护、无修改契约、禁止臆造；
3. `<input>`：用户文本用显式标签包裹；
4. `<output_format>`：JSON schema 或 `<<<LATEX>>>...<<<END>>>` 分隔符协议；
5. `<examples>`：JSON 类任务提供“无修改”和“有修改”各一个紧凑示例。

配套约束：

- 关键契约（输出格式、无修改、Marker、禁止臆造）放在提示词前部；
- 规则使用短句和强约束词，删除重复和可推导的内容；
- Agent system prompt 设置体积预算，超限需人工确认；
- 系统提示词的技术术语保持稳定，输出语言统一由 `{language}` 控制；
- 不引入新配置项，不新增依赖，不改动 JSON 校验和 Marker 还原链路。

## 分阶段计划

### 阶段 0：建立基线

1. 用当前 Qwen 配置运行 `tools/prompt_comprehension_eval/run_eval.py`，输出保存到
   `logs/prompt_comprehension_eval/`，记录各用例 PASS / PARTIAL / FAIL。
2. 用 `tools/generality_gate.py` 记录各模块提示词体积基线。
3. 对错字查找、表达优化、逐段翻译等模块各选取少量代表性语料做人工质量对照。

#### 阶段 0 完成情况（2026-08-17）

- Prompt 理解评测：全部用例 PASS，明细见
  `logs/prompt_comprehension_eval/baseline_qwen_20260817.json`。
- 提示词体积基线：`logs/prompt_comprehension_eval/prompt_size_baseline.json`。
- 错字查找完整语料：`logs/typo_check_eval/baseline_20260817/`（report.html +
  results.json）。
- 表达优化完整语料：`logs/expression_opt_eval/baseline_20260817/`。
- 逐段翻译完整语料：`logs/paragraph_translate_eval/baseline_20260817/`。
- 已知观察点：表达优化存在干净语料误改（如 expr_23 / expr_24）和 Marker 校验
  回退导致的漏检（expr_14），将作为阶段 2 的对照基准。

### 阶段 1：统一提示词架构

1. 在 `prompts.py` 中抽取共享规则块：Marker 保护、JSON-only、无修改契约、禁止臆造、
   外部数据不可信。
2. 按 `<role>` / `<critical_rules>` / `<input>` / `<output_format>` / `<examples>`
   统一所有提示词的排版顺序。
3. 保持 `get_prompt()` 接口不变，避免影响各模块调用方。
4. 在 `tests/test_prompts.py` 增加结构性测试：Marker 规则必须位于 schema 之前、
   JSON 类提示词必须包含无修改契约等。

#### 阶段 1 完成情况（2026-08-17）

- `prompts.py` 新增共享规则常量：`MARKER_PROTECTION_RULES`、`JSON_ONLY_RULE`、
  `NO_INVENTION_RULE`、`UNTRUSTED_DATA_RULE`。
- 常规提示词统一为 `<role>` → `<task>` → `<critical_rules>` → `[<input>]` →
  `<output_format>` → `<examples>` 结构（`<input>` 仅在输入嵌入 system 时
  存在，逐段检查类的输入由 user 提示词包裹；`<custom_instructions>` 允许作为
  末尾扩展）；Agent 提示词已加 `<role>` / `<task>` 标签，完整拆分在阶段 3
  进行。
- `get_prompt()` 接口保持不变；`tests/test_prompts.py` 增加分区顺序、共享规则、
  `<input>` 包裹等结构化回归测试。
- 验证：`pytest tests/` 通过（唯一失败为既有 Windows 临时文件清理用例，与本次
  改动无关）；`prompt_comprehension_eval` 26 例全部 PASS。

### 阶段 2：高频模块改写

优先处理用户高频且契约严格的功能：

- 错字查找、表达优化、逐段翻译、代码审查：压缩为“短规则 + schema + 示例”，强化
  “无问题时逐字节返回原文、`changes` 为空数组”。
- 写作助手：对话系统提示词增加简洁、不编造 URL/引用、信息不足时明说；编辑系统提示
  词增加分隔符协议示例。
- 编译错误翻译、宏包分析、搜索查询改写、参考文献管理：增加“识别不了就返回空/null
  并说明原因，绝不臆造命令、DOI、文献或报错”。
- 公式 OCR、表格 OCR：增加“图片无法辨认时显式报错，不编造公式/表格”。

#### 阶段 2 完成情况（2026-08-17）

- 错字查找、表达优化、逐段翻译：压缩为“短规则 + schema + 示例”，示例覆盖
  “无修改逐字节返回 + `changes` 为空数组”与“有修改逐条对应”两种情形；表达
  优化删除了独立的 guidelines 分区，翻译合并了重复的翻译要求；示例内统一注明
  “示例语言仅作格式演示，实际输出语言以 `{language}` 为准”，避免小模型照抄
  示例语言。
- 代码审查：压缩 ISSUE TYPES / IGNORE 等冗余表述，保留格式检查豁免、prompt
  injection 防御、`needs_review=false` 时 `suggestions` 为空、original 片段
  唯一性等契约，并补充无问题 / 重复宏包两个紧凑示例。
- 写作助手：对话 system 增加核心规则（简洁、不编造 URL/引用/DOI/数据、信息
  不足时明说、回复中不能直接改文件）；编辑 system 增加分隔符协议输出示例
  （`<<<LATEX>>>...<<<END>>>` + 可选 `<<<REASON>>>`）。
- 编译错误翻译、宏包分析、搜索查询改写、参考文献管理：增加“识别不了就返回
  空/null 并明说原因，绝不臆造命令、宏包名、DOI、文献或报错”的约束；宏包
  分析明确“无法确定归属的宏包直接省略，宁缺毋滥”。
- 公式 OCR、表格 OCR：把“无法辨认”输出升级为显式失败报告——LATEX 块必须为
  空、CONFIDENCE 为 0.0，禁止用编造的公式 / 表格内容填充。
- 共享规则拆分（发现 `NO_INVENTION_RULE` 误用于生成类任务后修正）：
  `NO_INVENTION_RULE` 只保留给事实/数据提取类（bib_manager 的
  format_analysis / metadata_extraction）；新增 `GENERATION_FIDELITY_RULE`
  用于错字查找、表达优化、逐段翻译、代码审查、编译错误翻译、宏包分析、
  查询改写、BibTeX 生成、图表推荐、样式修改、写作编辑等生成类提示词，
  表述为“生成内容必须基于输入，不得虚构输入中不存在或无法推导的数据/
  事实”，避免“禁止 invent suggestions / changes”与任务输出本身矛盾。
- 回归：`tests/test_prompts.py` 新增阶段 2 契约测试（示例分区、无修改契约、
  无法识别契约、写作助手规则、生成类规则不得误用）全部通过；全量 `pytest tests/` 在干净
  临时目录下 1661 通过 / 5 跳过，唯一失败为既有的 Windows 临时文件清理用例；
  `tools/generality_gate.py check` 通过；`prompt_comprehension_eval` 全量
  26 例 PASS，与阶段 0 基线一致、无回归，明细见
  `logs/prompt_comprehension_eval/phase2_qwen_20260817.json`；共享规则拆分
  后复跑见 `logs/prompt_comprehension_eval/phase2_rule_split_qwen_20260817.json`，
  同样 26 例全 PASS。

### 阶段 3：长 Agent 提示词瘦身与拆分

这是工程量和收益都最大的部分：

1. `template_gen.agent_system`：拆成核心 system（强制工作流、失败预算、禁止臆造）
   和按 planning / validation / compile 阶段注入的阶段指令；长检查清单移入工具描述
   或参考块。
2. `plot_gen.agent_system`：压缩重复规则（如 ASCII 标签、错误处理的多处重复），
   PGFPlots 模板保持独立，按 chart_type 只注入对应模板。
3. `style_modify.agent_system`：压缩并与 `refine_system` 去重。
4. 同步修改对应 service / executor 的注入逻辑，保证行为兼容。
5. 用 `tools/generality_gate.py` 为每个 Agent system 设置体积预算。

#### 阶段 3 完成情况（2026-08-17）

- **template_gen**：`agent_system` 压缩为“核心 system”（强制工作流、失败
  预算、禁止臆造、硬约束），新增 `planning_system`（架构原则、构建安全、
  需求清单、类别 playbook）、`compile_phase_system`（编译错误恢复协议 +
  缺失资源协议）、`validation_phase_system`（视觉质量门禁 + finish 前
  检查）三个阶段块；`service.py` 在开始时注入规划块、首次编译后注入编译
  恢复协议、首次成功编译后注入质量门禁，工具链不可用时质量门禁前置；
  `_compact_messages` 保留全部 system 阶段块，避免长任务中途丢失阶段指令。
- **plot_gen**：`agent_system` 压缩重复规则（ASCII 标签、日期/对数坐标、
  失败预算、手写回退），chart 专属 spec 说明压缩但保留，PGFPlots 模板仍
  按 chart_type 独立注入；`refine_system` 改为引用会话 system 规则 + 少量
  refine 专属约束，不再重复全文。
- **style_modify**：`agent_system` 压缩项目边界、编译可用性、最小编辑范围、
  收尾完整性与工具内容规则，删除重复的编译可用性表述；`refine_system`
  引用会话 system 规则，保留“不直接编辑真实项目”等收尾契约。
- **体积门禁**：`tools/generality_gate.py` 新增 `PROMPT_SIZE_BUDGETS`
  （template_gen ≤ 22000、plot_gen ≤ 15000、style_modify ≤ 5000 字符），
  `size` 子命令在任一 Agent 模块超限时非零退出。
- **回归**：`tests/test_prompts.py` 新增阶段 3 结构测试（阶段块存在、核心
  system 体积、refine 去重、编译可用性单次出现）；`tests/test_api/
  test_template_gen.py` 新增阶段注入时机与压缩保留测试；
  `tools/test_generality_gate.py` 新增预算测试；`prompt_comprehension_eval`
  新增 planning / compile_phase / validation_phase 三个用例，已在阶段 4
  全量复跑。
- **验证**：`pytest tests/` 1672 通过 / 5 跳过，唯一失败仍为既有 Windows
  临时文件清理用例；`tools/generality_gate.py check --full` 通过；体积
  template_gen 29564 → 17597、plot_gen 15708 → 13860、style_modify
  5632 → 4959 字符。

### 阶段 4：回归、评测与文档

1. 全量运行 `pytest tests/ -v`，重点检查 `tests/test_prompts.py`、
   `tests/test_plot_gen/test_prompts.py` 和 API 测试。
2. 重跑 `tools/prompt_comprehension_eval/run_eval.py`，与阶段 0 基线逐条对比。
3. 跑错字查找、表达优化、逐段翻译的人工评测工具抽样对比。
4. 更新 `backend/core/prompts.py` 头部说明，注明当前按 Qwen3.6-35B-A3B 优化。
5. 同步更新 DEVELOPMENT.md、docs/development.md、PROJECT_SUMMARY.md 中与本计划相关
   的索引和目录树；文档中不硬编码会变化的数量。

#### 阶段 4 完成情况（2026-08-17）

- 全量 `pytest tests/ -q` 通过（`1677 passed / 5 skipped`）。阶段 4 同时修复
  Windows 下 `os.fdopen()` 接管临时文件描述符失败时无法清理临时文件的问题，
  并增加 template_gen 同轮 `compile_latex` + `finish` 的验证门禁延迟测试。
- `tools/generality_gate.py check --full` 与提示词体积预算门禁均通过；Agent
  提示词体积保持在阶段 3 预算内。
- `prompt_comprehension_eval` 完整复跑报告见
  `logs/prompt_comprehension_eval/phase4_final_20260817.json`，全部用例通过。
  评测器同时增加语义等价输出回归：接受“直接调用 finish”、完整列出首轮
  核心编译契约，以及与 ISO 日期等价的中文年月日格式，避免关键词假阴性。
- 错字查找、表达优化、逐段翻译分别抽样对照阶段 0 基线，报告位于各自
  `logs/*_eval/phase4_final_20260817/` 目录；命中、漏检、误报与错误指标均未回退。
- `backend/core/prompts.py` 头部模型说明和开发文档索引、目录树、提示词结构说明
  已同步更新。

## 验收标准

- `prompt_comprehension_eval` 相对基线无回归，Marker 保留、JSON-only、无修改契约、
  分隔符协议、语言注入等关键用例通过率不低于基线或显著提升。
- template_gen / plot_gen / style_modify 的闭环评测工作区通过，
  `tools/generality_gate.py check` 通过。
- `pytest tests/ -v` 全绿。
- 文档与代码一致，术语符合项目文档规范。

## 风险与决策点

- **Agent 拆分回归面较大**：先做纯压缩，再按需拆分；每一步都单独跑评测。
- **示例增加 token 开销**：示例长度应远小于被删除的冗余规则；以评测数据为准，避免
  为加示例而加示例。
- **模型参数微调**：temperature、max_tokens、thinking 模式等参数调整不属于本计划
  范围，若需要可另行评估。
- **不为改而改**：若某模块评测已稳定且提示词不长，保持现状。
