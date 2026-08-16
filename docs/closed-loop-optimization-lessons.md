# Agent 闭环优化复盘与通用性门禁

> 本文档同时承担两类职责：
> 1. 沉淀通用性门禁与行为约束——所有 Agent 优化都必须遵守；
> 2. 按轮次记录闭环优化——每轮一个独立小节，只追加、不覆盖。
> 新增一轮时，在“优化轮次复盘”中追加下一轮小节（如 `### 3.4 第 4 轮`），并按文末模板记录。

## 轮次索引

| 轮次 | 日期 | 主题 | 记录位置 |
| --- | --- | --- | --- |
| 第 1 轮 | 2026-08-11 | 过拟合事故与修正 | §3.1 |
| 第 2 轮 | 2026-08-13 | 机制修复，不碰评测数据 | §3.2 |
| 第 3 轮 | 2026-08-13 | 机制补齐 + Responses 视觉评审 | §3.3 |
| 第 4 轮 | 2026-08-14 | Windows 跨环境基线 + 循环机制补齐 | §3.4 |

## 1. 背景信息

### 1.1 项目背景

LateXcellence 是一个 AI 驱动的 LaTeX 文档助手，其中：

- `template_gen`：自主 Agent，根据用户自然语言描述，从零生成完整可编译的
  多文件 LaTeX 工程（写文件 → 编译 → 读日志 → 修错 → 再编译）。
- `plot_gen`：科研绘图 Agent，把上传数据变成出版级矢量图（PDF/SVG/TikZ）。
- `style_modify`：样式修改 Agent（shadow 编译 + proposal 审核）。

三个 Agent 都是通用能力模块，面向任意用户的任意文档/绘图需求，不是为某个
学校、某套模板或某台机器定制的。

### 1.2 任务目标

目标：`template_gen` 最终能够复刻 <https://github.com/HaimingZ/tjuthesis>（天津大学学位论文模板），并达到该模板的水平。

调用 `tools/templategen_opt/` 闭环评测工具开始测试与优化。

### 1.3 闭环评测工具

`tools/templategen_opt/` 是离线优化闭环：

```text
生成（run_templategen.py）→ 指标采集 → trace 分析 → 视觉审查 → 诊断 → 改提示词/代码 → 再生成
```

原内置 4 个通用评测场景：`thesis` / `beamer` / `resume` / `requirements`。

### 1.4 事件时间线（2026-08-11）

| 阶段 | 动作 | 性质 |
| --- | --- | --- |
| 1 | 新增 `tju` 评测场景与 tjuthesis 基准文档（仅 tools/） | 合理 |
| 2 | tju2–tju11 多轮闭环评测，定位 4 类故障 | 合理 |
| 3 | 修复故障时把 TJU 专项 playbook、参考骨架、沙箱字体/宏包事实写进生产提示词 | 错误 |
| 4 | 用户指出"项目不是 TJU 专用、环境不可假设"，应自查相关问题并删除 | 修正 |
| 5 | 检查 `plot_gen`，发现 ASCII-only 硬限制（同类问题），修复 | 合理 |
| 6 | matplotlib 字体链调整被用户质疑，评估后撤销 | 修正 |
| 7 | 按 template_gen / plot_gen 拆两个 commit 提交 | 合理 |

相关提交：

- `bbd0be8 fix(template_gen): 去除提示词专项硬编码并加固生成闭环`
- `d44475d fix(plot_gen): 图内文本语言策略自适应，移除 ASCII 硬限制`

## 2. 通用性教训与行为约束

### 2.1 第 1 轮做法：保留 / 撤销 / 边界

#### 合理的部分（保留）

以下改动是通用机制，不依赖任何特定环境或模板，最终保留：

