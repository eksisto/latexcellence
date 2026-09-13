# Agent 提示词阶段注入时序

## template_gen 四段结构

| 提示词键 | 注入时机 | 职责 | 注入次数 |
| --- | --- | --- | --- |
| `agent_system` | 会话开始，作为唯一 system 消息 | 角色、强制工作流、失败预算、硬约束、生成保真 | 1 |
| `planning_system` | 初始 user 消息之后追加 system 消息 | 架构角色模型、构建安全优先级、类别 playbook | 1 |
| `compile_phase_system` | 第一次调用 compile_latex 之后（无论成功/失败） | 错误恢复协议，指导后续修复轮 | 1 |
| `validation_phase_system` | 第一次成功编译之后；工具链不可用时前置到开局 | 视觉质量门禁，finish 前必须执行 | 1 |

代码位置：`backend/modules/template_gen/agent/service.py`。

## finish 延迟机制

- 同一轮刚注入 `validation_phase_system` 时调用 finish，执行器返回 `Finish deferred: review the newly injected validation guidance, then call finish in the next round.`，本轮不结束，确保质量门禁先被执行。
- 最后一次编译仍失败时静默 finish 会被拒绝，并向前端推送 warning。
- style_modify 的 finish 有同类约束：overlay 在上次 shadow 编译之后发生变化时 finish 被拒绝（stale finish），必须先重新编译。

## 修改规则

- 新增阶段内容必须写进对应 phase 键，按事件注入，不要全部塞进 `agent_system`。
- 禁止把四个阶段合并回单个超长 system：会触发 `tools/generality_gate.py size` 预算超限（template_gen 上限 22000 字符）。
- 各 phase 同样是 `get_prompt` 模板：字面花括号写 `{{ }}`，占位符如 `{max_rounds}`、`{compile_enabled}` 必须由调用方传参。
- 核心 system 与各阶段块内使用一致的 XML 分区：`<workflow>` / `<quality_gate>` / `<failure_budget>` / `<tool_protocol>`；每个阶段块顶部带 `<stage_contract>`（entry / input / output / done / fallback 五字段）。分区必须成对闭合，`tests/test_prompts.py` 的计划 E 测试会校验。
- 修改后运行：`pytest tests/test_prompts.py -q`、`python tools/generality_gate.py size`、`python tools/generality_gate.py check --full`、`python tools/prompt_comprehension_eval/run_eval.py --dry-run`。

## style_modify / plot_gen

- 两模块使用 `agent_system` + `refine_system` 两段：`agent_system` 是常驻核心，`refine_system` 在用户追加优化指令时以 user 消息注入（`{refine_prompt}\n\nUser refinement: {message}`）。
- 关键约束（写入 agent_system）：最小编辑范围、shadow 编译、finish 完整性（stale finish 拒绝）、工具链不可用时的降级路径。
