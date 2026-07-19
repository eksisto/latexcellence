# Git 差异高亮模块 `git_diff/`

## 设计特点

差异高亮模块**不使用 LLM**——通过 `git show` / `git archive` 提取两个 Git 状态下的
LaTeX 源码，递归展开 `\input` / `\include` / `\subfile` 依赖后用 `difflib` 生成行级
差异，再用 `xcolor` + `framed` 宏包以红删绿增底纹标记，最后在临时目录中调用
`latexmk -xelatex` 编译为 PDF。**不写回用户项目文件**。

| 维度 | 差异高亮 | 其他模块 |
|------|---------|---------|
| 核心工具 | git + difflib + latexmk | latexmk / pandoc / LLM API |
| 输入 | Git revision（commit/branch/tag/工作区）+ 主文件路径 | LaTeX 源码 / 图片 / Markdown |
| 输出 | 高亮差异 PDF | 修改后文本 / 编译结果 / LaTeX 公式 |
| 是否修改源文件 | 否（在临时目录中编译，不影响项目） | 是 / 否 |
| Marker 保护 | 否 | 是（typo_check/expression_opt）/ 否 |
| 多文件支持 | 是（递归展开 input/include 依赖） | 单文件 |

## `service.py` — 差异高亮业务逻辑

### Pydantic 输出模型

```python
class GitRevision(BaseModel):
    value: str           # Git 引用（完整哈希/分支名/标签名/WORKTREE 标记）
    label: str           # 前端显示标签
    kind: str            # 类型："worktree" / "commit" / "branch" / "tag"
    short_hash: str = "" # 短哈希
    subject: str = ""    # 提交信息

class GitRefsResult(BaseModel):
    git_root: str
    current_branch: str = ""
    is_dirty: bool = False
    revisions: list[GitRevision]

class DiffHighlightResult(BaseModel):
    success: bool
    job_id: str = ""
    pdf_url: str = ""             # /api/git-diff/pdf/{job_id}
    main_file: str = ""
    base_ref: str = ""
    target_ref: str = ""
    added_lines: int = 0
    deleted_lines: int = 0
    included_files: list[str] = []  # 被展开的依赖文件列表
    stdout: str = ""
    stderr: str = ""
    explanation: str = ""
```

### 核心方法

| 方法 | 功能 |
| ---- | ---- |
| `list_git_revisions()` | 异步：列出可选 revision（工作区 + 最近 50 条提交 + 分支 + 标签） |
| `render_diff_pdf(main_file, base_ref, target_ref)` | 异步：生成差异高亮 PDF 的主入口 |
| `_flatten_main_document(ref, project, main_rel)` | 异步：递归展开主文件及其 input/include 依赖为单文档 |
| `_build_diff_document(base_content, target_content)` | 使用 difflib 生成带红删绿增底纹的差异 LaTeX 文档 |
| `_prepare_target_project(target_ref, context, job_dir)` | 异步：用 git archive 或 copytree 准备编译目录 |
| `get_pdf_path(job_id)` | 根据 job_id 定位已生成的 PDF 文件（含 hex 校验） |

### 差异生成流程

```
1. 验证项目已打开 + 获取 Git 根目录
2. 展开初始状态（base_ref）主文件 → 递归解析 \input/\include/\subfile
   - 工作区：直接读文件
   - commit/branch/tag：git show <ref>:<path>
3. 展开结束状态（target_ref）主文件 → 同上
4. 拆分两份文档为 preamble + body + end（以 \begin{document} 为界）
5. 对 body 部分行 difflib.SequenceMatcher 比对：
   - equal 行：原样保留
   - delete 行：包裹 \begin{LXDiffDeleted}...\end{LXDiffDeleted}（红色底纹）
   - insert 行：包裹 \begin{LXDiffAdded}...\end{LXDiffAdded}（绿色底纹）
   - replace 行：先删除块后增加块
6. 使用 target 的 preamble + 注入 xcolor/framed 宏包定义 + 差异 body + target 的 end
7. 准备编译目录：
   - 工作区：shutil.copytree 复制项目（排除 .git/node_modules/aux 等）
   - commit/branch/tag：git archive --format=zip 解压
8. 在编译目录中写入差异 .tex 文件，调用 latexmk -xelatex 编译
9. 复制 PDF 到临时目录 latexcellence_git_diff/{job_id}/diff-highlight.pdf
10. 返回 pdf_url + 增删行数 + 展开文件列表
```

### 差异高亮的 LaTeX 宏包定义

```latex
\usepackage{xcolor}
\usepackage{framed}
\definecolor{LXDiffAddBg}{HTML}{DFF6DD}   % 绿色底纹
\definecolor{LXDiffDelBg}{HTML}{FFE3E3}   % 红色底纹
\newenvironment{LXDiffAdded}{%
  \def\FrameCommand{\setlength{\fboxsep}{3pt}\colorbox{LXDiffAddBg}}%
  \MakeFramed{\advance\hsize-\width\FrameRestore}%
}{\endMakeFramed}
\newenvironment{LXDiffDeleted}{%
  \def\FrameCommand{\setlength{\fboxsep}{3pt}\colorbox{LXDiffDelBg}}%
  \MakeFramed{\advance\hsize-\width\FrameRestore}%
}{\endMakeFramed}
```

使用 `framed` 宏包的 `MakeFramed` 环境而非 `minipage`，因为前者支持跨页且能容纳
段落、列表等复杂 LaTeX 内容。preamble 取自 target 版本，确保编译环境与最终文档一致。

### 多文件项目适配

- 用户只需选择主文件（如 `main.tex`），系统自动递归展开 `\input{chapters/intro}` 
  等依赖到单文档后再做 diff
- 路径解析优先相对于当前文件目录，其次相对于项目根目录
- `\include` 命令展开时自动添加 `\clearpage`（与 LaTeX 行为一致）
- 路径安全校验：拒绝绝对路径和 `..` 目录穿越

### PDF 存储与清理

- 生成的 PDF 存放在系统临时目录 `latexcellence_git_diff/{job_id}/` 中
- job_id 为 32 位 UUID hex，通过 `GET /api/git-diff/pdf/{job_id}` 下载
- 临时目录由操作系统在重启时清理

## `router.py` — API 路由

Prefix: `/api/git-diff`, Tag: `git-diff`

| 方法 | 路径 | 功能 |
| ---- | ---- | ---- |
| `GET` | `/refs` | 获取当前项目的 Git revision 列表（工作区 + 提交 + 分支 + 标签） |
| `POST` | `/render-pdf` | 生成差异高亮 PDF（接受 `main_file`, `base_ref`, `target_ref`） |
| `GET` | `/pdf/{job_id}` | 下载/预览生成的 PDF 文件 |

### 请求体 (`/render-pdf`)

```json
{ "main_file": "main.tex", "base_ref": "abc1234", "target_ref": "__WORKTREE__" }
```