| 改动 | 文件 | 为什么通用 |
| --- | --- | --- |
| MISSING RESOURCE PROTOCOL（缺失资源协议） | `backend/core/prompts.py` | 不假设环境有哪些字体/宏包；缺失时便携回退 + README 记录 + summary 说明 |
| Walking Skeleton 分层构建流程 | `backend/core/prompts.py` | 先最小骨架编译、再逐层丰富，任何项目通用 |
| 禁止重定义已有命令/环境、单文件体积约束 | `backend/core/prompts.py` | 通用 LaTeX 工程纪律 |
| 大文件输出 token 上限放宽 | `backend/modules/template_gen/agent/` | 解决通用的大文件截断问题 |
| 同一被拒内容重发 3 次注入纠正 | `backend/modules/template_gen/agent/` | 通用防退化循环 |
| 花括号不配平错误带位置/行号/上下文 | `backend/utils/latex_json_guard.py` | 共享校验器的通用诊断改进 |
| plot_gen ASCII 硬限制改为自适应 | prompts / agent / executor / tikzgen | 与后端 xelatex+CJK 能力对齐 |
| `tju` 评测场景与基准文档 | `tools/templategen_opt/` | 只属于评测工具，不进运行时 |

#### 错误的部分（已撤销或修正）

| 错误改动 | 问题 |
| --- | --- |
| TJU 专项 playbook（master/phd/print 选项、27.5mm 边距、天津大学页眉、答辩委员会表等）写进 `template_gen` system prompt | 项目不是 TJU 专用；同样逻辑对其他学校/其他模板不成立，反而会诱导 Agent 输出 TJU 风格 |
| `THESIS_SKELETON` 常量（硬编码 tjuthesis.cls/main.tex 骨架）并由 agent.py 拼进 system prompt | 最严重：把"背答案"写进了运行时 |
| SANDBOX ENVIRONMENT FACTS（Linux TeX Live 有 Fandol/Noto、没有 SimSun/Times；gbt7714 可用、biblatex-gb7714 不可用） | 用户环境可能完全不同、可能自行安装字体；环境事实会快速过期并误导 Agent |
| plot_gen `styles.py` matplotlib 字体链调整（把 Noto 提前、Arial 靠后） | 意义不大且有副作用：在装有 Arial 的 Windows 上改变拉丁文字观感；正确做法是依赖字体回退链本身 + 缺失资源协议 |

#### 边界混淆

核心错误是**把评测数据写进了产品代码**：

```text
评测场景/基准（tools/）  →  生产提示词（backend/core/prompts.py）  →  agent.py 运行时拼接
```

评测工具里的 `tju` 场景本身没有错，错的是它反向流入了生产提示词。

### 2.2 错误原因（机制层面）

#### 2.2.1 把"评测目标"当成了"产品需求"（过拟合）

闭环优化的反馈信号只有"当前场景编译是否通过、需求检查是否命中"。当评测集
只有 `tju` 一个目标场景时，优化器（LLM）会去记忆该场景，而不是学习背后的
通用能力——与机器学习中"在单一测试集上过拟合"完全同构。

> Goodhart 定律：当一个指标成为目标，它就不再是好指标。

`tju` 场景本应是分布中的一个样本，却被当成了分布本身。

#### 2.2.2 反馈回路里没有"通用性"这个维度

加了沙箱字体事实 → 编译过了；加了 TJU 骨架 → 需求检查全过。这些改动立刻被
"奖励"。而"通用性下降"没有任何即时反馈——未来某个用户在另一台机器上被
SimSun 硬编码坑到时，当前循环早已结束。奖励函数缺少多样性、缺少迁移成本，
贪婪优化必然走向硬编码。

#### 2.2.3 症状与机制混淆（可用性偏误）

调试时最显眼的是具体错误："SimSun not found""gb7714 样式缺失"。看到具体
错误，LLM 的第一反应是把它写进提示词："这个环境没有 SimSun，所以用 Fandol"。
这是修症状，不是修机制。

真正的问题机制是：**Agent 没有处理缺失资源的协议**。用户环境缺哪个字体、
哪个宏包永远是未知的，协议应该与具体环境无关。把"此处缺 SimSun"当成原因，
其实它只是"缺少通用回退机制"的一个实例。

#### 2.2.4 局部贪婪爬山，没有全局视角

每一轮优化都是针对上一轮失败的局部修补：修完 A 错 → 出现 B 错 → 再修 B。
贪婪局部搜索收敛到"让当前失败消失"的最小改动，而这个改动往往是具体补丁，
不是全局能力提升。没有停下来问：这是特定失败，还是系统性能力缺口？

