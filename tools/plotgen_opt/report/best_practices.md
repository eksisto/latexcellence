# plot_gen 科研制图 Agent 最佳实践指南

> 面向后续开发与优化工作的可复用经验，基于 5 场景 × 4 轮真实运行日志总结。

## 1. 提示词设计

### 图内文字必须 ASCII

- 图内文字（标题/坐标轴/图例/标注）全部使用英文 ASCII；中文/μ/°/±/— 等符号会以 `Missing character` 或 U+FFFD 形式破坏 TikZ 编译
- 单位用 ASCII 转写：`ug/mL`、`uM`、`umol/L`、`degC`、`x`、`+-`
- 说明性文本（聊天消息、summary）可以使用目标语言——把"语言"约束从绘图代码中剥离

### 给足 TikZ 逃生通道

- 明确两级策略：matplot2tikz 优先，同根因编译失败 2 次后必须切换手写 PGFPlots
- 手写模板要具体到可执行细节：数据写 `artifacts/*.csv` 并按列名引用、x 用日序号/0..n、对数轴用 `xmode=log`（不是 `xscale=log`）、误差棒用 `y error plus/minus` 列、结尾裸 `\end{tikzpicture}`
- 提醒 Agent"matplot2tikz 的错误不要用裸 try/except 吞掉"，缺失 figure.tex 会浪费一整轮

### 明确失败预算

- 只靠"相同错误文本"检测拦不住"错误不同、根因相同"的循环（如 s1 的 26 轮 TikZ 重试）
- 按工具族计连续失败数（python_sandbox / compile_tikz 各 3 次），触发时注入"换思路"指令

### 防验证循环

- `compile_tikz` 成功后，Agent 仍可能出于谨慎重复 read_file + rerun（实测每轮浪费 2~6 轮）
- 显式声明："编译成功后，未修改代码就不要再执行任何工具，直接 finish"

## 2. 数据画像与推荐

- 画像上下文越完整，Agent 越少 read_file 原始数据（实测从 1 轮浪费降到 0）
- **单值单元格检测**：`unique(x) × unique(cat) ≈ rows` 时禁止 mean/sd 聚合与误差棒——单值聚合没有意义，且误差棒是 matplot2tikz 输出的头号雷区
- 推荐层（LLM 与确定性规则）同时注入冗余提示，避免"推荐方案制造后续故障"

## 3. 执行器与质量信号

- `_extract_tikz_issues()` 的错误模式要覆盖 PGFPlots 常见报错（`Dimension too large`、`Extra \endcsname`、`Missing control sequence`、`Text line contains an invalid character`），否则结构化 issues 会漏报
- 区分真警告与噪音：`Missing character: ... nullfont` 来自 latexmk 版本字符串，应过滤；引用具体字形（如 `U+4EA7`）的警告才需要 Agent 处理
- 每次成功运行强制产出 `figure.pdf`/`figure.svg`/`figure.png`/TikZ 源，PNG 是前端预览的唯一可靠来源（matplot2tikz 内部会产生 12×12 像素的假 PNG，注意区分）

## 4. 测试与回归

- 用固定种子生成含异常值/边界值/缺失值的数据集，保持 5 个代表场景长期可跑
- 每个运行保存 trace.jsonl + events.json，对比轮数/时长/成功标志；LLM 随机性大，结论至少看 2~3 次运行
- 评分维度建议：矢量产物齐全、执行成功、TikZ 成功、轮数、无警告、PNG 预览、summary、未截断
- 提示词模板若用 `str.format()`，模板内字面花括号必须写成 `{{ }}`（如 `\addplot table {{file.csv}}`、`\end{{tikzpicture}}`）

## 5. 已知边界

- matplot2tikz 0.5.4 对误差棒、非 ASCII、日期序号的兼容性差，是主要故障源；后续优先评估 `tikzplotlib` 或受控手写生成器
- 手写 PGFPlots 与 matplotlib 产物可能随时间分叉（微调会话需重新生成两者）
- 当前沙箱为 Python 层审计钩子防护，不属于操作系统级隔离（见 docs/modules/plot_gen.md 安全边界）
