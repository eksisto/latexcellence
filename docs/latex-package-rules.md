# 宏包规则表维护指南

本文档是 `backend/utils/latex_package_rules.py` 的维护手册。规则表的任何改动
都必须按本文档的模板和评审清单执行，确保"有依据、有测试、有验证"。

持续改进的三个目标：

1. 查准率：不误报。内核命令不进表，逐字内容、注释、URL 不触发规则
2. 查全率：不遗漏。论文写作、OCR、Pandoc 转换的常见宏包都能命中
3. 可维护性：每次改表都有依据、有测试、有验证，不依赖个人记忆

## 规则表职责边界

- 只收录"必须加载该宏包才能编译通过"的命令/环境/列说明符
- LaTeX 内核能力（`tabular` / `\frac` / `\mathcal` / `\hline` 等）**永不收录**，
  内核白名单见 `KERNEL_COMMANDS` / `KERNEL_ENVS`，由测试守卫
- 同一命令多个提供者时，只映射最常用提供者，取舍写入 `AMBIGUITY_DECISIONS`
- 涉及歧义映射（一个命令多个提供者）时，默认不映射命令、只映射环境
  （如算法伪代码的 `\If` 系列命令只映射 `{algorithmic}` 环境）
- 环境名区分大小写；命令/环境→宏包是一对一映射
- 规则表只输出包名，不推断 `\usepackage[opt]{...}` 选项

## 新增规则模板

新增一条规则必须能回答以下全部字段：

| 字段 | 说明 | 必填 |
| --- | --- | --- |
| 命令/环境名 | 去掉反斜杠的命令名，或 `\begin{...}` 的环境名（区分大小写） | ✓ |
| 类型 | `command` / `environment` / `column letter` / `column prefix` | ✓ |
| 目标宏包 | 小写、无 `.sty` 后缀（`xeCJK` 等大小写敏感包名除外） | ✓ |
| 证据 | CTAN 文档链接 **或** 编译验证结果（本机 `pdflatex` 实测） | ✓ |
| 测试片段 | 加入 `tests/fixtures/latex_packages/` 语料 + manifest 预期 | ✓ |
| 是否内核能力 | 查 `KERNEL_COMMANDS` / `KERNEL_ENVS`，若在内核白名单则**拒绝收录** | ✓ |
| 歧义说明 | 多个提供者时记录取舍理由到 `AMBIGUITY_DECISIONS` | 有歧义时 |
| 领域归属 | 该宏包在 math/table 领域是否应被排除（更新 `DOMAIN_EXCLUDED_PACKAGES`） | 有领域差异时 |

## 评审清单

提交规则表改动前逐项检查：

1. **内核守卫**：新规则不在 `KERNEL_COMMANDS` / `KERNEL_ENVS` 中
2. **数据完整性**：包名小写/无 `.sty`；命令/环境名无花括号、无反斜杠
3. **语料**：新增测试片段已加入 `tests/fixtures/latex_packages/`，
   manifest 中预期宏包列表准确，`test_latex_package_corpus.py` 全绿
4. **编译验证**：有 TeX 的机器上跑
   `python tools/latex_packages/verify_rules.py`，新片段加载推荐宏包后无残留错误；
   无法编译的对抗样本必须标记 `skip_compile` 并写明原因
5. **覆盖率**：`python tools/latex_packages/coverage_report.py` 无新增未覆盖项
   （或有充分理由）
6. **歧义记录**：多提供者的映射已写入 `AMBIGUITY_DECISIONS`
7. **领域表**：新宏包若在特定领域无关，已加入 `DOMAIN_EXCLUDED_PACKAGES`，
   且领域排除表条目都能在规则表/隐式产出中找到对应包名
8. **变更日志**：规则表头部 `变更日志` 已记录本次增删和理由
9. **全量测试**：`pytest tests/test_utils/ -q` 全绿

## 常用命令