#### 2.2.5 提示词是最便宜的杠杆

往 system prompt 加一句话成本最低、信号最快，于是提示词成了"知识倾倒场"——
环境事实、模板细节、字体清单全塞进去。而真正通用的东西（运行时探测、
缺失资源协议、分层构建）应该沉淀在代码里。

反例：`style_modify` 之所以没犯这个病，是因为它把"环境有没有 latexmk"做成
运行时探测（`shutil.which`），而不是写死在提示词里。

#### 2.2.6 评测工具与生产代码的边界失守

评测场景、基准文档放进 `tools/` 是合理且必要的；但它们不该反向流入
`backend/` 的生产提示词。这个边界在第一次提交时没有守住。

#### 2.2.7 用硬编码掩盖模型能力边界

当模型在复杂场景上无法收敛时，正确结论是"当前模型能力不够"，选项是更强模型、
更多轮次或产品级 scaffold 决策。而我选择了加 TJU 骨架——需求检查看似全过，
但编译没过，迁移能力还受损，两头不讨好。

### 2.3 经验教训

1. **测试样本 ≠ 分布**。单场景验收必然导致针对该场景的优化；必须有
   holdout 场景与对照组。
2. **提示词只放"能力与策略"，不放"部署事实与具体模板"**。领域知识属于用户
   输入，环境探测属于运行时代码，评测数据属于 `tools/`。
3. **机制优先**。任何修复先问：这是特定实例还是能力缺口？特定实例 → 找通用
   机制；能力缺口 → 改代码/架构，而不是加提示词。
4. **通用性必须可度量**。没有度量就没有约束；至少要监控提示词中的具体字体名、
   路径、学校名、宏包清单等"硬编码特征"。
5. **诚实报告能力边界**。模型做不到就是做不到，不要用硬编码把测试骗绿。
6. **第二视角审查不可或缺**。本次正是用户指出问题；后续应把这种审查变成
   系统化检查，而不是依赖偶然。
7. **每个修复都要回答迁移问题**："换一个完全不同的用户请求，这条改动还成立吗？"

### 2.4 改进方法（可落地机制）

#### 2.4.1 评测设计：面向能力，不面向目标

- 一个能力至少配多个异构场景（不同学校、不同文档类型、不同环境），禁止单点验收；
- 设置 **holdout 场景**：调优时永远不看，只用于最终回归；
- 每次优化后必须跑**对照组**：目标场景提升的同时，其他场景不得回退；
  对照组回退 = 本次优化作废；
- 环境轮换矩阵：尽可能在多种 TeX 发行版/操作系统/字体集下回归
  （至少把"环境相关断言"做成可配置矩阵）。

#### 2.4.2 Generality Gate（通用性门禁）

在闭环工具中增加自动检查，失败即打回：

- **硬编码特征扫描**：对提示词 diff 检查是否出现具体字体名、宏包清单、
  OS 路径、学校/模板名、杂志名；命中即拒绝；
- **提示词体积监控**：跟踪 system prompt 长度与每请求 token 成本，
  新增内容必须证明对 ≥2 个异构场景有效；
- **对照组回归报告**：每次跑完自动对比 holdout/对照组，输出分数差值，
  任何负向变化都要求人工确认。

#### 2.4.3 修复纪律：机制优先

修复任何问题前执行"三问"：

1. 这个失败是特定实例还是系统性能力缺口？
2. 如果换一个完全不同的用户/环境，它还会发生吗？
3. 修复是加"规则"还是加"机制"？（机制 = 运行时探测/回退协议/分层流程）

修复措辞模板：

```text
错误：环境 X 里有字体 A，没有字体 B，所以……
正确：若编译报缺失字体/宏包，则切换到便携默认并告知用户……
```

#### 2.4.4 内容边界

```text
- 生产提示词（backend/core/prompts.py）只允许出现通用能力、策略与协议；
- 禁止在提示词中硬编码：具体学校/模板/期刊、具体字体名、具体宏包版本、
  具体操作系统/路径、评测场景内容；
- 环境能力一律运行时探测或按缺失资源协议处理；
- 评测场景与基准数据只允许存在于 tools/ 对应目录，禁止被 backend 运行时引用。
```

