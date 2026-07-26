# Git 差异高亮模块 `git_diff/`

差异高亮本身不直接调用 LLM——通过 `git show` / `git archive` 提取两个 Git 状态下的 LaTeX 源码，递归展开 `\input`/`\include`/`\subfile` 依赖后，以红删绿增底纹标记，编译为 PDF。不写回用户项目文件。

支持两种差异引擎：内置 `difflib`（行级对比）或外部 `latexdiff`（语义对比）。

当 PDF 编译失败时，会调用 `compile_error_translator.translate_compile_errors()` 将错误日志翻译为中文解释和修复建议（复用 compile 模块的 LLM 错误翻译能力）。

| 维度 | 差异高亮 | 其他模块 |
| --- | --- | --- |
| 核心工具 | git + difflib/latexdiff + latexmk + LLM（仅编译失败翻译错误） | latexmk / pandoc / LLM |
| 是否修改源文件 | 否（临时目录编译） | 是/否 |

## Pydantic 输出模型

- `GitRevision`（value, label, kind, short_hash, subject）
- `GitRefsResult`（git_root, current_branch, is_dirty, revisions）
- `DiffHighlightResult`（success, job_id, pdf_url, main_file, base_ref, target_ref, added_lines, deleted_lines, included_files）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `list_git_revisions` | 列出可选 revision（工作区 + 最近 50 条提交 + 分支 + 标签） |
| `render_diff_pdf(main_file, base_ref, target_ref)` | 生成差异高亮 PDF 的主入口 |
| `_flatten_main_document` | 递归展开主文件及 input/include 依赖 |
| `_build_diff_document` | difflib 生成带红删绿增底纹的差异 LaTeX 文档 |
| `_prepare_target_project` | git archive 或 copytree 准备编译目录 |
| `get_pdf_path` | 根据 job_id 定位已生成的 PDF |

**差异生成流程**：

1. 展开 base_ref 主文件（递归解析 `\input`/ `\include`/ `\subfile` 依赖）
2. 展开 target_ref 主文件（工作区直接读文件；commit/branch/tag 通过 `git show <ref>:<path>`）
3. 根据选择的差异引擎生成差异：
    - **difflib 引擎（内置）**：
        1. 两份文档以 `\begin{document}` 为界拆分为 preamble + body + end
        2. body 部分逐行 `difflib.SequenceMatcher` 比对：equal 原样保留、delete 行包裹 `LXDiffDeleted`（红色底纹）、insert 行包裹 `LXDiffAdded`（绿色底纹）、replace 行先删后插
        3. 组装差异文档：target 的 preamble + `xcolor`/`framed` 宏包定义 + 差异 body + target 的 end
    - **latexdiff 引擎（外部）**：
        1. 调用 `latexdiff` 命令行工具生成差异 `.tex` 文件
        2. 支持配置标记样式（markup type）和数学公式标记粒度（math markup）
        3. 支持排除安全命令和文本命令
4. 准备编译目录（工作区用 `shutil.copytree`，commit 用 `git archive --format=zip`）
5. 写入差异 `.tex` 文件，调用 `latexmk -xelatex` 编译 → PDF 复制到临时目录

**差异 LaTeX 宏包定义**（使用 `framed` 和 `MakeFramed` 环境以支持跨页和复杂内容，颜色可通过配置自定义）：

```latex
\usepackage{xcolor}
\usepackage{framed}
\definecolor{LXDiffAddBg}{HTML}{D7E8B1}  % 默认新增颜色，可通过配置修改
\definecolor{LXDiffDelBg}{HTML}{FFA3A3}  % 默认删除颜色，可通过配置修改
\newenvironment{LXDiffAdded}{...\colorbox{LXDiffAddBg}...}{...}
\newenvironment{LXDiffDeleted}{...\colorbox{LXDiffDelBg}...}{...}
```

### 多文件支持

- 用户只需选择主文件，系统自动递归展开 `\input`/ `\include`/ `\subfile` 依赖
- 路径解析优先相对于当前文件所在目录，其次相对于项目根目录
- 路径安全校验：拒绝绝对路径和 `..` 目录穿越

**PDF 存储**：系统临时目录 `latexcellence_git_diff/{job_id}/`，由 OS 重启时清理。

## API 路由

Prefix: `/api/git-diff`, Tag: `git-diff`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/refs` | 获取 Git revision 列表 |
| POST | `/render-pdf` | 生成差异高亮 PDF |
| GET | `/pdf/{job_id}` | 下载/预览 PDF |

## 配置项

`config.json` 中 `features.git_diff` 部分：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用 Git 差异高亮模块 |
| `default_engine` | string | `"difflib"` | 默认差异引擎：`difflib`（内置行级对比）/ `latexdiff`（外部语义对比） |
| `default_markup_type` | string | `"UNDERLINE"` | latexdiff 默认标记类型（UNDERLINE/CTRADITIONAL/CFONT/CHANGEBAR/CCHANGEBAR/CULINECHBAR/FONTSTRIKE/INVISIBLE） |
| `default_math_markup` | string | `"fine"` | latexdiff 数学公式标记粒度：`fine`（逐符号）/ `coarse`（整公式）/ `off`（关闭）/ `whole`（整段） |
| `default_color_add` | string | `"#D7E8B1"` | 新增内容颜色（HEX 格式） |
| `default_color_del` | string | `"#FFA3A3"` | 删除内容颜色（HEX 格式） |
