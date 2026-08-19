# LateXcellence 提示词优化计划

> 状态：阶段 0 至阶段 4 已完成；阶段 5 审查已完成，计划 A、B、C、D、E 已实施。
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

### 阶段 5：提示词工程审查与上下文工程计划

#### 审查范围与结论（2026-08-19）

对以下内容进行静态审查：

- `backend/core/prompts.py` 中的集中式提示词、共享规则和阶段提示词；
- 提示词调用方的上下文组装、会话压缩和工具调用流程；
- `backend/modules/writing_chat/context.py` 的文件上下文截取；
- `backend/utils/project_tools/`、`backend/utils/web_search/` 和学术检索工具的
  schema、结果截断与传输格式；
- `tests/test_prompts.py`、提示词结构检查脚本和
  `tools/prompt_comprehension_eval/run_eval.py --dry-run`。

总体判断：当前项目已经达到“工程化、可用且可靠性约束较完整”的水平，但还没有完全
达到“最小充分结构 + 完整上下文生命周期管理”的目标。强项是输出契约、安全边界和
Agent 阶段拆分；短板是上下文元数据、长文档检索策略、高风险任务示例、自定义提示词
隔离、Prefill 以及工具分页协议。

#### 已达标或基本达标的能力

1. **清晰直接**：大多数任务以明确角色和动作型任务开头，直接说明要查找、改写、翻译、
   分析或生成什么。
2. **角色设定**：常规模块和 Agent 均有明确的角色定义，任务边界较清楚。
3. **XML 分区**：常规结构化提示词已统一使用 `<role>`、`<task>`、`<critical_rules>`、
   `<input>`、`<output_format>` 和 `<examples>`；Agent 按项目规则使用阶段提示词，
   已避免把所有流程合并成一个 system prompt。
4. **可靠输出**：Marker 保护、JSON-only、Pydantic 校验、失败重试、LaTeX×JSON 防护、
   无修改契约和分隔符协议已经形成端到端约束链。
5. **不确定性控制**：多个提取、搜索和分析任务明确要求使用 `unknown`、空值或空列表，
   信息不足时说明原因，不虚构 DOI、URL、引用、数据、宏包或修复命令。
6. **Prompt chaining**：`template_gen` 已按 planning、compile、validation 阶段注入；
   `plot_gen` 和 `style_modify` 也有独立的 refine 阶段；写作助手具备上下文压缩和恢复
   机制。
7. **工具基础设计**：工具目的基本独立，参数名称较明确，项目文件有后缀白名单和行范围
   读取，联网搜索有结果数、字符数和页面内搜索限制，网页结果会标记为不可信数据。

#### 本轮发现的问题

##### P0：上下文和安全契约

1. **写作助手上下文缺少文档元数据和引用协议**。

   `backend/modules/writing_chat/context.py` 当前以 `[Paragraph n]` 拼接原始内容；写作
   system prompt 只有一个泛化的 `<context>` 包装。上下文没有稳定的 `source`、
   `document_content`、文件路径和章节层级，也没有要求模型先引用相关段落再分析，导致
   文档级问题的依据不够显式。

2. **全文上下文的截取策略可能丢失相关内容**。

   未指定段落时，当前实现只取文件开头的有限段落；超出预算的部分直接省略。指定段落
   时虽然会取目标段落及相邻段落，但不支持跨较远距离检索。长文档后半部分的相关信息
   可能因此从未进入模型上下文。

3. **自定义提示词可能覆盖核心契约**。

   `expression_opt` 将自定义指令描述为优先于一般规则；写作助手还会把用户配置的助手
   提示词直接拼入 system prompt。当前没有统一声明“自定义内容只能影响风格和偏好，不能
   覆盖 Marker、输出 schema、源文件保护、安全规则或工具授权”。这会让自定义内容和
   系统安全边界的优先级不够清晰。

##### P1：输出控制与任务可靠性