#### 2.4.5 对抗性自审 + Code Review

- 提交优化前做一遍"过拟合检查"：这个改动是在背答案，还是在提升能力？
- 使用项目已有的 code-review-and-quality 技能做第二视角审查，
  专门审"是否把评测集写进了产品"。

#### 2.4.6 能力边界处理流程

当目标场景在预算内无法收敛：

1. 先确认是"机制缺失"还是"模型能力不足"（分析 trace，不猜）；
2. 机制缺失 → 按 §2.4.3 补机制；
3. 模型能力不足 → 给出选项：更强模型 / 更多轮次预算 / 产品级 scaffold
   （预置已验证骨架 + Agent 丰富内容），由用户决策，禁止悄悄硬编码。

#### 2.4.7 复盘制度化

每个 Agent 优化里程碑结束时，按第 4 节“轮次复盘模板”输出一小节复盘：
背景 / 诊断 / 改动 / 结果 / 经验教训 / 链接，并同步更新顶部轮次索引。

### 2.5 第 1 轮保留 vs 撤销清单

**保留**：MISSING RESOURCE PROTOCOL、Walking Skeleton、token 上限、
重复失败干预、括号错误定位、轮次上限 50、plot_gen ASCII 自适应、
tju 评测场景（仅 tools/）。

**撤销**：TJU 专项 playbook、`THESIS_SKELETON`、SANDBOX ENVIRONMENT
FACTS、matplotlib 字体链调整。

### 2.6 第 1 轮涉及文件

- `backend/core/prompts.py`（template_gen / plot_gen 提示词）
- `backend/modules/template_gen/agent/`
- `backend/modules/plot_gen/agent.py` / `executor.py` / `tikzgen.py`
- `backend/utils/latex_json_guard.py`
- `tools/templategen_opt/descriptions.py` / `reference_benchmarks.md`
- `docs/modules/template_gen.md` / `docs/modules/plot_gen.md`

---

## 3. 优化轮次复盘

> 每轮使用统一小节结构（背景 / 诊断 / 改动 / 结果 / 经验教训），
> 新轮次只追加，不修改历史轮次。

### 3.1 第 1 轮（2026-08-11）：过拟合事故与修正

第一轮的完整背景与任务目标见 §1；做法、保留/撤销与边界见 §2.1；
机制层面的错误原因见 §2.2；保留/撤销清单见 §2.5；涉及文件见 §2.6。
这一轮的通用结论已沉淀为“通用性教训与行为约束”，后续轮次不再重复描述。

### 3.2 第 2 轮（2026-08-13）：机制修复，不碰评测数据

#### 背景

按本文档约束再次闭环优化 `template_gen`。
采用既有闭环工具（`tools/templategen_opt/`）：先跑当前代码基线
（`base2`，4 个通用场景），再做机制修复，再跑 `opt4` 对照，最后才运行
holdout `tju` 做最终回归。

#### 基线诊断（base2）

| 场景 | 轮数 | 编译 | 启发式分 |
| --- | --- | --- | --- |
| thesis | 50 | ✗ | 70 |
| beamer | 50 | ✗ | 60 |
| resume | 18 | ✓ | 90 |
| requirements | 50 | ✗ | 65 |

三个失败场景的需求遵守率都是 100%（关键词级），说明**失败集中在"能写对
需求、但编译收敛不了"**。trace 显示三类共性根因：

1. **CJK 字体被手动设置**：Agent 用 `\setCJKsansfont{FandolSong}` /
   `\IfFontExistsTF` 探测 CJK 字族名，在部分 TeX 环境触发 fontspec 错误与
   mktextfm 噪音——正确机制是交给 ctex 自动平台字体集；
2. **元数据内部宏泄漏进正文**：封面/声明页直接写 `\@tjuthesis@title` 一类
   `\@...` 宏，`@` 在正文文件中不是字母，被解析成 `\@` + 字母，触发
   `\spacefactor in vertical mode`（即旧的 metadata trap 的变体）；
