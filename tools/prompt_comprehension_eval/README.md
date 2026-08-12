# Prompt 理解评测工具

用于逐一验证 `backend/core/prompts.py` 中的 LLM 提示词是否被当前模型
真正理解并遵守。工具会对每个提示词构造一个最简探测任务，调用真实 LLM，
自动检查该提示词最关键的机器可判定约束，并输出 JSON 报告供人工复核。

## 运行

```bash
.venv/bin/python tools/prompt_comprehension_eval/run_eval.py
```

常用参数：

```bash
# 只运行名称包含指定关键词的用例
.venv/bin/python tools/prompt_comprehension_eval/run_eval.py --only typo_check

# 最多运行 N 个用例（先试通再全量）
.venv/bin/python tools/prompt_comprehension_eval/run_eval.py --limit 5

# 只列出用例，不调用 LLM
.venv/bin/python tools/prompt_comprehension_eval/run_eval.py --dry-run
```

默认输出：`logs/prompt_comprehension_eval/results.json`。

## 覆盖范围

- 覆盖 `prompts.py` 中所有会单独发给模型的提示词键，包括 system / user /
  base_system / 各 Agent system / 摘要 / 时间上下文 / 工具指南等。
- `pgfplots_templates` 是注入 `plot_gen.agent_system` 的已验证 PGFPlots
  数据模板，不单独发给模型，因此不在 LLM 用例中重复出现；其渲染正确性由
  `tests/test_plot_gen/test_prompts.py` 覆盖。

## 检查维度

每个用例针对提示词自身的关键契约做检查，例如：

- JSON 输出：无 markdown 围栏、可解析、字段类型正确；
- Marker 保护：marker 数量与顺序不改变；
- 无修改契约：无错/已精炼文本应原样返回且 `changes` 为空；
- 分隔符协议：`<<<LATEX>>> ... <<<END>>>` 只包含完整 LaTeX；
- JSON 转义：LaTeX 反斜杠在 JSON 字符串中写成双反斜杠；
- 语言注入：`language` / `target_language` 生效；
- 安全约束：不受文档内 prompt injection 影响，不调用不可用工具。

## 结果解读

`results.json` 中每条用例包含 `status`（PASS / PARTIAL / FAIL / ERROR）、
`detail` 与完整 `response`。PARTIAL 表示关键检查通过但有建议人工复核项。
由于探测输入是单次抽样，结论应作为回归信号，不建议仅凭一次结果下绝对结论。