4. **结构化任务主要依赖 JSON-only，没有真正的 Prefill 或原生 schema 输出**。

   当前已有后端解析、Pydantic 校验和重试，这些是必要的后验防线，但没有发现统一的
   `{` 预填充或 API 原生 JSON schema / response format 策略。模型仍可能先产生格式噪声，
   再由后端恢复。

5. **Few-shot 示例覆盖不均衡**。

   错字查找、表达优化、代码审查、翻译、查询改写和宏包分析已有较好的正反例；公式 OCR、
   表格 OCR、编译错误翻译、BibTeX 生成、元数据提取和部分 Agent 提示词缺少正常成功、
   缺失信息或无法判断的示例。规则越多而示例越少，越容易出现边界行为不一致。

6. **普通高风险任务缺少独立的轻量复核阶段**。

   逐段检查、翻译和表达优化主要采用一次生成加 JSON 校验。后端已有结构校验，但对过度
   改写、翻译遗漏、主字段与 changes 不一致等语义问题，仍主要依赖单次模型输出。

##### P1：工具和结果上下文

7. **工具有截断上限，但没有统一分页或 continuation 协议**。

   `list_files`、`search_across_files` 和部分学术检索结果有数量限制，网页结果有字符预算，
   但结果未统一返回 `truncated`、`next_cursor` 或下一步检索建议。模型可能无法判断结果
   是完整集合还是被截断的集合。

8. **文件读取工具在省略行范围时仍可能返回过大的文件内容**。

   虽然支持按行读取，但默认读到文件末尾；对大型 `.tex`、`.bib` 或 Markdown 文件，模型
   可能一次接收低信号的大段内容，增加上下文压力。

9. **工具结果缺少统一的高信号元数据**。

   不同工具的纯文本结果在来源、行号、结果总数、截断状态和后续动作上的表达不一致，
   模型需要自行解析这些状态。

##### P2：结构和维护性

10. **Agent 阶段提示词的语义分区仍不够统一**。

    Agent 拆分本身是正确方向，但 workflow、failure budget、hard constraints、tool
    protocol 和 quality gate 主要使用普通文本标题，后续可在不增加总长度的情况下用 XML
    标签加强边界。

11. **长规则与示例的取舍还没有完全由失败模式驱动**。

    当前重点已经从“一个超长 system prompt”转向“阶段注入”，但仍应优先删除重复规则，
    再依据评测失败添加最小示例，避免为了满足示例数量而继续扩大提示词体积。

#### 后续实施计划

##### 计划 A：自定义指令隔离与安全优先级（P0） ✅

1. 为 expression_opt 和 writing_chat 统一增加 `<custom_instructions>` 包装。
2. 明确自定义内容只能改变风格、语气、术语偏好和非安全性的任务偏好。
3. 明确自定义内容不得覆盖 Marker 保护、JSON / 分隔符协议、无修改契约、源文件保护、
   不虚构规则和工具授权。
4. 为自定义指令加入不可信数据边界，避免其被误解为更高优先级的系统规则。
5. 增加提示词结构测试和至少一个覆盖"自定义指令要求破坏输出契约"的回归用例。

###### 计划 A 完成情况（2026-08-19）

- 新增共享常量 `CUSTOM_INSTRUCTIONS_ISOLATION_RULE`，明确自定义指令仅影响风格、
  语气、术语偏好，绝不能覆盖 Marker 保护、JSON-only、分隔符协议、无修改契约、
  源文件保护、不虚构规则和工具授权；系统规则始终优先。
- `expression_opt` 的 `<custom_instructions>` 块移除旧的"take priority"措辞，
  替换为隔离规则，并标注用户内容为不可信数据。
- `writing_chat` 的助手指令注入从原始字符串拼接改为 `<custom_instructions>`
  包装，同样标注不可信数据边界并注入隔离规则；无助手指令时不生成该块。