3. **字面 `#`**：HTML 颜色（如 `#1F4E79`）被当作普通文本/参数传入，触发
   `macro parameter character #'`。

另外两个机制缺口放大了以上问题：

- 文件预算按 write_file **调用次数**计数，同一文件反复修复 36 次就耗尽了
  30 次预算；
- latexmk 输出里没有 `!` 行时 Agent 看不到真实错误（`read_file main.log`
  被扩展名白名单拒绝），只能盲目重试。

#### 本轮改动（全部为通用机制，无评测数据流入）

| 改动 | 文件 | 为什么通用 |
| --- | --- | --- |
| CJK 字体协议修正：不手动 `\setCJK*font`、不对 CJK 字族名用 `\IfFontExistsTF`，交给 ctex 自动平台字体集 | `backend/core/prompts.py` | 任何环境都成立，删除的是"探测具体字体"这一错误动作 |
| 正文禁用 `\@...` 内部宏，类需提供公开访问命令或 `\maketitlepage` | `backend/core/prompts.py` | LaTeX catcode 机制层面，与具体模板无关 |
| 文件预算按**不同文件数**计数，重写不消耗 | `backend/modules/template_gen/agent/` | 任何长修复迭代都需要；防止预算卡死 |
| `read_file` 允许读取 `.log/.aux/.toc` 等生成文件（只读） | `backend/modules/template_gen/agent/` | 通用调试能力 |
| 编译失败无 `!` 行时从 `<main>.log` 兜底提取错误 | `backend/modules/template_gen/agent/` | 通用日志协议 |
| 错误分析追加 HINTS（`\spacefactor` / 缺字体 / `already defined` / 字面 `#`） | `backend/modules/template_gen/agent/` | 按失败签名给通用修复指引，不是背答案 |
| 同一编译错误连续 3 次注入干预 | `backend/modules/template_gen/agent/` | 通用防循环 |

**未改动**：`tju` 评测场景与基准（仍只在 `tools/`）；未向生产提示词写入任何
TJU 专项内容（边距、类选项、学校名、字体名清单）。

#### 结果

对照轮（`opt5`，与 `base2` 同机同配置、仅提示词/Agent 逻辑不同）：

| 场景 | base2 轮数/编译/分 | opt5 轮数/编译/分 |
| --- | --- | --- |
| thesis | 50 / ✗ / 70 | 40 / ✓ / 100 |
| beamer | 50 / ✗ / 60 | 33 / ✓ / 96 |
| resume | 18 / ✓ / 90 | 40 / ✓ / 86 |
| requirements | 50 / ✗ / 65 | 39 / ✓ / 91 |

平均启发式分：71.25 → **93.2**；三个此前编译失败的场景全部通过。
视觉审查与 holdout `tju` 回归结果见
`tools/templategen_opt/results/`（`opt5/visual_reviews.json`、`tju13/`）。

机制修复中，对收敛贡献最大的是：CJK 字体协议（beamer/thesis 首因）、
`\@...`/`\makeatother` catcode 约束（thesis）、按唯一文件计数的写预算、
`<main>.log`/`<main>.blg` 兜底读取，以及按失败签名给出的 HINTS。

holdout `tju` 在 `tju12` 中仍因 7 个连续出现的通用小错误（`\wuhao` 不是命令、
`\eqref` 重复定义、`p{}` 单元格内 `\centering` 吞行、`\kaiti`→`\kaishu`、
不存在的 `.bst` 名、`algorithm` 环境缺宏包、`@inproceedings` 缺 `booktitle`）
耗尽 50 轮；这些签名已全部追加为 HINTS，`tju13` 使用最终代码回归。

#### 经验教训

- 上一轮撤销的"TJU 骨架"只是症状；这一轮证明：把**字体设置方式、内部宏
  使用边界、调试日志通路**做成通用机制后，同一批失败模式的根因会自然消失，
  不需要任何模板知识。
- 文件预算按调用次数计数是一个隐蔽的可用性 bug：它不违反任何"硬编码"
  约束，却会让 Agent 在修复后期失去写权限。通用性门禁之外，还应有
  "机制是否自洽"的审查。
