# style_modify 样式修改 Agent 优化报告

> 日期：2026-08-11 ｜ 测试环境：Python 3.13 / deepseek-flash 端点 / Linux 评测沙箱
> TeX Live 2026
> 迭代：基线 → opt1 → opt2 → opt3 → opt4

## 1. 评测场景与工具

在 `tools/stylemodify_opt/` 新建了 style_modify Agent 的离线优化闭环，与
`tools/plotgen_opt/`、`tools/templategen_opt/` 同模式。5 个可复现场景：

| 场景 | 说明 | 核心检查 |
| --- | --- | --- |
| s1 主题色替换 | article + `.sty` 主题色 #333333 → #1F4E79 | 新色命中、旧色移除、样式文件被改、正文保留 |
| s2 章节标题格式 | titlesec 标题改深蓝加粗 + 方块 | 新色、titleformat、方块装饰、旧色移除、正文保留 |
| s3 三线表 | 普通 `tabular` → booktabs 三线表 | booktabs、三线命令、表头加粗、列左对齐、旧横线移除 |
| s4 Beamer 主题色 | 自定义 theme 改深蓝 + 橙强调色 | RGB 命中、旧色移除、theme 文件被改、frame 保留 |
| s5 文档类选项 | `.cls` 11pt→12pt、A4、边距 2cm | 12pt、a4paper、2cm、旧选项移除、正文保留 |

每个场景还执行 proposal 应用 + 撤销事务测试，并检查 `allowed_targets`（只允许
改样式相关文件）与 `preserve_markers`（正文/公式/表格数据不得丢失）。

## 2. 优化前后对比

| 场景 | 基线（分/轮/秒） | opt1（分/轮/秒） | opt2（分/轮/秒） | opt3（分/轮/秒） | opt4（分/轮/秒） |
| --- | --- | --- | --- | --- | --- |
| s1 主题色替换 | 80 / 6 / 9.8 | 85 / 5 / 7.5 | 85 / 5 / 9.0 | 100 / 6 / 9.0 | 100 / 6 / 11.6 |
| s2 章节标题格式 | 74 / 7 / 11.2 | 85 / 5 / 7.3 | 85 / 5 / 7.8 | 100 / 6 / 11.6 | 100 / 6 / 11.2 |
| s3 三线表 | 80 / 6 / 10.5 | 85 / 4 / 9.3 | 85 / 4 / 7.9 | 100 / 6 / 11.1 | 100 / 6 / 11.8 |
| s4 Beamer 主题色 | 80 / 5 / 8.2 | 85 / 4 / 6.4 | 85 / 4 / 5.8 | 100 / 5 / 8.0 | 100 / 5 / 11.0 |
| s5 文档类选项 | 75 / 5 / 7.3 | 80 / 4 / 5.9 | 85 / 4 / 6.2 | 100 / 5 / 7.3 | 100 / 9 / 17.8 |
| 汇总均值 | 77.8 / 5.8 / 47.0s | 84.0 / 4.4 / 36.4s | 85.0 / 4.4 / 36.8s | 100.0 / 5.6 / 47.0s | 100.0 / 6.4 / 63.4s |

汇总指标：

| 指标 | 基线 | opt1 | opt2 | opt3 | opt4 |
| --- | --- | --- | --- | --- | --- |
| 平均启发式质量分 | 77.8 | 84.0 | 85.0 | 100.0 | 100.0 |
| 平均轮数 | 5.8 | 4.4 | 4.4 | 5.6 | 6.4 |
| 总时长 | 47.0s | 36.4s | 36.8s | 47.0s | 63.4s |
| 需求遵守全命中场景 | 3/5 | 4/5 | 5/5 | 5/5 | 5/5 |
| 内容保留全命中 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| proposal 应用/撤销 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| 总 warning | 5 | 0 | 0 | 0 | 0 |
| 总 error | 0 | 0 | 0 | 0 | 0 |
| shadow 编译成功 | 0/5（基线阶段未接 TeX PATH） | 0/5 | 0/5 | 5/5 | 5/5 |

> 基线/opt1/opt2 在接入 Linux TeX PATH 前运行，因此编译分缺失；opt3/opt4 接入
> `/usr/local/texlive/2026/bin/x86_64-linux` 后全部场景编译通过，达到满分 100。

## 3. 诊断出的问题

### P1（最严重）：finish 摘要与实际 staged 内容不一致

- 现象：s5 基线 stage_replace 只改了 12pt 和边距，overlay 中根本没有 `a4paper`，
  但 finish 摘要声称“如需 A4 将补充该选项”，产生虚假交付。
- 根因：提示词只要求“保留内容”，没有要求 finish 前按字面核对需求是否全部落在
  overlay，也没有约束摘要只能描述已 staged 的变更。

### P2：替换主题色时保留旧定义

- 现象：s2 基线新增 `sectioncolor=1F4E79`，但旧的 `themecolor=222222` 仍留在
  `.sty` 中，产生歧义与潜在误用。
- 根因：提示词缺少“替换旧主题定义，不留陈旧颜色”的明确约束。

### P3：无 TeX 环境下的编译循环与误报

- 现象：`compile_enabled` 只按配置超时判断，环境实际没有 latexmk 时仍显示
  available；s2 基线连续调用 2 次编译；`not_run` 被当成“编译失败”发出
  “last shadow compilation failed”警告。
- 根因：Agent 未检测真实工具链，编译状态语义区分不清。