- `check_prompt_structure.py` 的 `SHARED_NAMES` 已同步注册新常量。
- 回归：`tests/test_prompts.py` 新增 6 个阶段 5 测试全部通过；
  `prompt_comprehension_eval --dry-run` 29 例通过；`check_prompt_structure.py`
  通过；全量 `pytest tests/ -q` 1755 通过 / 5 跳过，无回归。

##### 计划 B：写作助手长上下文改造（P0） ✅

1. 将文件上下文改为稳定的 XML 信封，例如 `<documents>`、`<document>`、`<source>`、
   `<document_content>` 和带索引的段落标签。
2. 保留文件路径、段落索引、章节信息和上下文截断状态等元数据。
3. 在写作 system prompt 中要求模型先定位并引用相关段落，再回答文档相关问题。
4. 将"文件开头固定截取"改为"目标段落锚点 + 相关内容检索 + 明确预算"的策略；不能
   获取完整相关内容时，明确告诉模型上下文不完整。
5. 为文档后半部分、跨段落问题、无相关命中和截断状态增加评测语料。

###### 计划 B 完成情况（2026-08-19）

- `context.py` 的 `build_file_context_text` 改为 XML 信封格式：`<documents>` →
  `<document source="path">` → `<total_paragraphs>` + `<document_content>` →
  `<paragraph index="N">` + 可选 `<context_note>`（截断状态）。
- 无指定段落时：从 head（前 3 段）、middle（中间 3 段）、tail（后 3 段）采样，
  覆盖全文；段落总数 ≤ 9 时全量展示。旧策略为固定取文件开头最多 12 段，长文档
  后半部分系统性丢失。
- 指定段落时：以该段为锚点取 ±2 范围（旧策略为 ±1），提供更多上下文。
- `prompts.py` 的 `chat_system` 新增 `<context>` 区的 XML 格式说明、段落引用协议
  （"先定位段落编号，再引用"）和截断处理指令（使用工具检索额外段落或声明上下文
  不完整）。
- `tests/test_prompts.py` 新增 4 个阶段 5 计划 B 测试（XML 格式描述、引用协议、
  截断处理说明、核心契约不变）。
- `tests/test_api/test_writing_chat.py` 新增 `TestContextXmlEnvelope` 类，覆盖 XML
  信封结构、截断注释、错误状态、XML 转义、锚点选择（±2 半径、边界不越界）、
  head/middle/tail 采样（小文件不截断、大文件截断）、`paragraph_index=-1` 返回空、
  指定段落返回锚点范围段落等场景。
- `tools/prompt_comprehension_eval/run_eval.py` 新增 2 个评测用例：
  `writing_chat.doc_reference_protocol`（模型应引用段落编号）和
  `writing_chat.truncated_context_awareness`（模型应识别截断状态）；现有
  `writing_chat.chat_system` 用例的 context_block 升级为 XML 格式。
- `docs/modules/writing_chat.md` 的上下文锚点说明已同步更新。
- 回归：`tests/test_prompts.py` 48 通过；`tests/test_api/test_writing_chat.py`
  60 通过；全量 `pytest tests/ -q` 1770 通过 / 5 跳过，无回归；
  `tools/generality_gate.py size` 与 `check --full` 通过；
  `prompt_comprehension_eval --dry-run` 31 例通过（含 2 新增）。

##### 计划 C：结构化输出和示例补齐（P1） ✅

1. 盘点各 LLM 调用是否支持原生 JSON schema 或 response format；支持时优先使用原生
   结构化输出，同时保留 `retry_with_validation()` 作为后备校验。
2. 对稳定 JSON 任务评估 Prefill；如果底层客户端不支持，则不以提示词中的“JSON only”
   冒充 Prefill 能力。
3. 优先为 formula_ocr、table_ocr、compile_error_translator、metadata_extraction 和
   bibtex_generation 增加紧凑示例。