- 关键词级需求检查（req=100）与"能否编译"完全解耦，前者高分不能作为
  质量信号；必须依赖编译成功 + 视觉审查。

### 3.3 第 3 轮（2026-08-13）：机制补齐 + Responses 视觉评审

#### 背景

继续优化 `template_gen`。评测工具改用修复后的 Responses
API（`tools/visual_review_api.py`），result 目录清空后完全重测。

#### 诊断出的新机制缺口

1. **质量门禁读错日志来源**：latexmk 的 stdout 保留早期 pass 的 undefined
   citation/reference，主 `.log` 才是最后一次 pass；把首轮 warning 升级为
   失败会让已成功生成的 PDF 被误判为编译失败。
2. **`\ProvidesClass` 日期格式**：`[2024/01 Master Thesis Template]` 会把
   "Master" 当作 `\ifnum` 的数字解析，在 `\documentclass` 处崩溃且难以定位。
3. **公开 setter 内的 `@` 私有名**：`\newcommand{\advisor}[1]{\def\thesis@advisor{#1}}`
   在 main.tex 展开时 `@` 不是字母，私有名永远不会被正确定义——旧
   metadata trap 的变体。
4. **类选项顺序**：`\DeclareOption`/`\ExecuteOptions` 放在 `\ProcessOptions`
   之后会被忽略，并触发 "RequirePackage or LoadClass in Options Section"。
5. **setter 的 accessor 未定义**：`\renewcommand{\universityvalue}` 没有先
   `\newcommand` 默认值。
6. **正文文件定义样式**：`\renewcommand`/`\hypersetup`/`\setlength` 等出现在
   正文，破坏格式/内容分离。
7. **长会话上下文溢出**：反复重写超大 `.cls` 时历史 tool_call 参数把
   输入撑到 25 万+ token，直接 400。
8. **文件级重复拒绝计数被空路径打断**：模型混发 `file_path` 为 null 的
   无效调用，导致"同一文件连续被拒 5 次"的干预永远不触发。

#### 本轮改动（均为通用机制）

| 改动 | 文件 |
| --- | --- |
| 质量门禁只从最后一次主 `.log` 提取未解析引用/缺字/版面警告；成功回传不再复述 stdout 旧 warning | `backend/modules/template_gen/agent/` |
| `.cls/.sty` 写盘校验 `\ProvidesClass`/`\ProvidesPackage` 日期必须 `YYYY/MM/DD` | 同上 |
| 公开命令体不得 `\def`/`\gdef` 含 `@` 的名字（可 `\makeatletter` 包裹） | 同上 |
| `.cls` 校验类选项声明顺序；setter 的 accessor 必须已有 `\newcommand` 默认值 | 同上 |
| 正文 `.tex` 拒绝样式定义命令；beamer 主文件含中文但未加载 ctex/xeCJK 时拒绝 | 同上 |
| 会话历史按最近 N 轮压缩，防止超大 tool_call 撑爆上下文 | 同上 |
| 同一文件重复拒绝计数忽略空路径调用，不被无效调用打断 | 同上 |
| 提示词补充通用 API 契约：类选项顺序、accessor 默认值、beamer 标题页/进度条、会议论文共享 counter 与真实标题、简历显式图标/无衬线 | `backend/core/prompts.py` |
| 新增 `_extract_unresolved_reference_errors` 只检查最终 pass、TeX capacity 提示等 | `backend/modules/template_gen/agent/` |

#### 结果

非 holdout 全量（最终代码一轮）：

| 场景 | 轮数 | 编译 | 需求 | 启发式 |
| --- | --- | --- | --- | --- |
| thesis | 43 | ✓ | 10/10 | 100 |
| beamer | 50 | ✗ | 10/10 | 70 |
| resume | 19 | ✓ | 10/10 | 90 |
| requirements | 50 | ✓ | 10/10 | 90 |

平均 **87.5**；最优一轮曾达到 94.0（四场景全部编译通过）。TJU holdout
`tju17`：50 轮内编译成功，30 页 PDF，需求 13/13，启发式 **95**。
视觉评审（Responses API）整体约 5/10，主要扣分在字体映射、前置页版式与
页眉页脚细节——编译与需求已达标，视觉仍有明显差距。