## 4. 代码改动清单

### 4.1 `backend/modules/style_modify/agent.py`

- 新增 `_tex_toolchain_available()`：检查 `latexmk` + `pdflatex/xelatex/lualatex`
  是否真实存在，`run`/`refine` 的 system prompt 按结果注入
  `compile_enabled=available|unavailable`。
- `compile_staged_project` 在工具链缺失时提前短路，返回 `not_run` 与明确
  “不要再次调用”指引，不再进入 shadow 构建流程。
- `compile_state` 增加 `status`，只有真实 `failed` 才触发“最后编译失败”警告；
  工具链缺失且未调用编译时不再误报“未运行 shadow 编译”。
- `compile_staged_project` 工具 schema 注明 `not_run` 时不要重试。

### 4.2 `backend/core/prompts.py`

`style_modify.agent_system` 新增：

- `COMPILE AVAILABILITY`：unavailable 时禁止调用编译，摘要必须说明编译未验证。
- `MINIMAL EDIT SCOPE`：只允许样式相关行变化；替换主题色时删除旧定义；diff 含
  无关编辑时重新 stage 或 discard。
- `FINISH INTEGRITY`：finish 前逐条核对需求；摘要只描述已 staged 内容；按字面
  验证纸张/颜色/字号（article 默认 letterpaper，A4 必须显式写 `a4paper`）。

`style_modify.refine_system` 同步注入 `compile_enabled`，unavailable 时禁止编译。

### 4.3 测试

- `tests/test_api/test_style_modify_agent.py`：编译上限测试补 `_tex_toolchain_available`
  mock，保持原语义。
- 新增 `test_compile_unavailable_short_circuits_without_failed_warning`：工具链缺失时
  不执行真实编译、`not_run` 不误报为失败、仍产出 proposal。

### 4.4 新增强化工具

- `tools/stylemodify_opt/`：场景定义、运行器、trace 分析、多 tag 对比、README、
  本报告，全部入库；`results/`、`runtime/`、`data/projects/` 不入库。
- 运行器新增 TeX 自动发现：扫描 `/usr/local/texlive/*/bin/*/latexmk` 并加入
  PATH，避免 shell PATH 未配置时误判工具链不可用；评分权重修正为满分 100。

## 5. 本轮闭环复盘（2026-08-18）：显式需求完成闸门

### 背景与基线

提示词重构后的完整基线中，常规场景均通过，但 holdout 的文档类选项场景出现
“字号和边距已改、A4 作为可选项未写入”的部分完成 proposal；模型甚至在摘要中
承认未显式设置 A4，仍调用 `finish`。基线五场景均值为 99/100，需求全命中为 4/5。

### 本轮改动

| 改动 | 文件 | 为什么通用 |
| --- | --- | --- |
| 强化显式需求优先于最小修改的提示词约束，并要求逐项核对 staged 内容 | `backend/core/prompts.py` | 防止“最小修改”被解释为可以省略用户明确要求 |
| finish 前拒绝承认未完成、未来再添加、或以默认值替代显式设置的样式摘要 | `backend/modules/style_modify/agent.py` | 在 LLM 自检不可靠时，阻止部分完成 proposal 进入交付阶段 |
| 增加不误判正文未变动的单元测试 | `tests/test_api/test_style_modify_agent.py` | 固化交付闸门的正例与负例 |
| 同步记录 Agent finish 校验行为 | `docs/modules/style_modify.md` | 让模块文档反映实际交付流程 |

### 结果

| 场景 | 基线 | opt4 | 编译 | 需求 |
| --- | --- | --- | --- | --- |
| s1_theme_color | 100 | 100 | ✓ | 4/4 |
| s2_section_format | 100 | 100 | ✓ | 5/5 |
| s3_table_style | 100 | 100 | ✓ | 5/5 |
| s4_beamer_color | 100 | 100 | ✓ | 4/4 |
| s5_class_options（holdout） | 95 | 100 | ✓ | 6/6 |

opt4 完整评测平均启发式分 **99.0 → 100.0**，平均轮数 5.8 → 6.4；内容保留、proposal
应用/撤销和编译全部通过。对照组无回退，Generality Gate PASS。

### 经验教训

- 提示词要求“逐项核对”不能完全替代确定性闸门；模型可能用“默认值已满足”重新解释
  显式要求。
- finish 摘要是低成本的最后一致性信号；检测未完成承诺后拒绝交付，能促使 Agent
  重新 stage 并编译，而不需要把某个场景答案写进生产逻辑。
- 闸门必须避开“正文和 label 未变动”这类成功的保留声明，测试需要同时覆盖误判排除。

### 验证

- `python tools/stylemodify_opt/run_stylemodify.py opt4 --include-holdout`
- `python tools/stylemodify_opt/compare.py baseline_full opt4`：PASS
- `python tools/generality_gate.py size`：`style_modify_chars=4940`，PASS
- `python tools/generality_gate.py check --full`：PASS
- `python -m pytest tests/ -q`：1678 passed, 5 skipped

## 6. 后续优化方向

- 可多轮采样取中位数，降低 LLM 随机性影响。
- 增加 refinement 场景（对已生成 proposal 追加需求），覆盖 `refine` 路径。
- 增加“摘要与 staged diff 一致性”的确定性预检：finish 前扫描摘要中的承诺词
  （如“补充/将添加”）与 overlay 是否实际匹配，命中时回传 LLM 修正。