4. 每个新增示例至少覆盖一个正常案例和一个缺失信息、无法判断或无需修改案例；示例必须
   使用真实边界，不能加入部署环境或评测场景硬编码。
5. 为表达优化和翻译增加确定性检查或轻量复核，优先检查遗漏、过度改写、Marker 和主字段
   一致性，不默认增加第二次 LLM 调用。

###### 计划 C 完成情况（2026-08-19）

1. **盘点原生 JSON schema / response_format 支持**：所有稳定 JSON 任务目前统一使用
   `response_format={"type": "json_object"}`（OpenAI 兼容客户端 legacy JSON 模式）。
   SDK 版本 openai 2.44.0 支持 `{"type": "json_schema"}` 结构化输出，但 TJU 端点
   （`deepseek-flash`，自定义 OpenAI 兼容实现）未确认支持原生 schema 校验；因此保持现状，
   继续以 `retry_with_validation()` + Pydantic 校验作为唯一防线，不向端点下发
   `json_schema` 类型（避免不支持时整链路失败）。
2. **Prefill 评估**：底层客户端与端点均无原生 Prefill 支持，不在提示词中以“JSON
   only”冒充 Prefill 能力；现有 `JSON_ONLY_RULE` 仅声明输出格式，语义正确。
3. **示例补齐**：为 formula_ocr、table_ocr、compile_error_translator、
   metadata_extraction、bibtex_generation 五个模块各增加紧凑 `<examples>`：
   - formula_ocr / table_ocr：正常识别（含真实 LaTeX 结构）与“无法辨认返回空块 +
     CONFIDENCE 0.0”各一例，按分隔符协议书写。
   - compile_error_translator：正常错误（演示 JSON 双重转义 `\\usepackage`）与
     “仅警告无法定位根因 → fixes 为空数组”各一例，并注明示例语言仅作格式演示、
     实际输出语言由 `{language}` 控制。
   - metadata_extraction：全部字段命中与 DOI 缺失返回空字符串各一例。
   - bibtex_generation：完整元数据与缺失可选字段（raw_bibtex 中不含臆造字段）
     各一例，示例内换行按 JSON 字符串 `\n` 转义书写。
   示例均使用真实边界（真实论文元数据、真实 LaTeX 结构），不加入部署环境或评测
   场景硬编码。
4. **确定性检查 / 轻量复核**：`paragraph_translate` 复用 expression_opt / typo_check
   已有的 changes 契约校验——`normalize_changes_contract()`（主字段与原文一致但
   changes 非空 → 清空虚假条目）与 `has_unlisted_changes()`（主字段已修改但
   changes 为空 → 标记 `unlisted_changes=true`），结果字段同步输出
   `unlisted_changes`；不新增第二次 LLM 调用。expression_opt 此前已具备这两项检查。
5. **回归**：`tests/test_prompts.py` 新增 5 个计划 C 测试（五模块示例存在、
   OCR 正常/失败双例、编译翻译正常/无法识别双例 + JSON 双重转义、元数据正常/
   缺失双例、BibTeX 正常/缺失双例）；`tests/test_api/test_paragraph_translate.py`
   新增 2 个确定性校验测试；`check_prompt_structure.py` 通过；
   `prompt_comprehension_eval --dry-run` 31 例通过；`generality_gate.py size` /
   `check --full` 通过（Agent 提示词未改动）；全量 `pytest tests/ -q`
   1777 通过 / 5 跳过，无回归。

##### 计划 D：工具结果协议统一（P1） ✅

1. 为文件搜索、文件列表和学术检索设计统一的截断字段和 continuation 语义。
2. 对无法一次返回完整结果的工具，返回明确的 `truncated` 状态、后续查询方式或 cursor。
3. 为 `read_file_content` 设置字符或行预算，并在结果中提供文件、行范围和截断信息。
4. 统一工具结果的来源、行号、数量、截断状态和下一步动作表达。
5. 更新工具 schema 描述，使模型知道何时缩小范围、继续读取或换用搜索工具。