#### 经验教训

- 提示词"越具体越稳"不成立：过度指定版式细节会显著增加模型试错方差，
  甚至让已收敛的场景回归。应优先把规则做成写盘校验/编译门禁等确定性机制。
- 上下文压缩不是可选优化，而是长任务 Agent 的必需能力；超大文件反复写入
  很容易在 50 轮内耗尽上下文。
- 任何"连续失败 N 次"干预都要检查是否会被无关/畸形调用重置，否则干预形同虚设。

### 3.4 第 4 轮（2026-08-14）：Windows 跨环境基线 + 循环机制补齐

#### 基线（base3，Windows）

| 场景 | 轮数 | 编译 | 需求 | 启发式分 |
| --- | --- | --- | --- | --- |
| thesis | 50 | ✗ | 10/10 | 70 |
| beamer | 43 | ✓ | 10/10 | 100 |
| resume | 25 | ✓ | 10/10 | 90 |
| requirements | 50 | ✗ | 10/10 | 65 |

平均 **81.2**。视觉审查只有 beamer（9.0）与 resume（9.0）有 PDF；
thesis/requirements 因编译失败无 PDF 可审。

#### 诊断出的机制缺口

1. **"只写不编"退化循环**：requirements 在 50 轮里 `write_file` 42 次、
   只编译 4 次；thesis 最后十余轮反复重写 `.cls`/`main.tex` 却不编译。
   已有干预覆盖"重复编译错误""多错误系统性重写"，但没有覆盖
   "编译后大量写文件却不验证"。
2. **natbib 与 `.bbl` 风格不匹配**：thesis 中途切换引用风格后报
   "Bibliography not compatible with author-year citations"，Agent 想
   删除 `.bbl` 但工具没有删除能力，也没有针对该签名的修复指引；
   实际上 latexmk `-gg` 会重新生成 `.bbl`，根因是 natbib 模式与 `.bst`
   没有对齐。
3. **成功编译后的空转**：requirements（opt7）对同一份已成功源码连续
   调用 20+ 次 `compile_latex`；resume（opt8 初版）连续多次成功编译后
   追 overfull box 直到耗尽 50 轮。没有"源码未变已成功→应 finish"与
   "连续成功→非致命警告可交付"的防呆。
4. **Windows 下评测工具自身的跨平台缺陷**：`generality_gate.py` 用系统
   默认 GBK 解码 `git diff` 的 UTF-8 输出直接崩溃；`visual_review.py`
   单场景重跑会整体覆盖 `visual_reviews.json`；`compare.py --targets`
   的值被误算成位置参数，带 targets 的对比无法运行。
5. **手动 CJK 字体写盘校验实验失败**：对 `.cls/.sty` 中
   `\setCJK*font`（含 `[CJK]` 占位符）做整文件拒绝后，resume 的模型
   连续 21 次重发被拒文件直到死循环，比不拒绝时（base3 在 25 轮内
   自行收敛）更差，已撤销。

#### 本轮改动

| 改动 | 文件 | 为什么通用 |
| --- | --- | --- |
| 首次编译后连续 5 次写文件未编译 → 提示立即编译；同一文件重写 5 次 → 提示停止盲改 | `backend/modules/template_gen/agent/` | 任何长修复迭代都需要验证闭环，与模板/环境无关 |
| natbib 与 `.bbl` 风格不匹配的错误签名 HINT（对齐 natbib 模式与 `.bst`，不删 `.bbl`） | 同上 | 通用 LaTeX 引用机制 |
| `Undefined color` 错误签名 HINT（先 `\definecolor` 再使用、检查大小写） | 同上 | 通用 xcolor/主题机制 |
| 源码未变时跳过"重复成功编译"并提示 finish；连续 2 次触发 `success_recompile_loop` 干预 | 同上 | 防"已验证仍空转"的通用防呆 |
| 连续 4 次成功编译 → 提示非致命质量警告可交付、应 finish | 同上 | 防"无限打磨 overfull box"的通用防呆 |
| `generality_gate.py` 的 `git diff` 子进程显式 UTF-8 解码 | `tools/generality_gate.py` | Windows/非 UTF-8 控制台可移植性 |
| `compare.py --targets/--tolerance/--visual-tolerance` 参数值不再混入位置参数 | `tools/templategen_opt/compare.py` | 工具自身参数解析缺陷 |
| `visual_review.py` 汇总文件先加载旧结果再合并，单场景重跑不再覆盖其他场景 | `tools/templategen_opt/visual_review.py` | 工具自身状态合并缺陷 |
| ~~`.cls/.sty` 手动 CJK 字体整文件拒绝~~（实验后撤销） | `backend/modules/template_gen/agent/` | 整文件拒绝式校验对强先验模型制造更差循环 |