```bash
# 语料基线（查准率/查全率/LLM 兜底触发率，无 TeX 也可跑）
python -m pytest tests/test_utils/test_latex_package_corpus.py -q

# 离线编译验证（维护者专用，需本机 TeX；无 TeX 时自动跳过）
python tools/latex_packages/verify_rules.py

# CI 编译验证（具备 TeX 环境时自动运行，LATEX_PACKAGE_VERIFY=0 可跳过）
python -m pytest tests/test_utils/test_latex_package_compile_verification.py -q

# 覆盖率报告（对照 tools/latex_packages/common_packages.txt）
python tools/latex_packages/coverage_report.py
```

## 领域参数约定

| 领域 | 调用方 | 排除策略 |
| --- | --- | --- |
| `general` | 格式转换（整篇文档） | 不排除；已加载宏包自动剔除 |
| `math` | 公式 OCR | 排除表格/浮动/版式/引用/字体/代码/算法/绘图类宏包 |
| `table` | 表格 OCR | 排除引用/字体/页面布局/算法/定理/代码/PDF 插入类宏包 |

`float` 由分析器的 `[H]` 浮动识别隐式产出，虽不在映射表中，仍参与领域排除
（公式领域不推荐 float）。

当前领域排除按包名粗粒度（`DOMAIN_EXCLUDED_PACKAGES`）。后续如需更细粒度
（如 math 领域只排除某个宏包的表格用法），再改为逐条打标签。

## 明确不做

- 运行时编译验证（需要用户安装 TeX，违背零依赖约束）
- `kpsewhich` 存在性探测（同样依赖 TeX 发行版）
- 推荐宏包选项（只输出包名注释，不推断 `\usepackage[opt]{...}`）
- 动态学习用户自定义宏包（留给 LLM 兜底路径）
- 在 LLM 兜底提示词中维护第二份 Feature→Package 映射表（已移除；
  规则表保持唯一事实来源，兜底只做通用推理与内核能力排除）

## 后续扩充路线图

第一批（引用/交叉引用/算法/定理/附录/PDF/中文/表格补充/页面布局/代码/术语/
数学补充）已完成，语料已加入。后续按需扩充的候选（每批仍须带测试片段和
验证依据，按新增规则模板提交）：

第二批（按需补）：

- 数学符号包：mathdots、stackengine、xfrac
- 字体包：libertine、newtxtext/newtxmath、stix
- 绘图扩展：`{tikzcd}` → tikz-cd、`{circuitikz}` → circuitikz、`{forest}` → forest
- 化学扩展：mhchem 更多命令

第三批（暂缓或明确不做）：

- 过时包（如 tabu）
- 与内核极易混淆的映射（如 `\frac` → amsmath 这类）
- 需要运行时探测才能确定的映射

## 效果度量

以下指标以"建好语料后的当前规则表"为基线，每次改表后重跑基线对比，
把差异记录在规则表头部 `变更日志` 里：

- **查准率**：规则表给出的包列表中，实际需要（编译验证）的比例
- **查全率**：实际需要（编译验证）的包中，规则表命中的比例
- **LLM 兜底触发率**：规则表未命中而触发 LLM 调用的次数占比，越低越好
- **单次检测耗时**：规则表路径不调用 LLM，应接近纯正则扫描耗时

前两项与 LLM 兜底触发率由 `test_latex_package_corpus.py` 输出基线。
单次检测耗时可在装有 TeX 的机器上用 `verify_rules.py` 的编译结果对照。

## 相关文件

- 规则表：`backend/utils/latex_package_rules.py`
- 扫描器与兜底：`backend/utils/latex_package_analyzer.py`
- 单测：`tests/test_utils/test_latex_package_analyzer.py`、
  `test_latex_package_rules.py`、`test_latex_package_corpus.py`、
  `test_latex_package_compile_verification.py`
- 语料：`tests/fixtures/latex_packages/`（manifest.json 记录预期宏包）
- 维护脚本：`tools/latex_packages/`（verify_rules.py / coverage_report.py /
  common_packages.txt）
- 调用方：`backend/modules/formula_ocr/service.py`、
  `backend/modules/table_ocr/service.py`、
  `backend/modules/format_converter/service.py`
- 核心模块文档：`docs/backend-core.md`