###### 计划 D 完成情况（2026-08-20）

- 新增共享模块 `backend/utils/tool_result_protocol.py`：统一状态块构建器
  `format_result_status()`，固定字段 `shown` / `total` / `truncated` /
  `next_step`（可选 `source` 与附加说明行）。工具正文保持原有语言，
  末尾追加该状态块，模型据此判断结果是否完整及下一步动作。
- **list_files**：单次返回上限 200 个文件，超出时截断并标注
  `truncated: yes`、`total` 与"缩小 pattern 范围"提示。
- **read_file_content**：未显式指定 `end_line` 时按 200 行预算读取，
  超限截断并在 `next_step` 给出下一段起始行（`start_line=N`）；单行
  超过 500 字符截断并在状态块附注说明；头部保留文件、行范围与字符数。
- **search_across_files**：匹配总数统计上限 1000（`total` 未知时显示
  `?`），结果被截断时明确 `truncated: yes` 并提示用更精确的
  pattern / file_pattern 缩小范围或调高 `max_results`；每条匹配保持
  `文件:行号` 前缀。
- **学术检索**：`SearchScholarlyResult` / `RelatedWorksResult` 的
  `format_for_llm()` 增加统一状态块，`total_estimated` / `total` 大于
  展示条数时标记 `truncated: yes` 并提示细化查询或增大 `max_results`；
  覆盖 `search_scholarly_works`、`find_related_works`、
  `search_research_data_resources` 三个工具。
- **schema 描述**：`list_files` / `read_file_content` /
  `search_across_files` 与学术检索三个可截断工具的描述补充截断语义与
  继续检索方式。
- **提示词接入**：`prompts.py` 新增共享常量 `TOOL_RESULT_PROTOCOL_RULE`，
  注入 `writing_chat.chat_system` 的 `<context>` 与
  `scholarly_research_tool_guide`，指导模型读取状态块并遵循 `next_step`；
  `check_prompt_structure.py` 的 `SHARED_NAMES` 已同步注册。
- **回归**：`tests/test_prompts.py` 新增 5 个计划 D 测试（共享规则字段、
  chat_system / 学术指南注入、项目工具与学术工具 schema 截断描述）全部
  通过；`tests/test_utils/test_project_tools_manager.py` 新增 list 截断、
  read 行预算 / 显式行范围 / 长行截断、search 截断状态等 8 个测试并
  修正 2 个受格式变化影响的既有断言；`tests/test_utils/
  test_scholarly_research.py` 新增 3 个截断状态测试；
  `check_prompt_structure.py` 通过；`prompt_comprehension_eval --dry-run`
  31 例通过；全量 `pytest tests/ -q` 1792 通过 / 5 跳过，无回归。

##### 计划 E：Agent 分区和最小充分信息治理（P2） ✅

1. 在不合并阶段提示词的前提下，将 Agent 中的 workflow、quality gate、tool protocol
   和 failure budget 改为一致的 XML 分区。
2. 使用现有体积门禁，先删除重复约束，再根据闭环评测的失败模式增加规则或示例。
3. 为每个阶段记录"进入条件、输入、输出、完成条件和失败回退"，避免只依赖自然语言
   中的顺序描述。
4. 保持阶段提示词延迟注入，避免把只在特定阶段需要的信息提前放入所有请求。

###### 计划 E 完成情况（2026-08-20）