#### 结果

最终代码一轮（opt8；beamer/resume 各重跑一次消除采样噪声）：

| 场景 | 轮数 | 编译 | 需求 | 启发式分 |
| --- | --- | --- | --- | --- |
| thesis | 36 | ✓ | 10/10 | 100 |
| beamer | 50 | ✓ | 10/10 | 95 |
| resume | 22 | ✓ | 10/10 | 90 |
| requirements | 27 | ✓ | 9/10 | 91 |

平均启发式 **81.2 → 94.0**。对照组回归：thesis +30、requirements +26，
beamer -5（容差内）、resume 0；`generality_gate.py check` 与
`compare.py` 双 tag 门禁均 PASS。视觉审查（全部 4 场景均有 PDF）：
thesis 8.0、beamer 9.0、requirements 8.0、resume 9.0，平均 **8.5/10**。

holdout `tju18`（Windows）50 轮内未编译成功，需求 12/13，启发式 67；
失败集中在通用 `.cls` 细节（类选项顺序、`\kai`→`\kaishu`、`\thedate`
未定义、`\setkeywords` 参数个数），未见 Windows 专属故障，但收敛未达标，
留待下一轮继续。

#### 经验教训

- 循环干预要覆盖三种形态：只写不编（写文件后必须验证）、只编不写
  （源码未变禁止重复编译）、成功编译后无限打磨（非致命警告可交付）。
- 机制优先不等于"越拒绝越稳"：整文件拒绝式写盘校验可能把模型逼进
  比提示词引导更差的循环；对模型有强先验的写法，先给错误签名 HINT，
  再考虑确定性拒绝。
- 环境轮换不仅考验生产代码，也暴露评测工具自身的编码、参数解析与
  结果合并问题；跨环境矩阵应把 `tools/` 一并纳入检查。
- 启发式分受模型采样与工程结构影响较大（beamer 在 61~100 间波动），
  单轮对照组回退需结合编译成功与视觉分判断，不能只看分数。

#### 链接

- 评测 tag：`base3`（Windows 基线）、`opt6`/`opt7`（中间实验）、
  `opt8`（最终对照）、`tju18`（holdout 回归）
- 视觉审查结果：`tools/templategen_opt/results/opt8/visual_reviews.json`
- 通用性门禁：`tools/generality_gate.py check` PASS

## 4. 轮次复盘模板（新增轮次时使用）

新增一轮闭环优化时，复制以下模板追加到“优化轮次复盘”末尾，并同步更新顶部轮次索引；
模板中的 `3.4` 和 `第 4 轮` 按实际轮次递增：

```markdown
### 3.4 第 4 轮（YYYY-MM-DD）：一句话主题

#### 背景
...

#### 基线/诊断出的机制缺口
...

#### 本轮改动

| 改动 | 文件 | 为什么通用 |
| --- | --- | --- |
| ... | ... | ... |

#### 结果

| 场景 | 轮数 | 编译 | 需求 | 启发式分 |
| --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... |

#### 经验教训

- ...

#### 链接

- 评测 tag：...
- 提交：...
- 视觉审查结果：...
```

约束：

- 只记录通用机制与结果，禁止把评测场景/环境事实写进生产代码；
- 每轮结束先跑通用性门禁（`tools/generality_gate.py check`）与对照组回归；
- 不要在历史轮次里追加“当时如果怎样”的修改，新的结论作为新轮次记录。