- **统一 XML 分区**：三个 Agent 的核心/常驻 system 均把 workflow、quality gate、
  tool protocol、failure budget 拆成成对闭合的 XML 分区：
  - template_gen `agent_system`：`<workflow>`（首回复必带 tool_call、walking
    skeleton、逐步重编译、finish 时机）、`<quality_gate>`（压缩版，指向
    validation 阶段注入的详细清单）、`<failure_budget>`、`<tool_protocol>`。
  - plot_gen `agent_system`：输入数据包为 `<data_profile>` / `<chart_plan>` /
    `<chart_style>`，规则部分统一 `<style_note>` / `<figure_text_language>` /
    `<axis_guidance>` / `<layout_rules>` / `<outputs>` / `<tikz_protocol>` /
    `<workflow>` / `<statistics>` / `<quality_gate>` / `<failure_budget>`。
  - style_modify `agent_system`：`<project_boundary>` / `<compile_availability>` /
    `<minimal_edit>` / `<finish_integrity>` / `<workflow>` / `<tool_protocol>`。
- **阶段契约（stage_contract）**：为每个阶段块（template_gen 的 planning /
  compile_phase / validation_phase，plot_gen 与 style_modify 的 refine_system）
  增加 `<stage_contract>`，统一记录 `entry`（进入条件）、`input`（输入）、
  `output`（输出）、`done`（完成条件）、`fallback`（失败回退），不再只依赖自然
  语言顺序描述。
- **去重**：compile_phase 的缺失资源协议删除与核心 system 重复的"禁止创建辅助
  测试文档"；quality gate 摘要不再完整重复 validation 阶段的详细清单；plot_gen
  refine 删除被 stage_contract fallback 覆盖的重复修复规则；style_modify 合并
  finish integrity 中重复的"逐条核对需求"表述与 refine 中重复的编译段落。
- **体积控制**：分区重构后 `tools/generality_gate.py size` 全部通过：template_gen
  17597 → 19370、plot_gen 14089 → 14773、style_modify 4940 → 4994（均在预算
  22000 / 15000 / 5000 内）。style_modify 通过压缩冗余文本抵销分区与阶段契约的
  增量。
- **回归**：`tests/test_prompts.py` 新增 7 个计划 E 测试（四分区存在且成对闭合、
  五阶段块均含 stage_contract 五字段、阶段块内部分区平衡、plot_gen / style_modify
  分区、编译阶段去重、体积预算仍在预算内）全部通过；`check_prompt_structure.py`
  通过；`prompt_comprehension_eval --dry-run` 31 例通过；`generality_gate.py size`
  与 `check --full` 通过；全量 `pytest tests/ -q` 1799 通过 / 5 跳过，无回归。

#### 实施顺序与回归要求

实施顺序为：计划 A → 计划 B → 计划 C / D → 计划 E。每个计划单独提交、单独评测，
不在同一轮同时改变规则、上下文格式和工具结果格式，以便定位回归原因。

每次提示词或上下文协议变更必须执行：

1. `python -m pytest tests/test_prompts.py -q`；
2. `python tools/prompt_comprehension_eval/run_eval.py --dry-run`；
3. 有真实评测凭据时，运行对应模块的 comprehension / 人工评测，并与阶段 0 或最近一次
   基线逐项比较；
4. Agent 提示词变更时运行 `python tools/generality_gate.py size` 和
   `python tools/generality_gate.py check --full`；
5. 相关工具、写作助手和会话压缩变更时运行对应测试，再执行完整 `pytest`；
6. 检查提示词中的 Marker、JSON / 分隔符契约、语言注入和不确定性行为没有回退。

#### 阶段 5 验收标准

- 自定义提示词不能覆盖核心安全规则、输出协议和源文件保护契约。
- 文档上下文包含稳定来源元数据，并能显式表示相关段落和截断状态。
- 长文档测试不因固定取文件开头而系统性遗漏后部相关内容。
- 高风险结构化任务至少有正常与缺失信息两类示例，且示例不会破坏模板转义规则。
- 工具结果能明确表达完整、截断和继续检索状态。
- Prefill / 原生结构化输出只有在客户端真实支持并通过回归后才纳入正式方案。
- 提示词理解评测、Marker 保留、JSON 校验、无修改契约和 Agent 通用性门禁不低于最近
  基线；发现回退时暂停该阶段并保留失败样本。

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
