# Git 版本管理模块 `git_manage/`

为项目提供 Git 版本控制的最小闭环：自动/手动初始化、状态与变更文件列表、一键全部提交、提交历史，并与 Git 差异高亮（`git_diff`）打通（历史条目可一键预填对比）。不做文件级暂存、回滚、分支与远端操作。

本模块**不调用 LLM**，也不写回用户的 LaTeX 源文件——唯一的文件写入是 init 时生成 `.gitignore`（仅当项目没有时）。

## 与 git_diff 的复用关系

git_manage 不复制 git 执行层，直接复用 `backend/modules/git_diff/service/` 的共享设施：

| 复用项 | 来源 | 说明 |
| --- | --- | --- |
| `run_git` / `run_git_checked` | `git_diff/service/process.py` | git 子进程统一经 SandboxRunner（image_kind=tex）执行，自动获得超时与 `LC_ALL=C` 稳定输出；只读子命令（status/log/rev-parse 等查询类）豁免每用户并发租约，init/add/commit 写操作照常占用；env 注入 `safe.directory=*` 硬化容器 uid 差异 |
| `redact_command_output_for_user` | `git_diff/service/process.py` | 多用户模式下命令输出/错误消息的路径脱敏统一口 |
| `get_project_context` | `git_diff/service/revisions.py` | 仓库定位（含 project_prefix 子目录场景） |

不把函数搬进新的共享 util，直接复用 git_diff 的公开执行层接口（这批函数原名带下划线，随 git_manage 接入已提升为公开名，`_run_process` 等仅同包使用的实现保持私有）；`test_git_diff.py` 以 `monkeypatch.setattr(git_diff_process, "run_git", …)` 为锚点 mock git 调用。

## 初始化语义

- **已初始化判定以"项目根自带 `.git`"为准**（`.git` 目录或文件，兼容 worktree/submodule），而非 git 的父级仓库发现语义——项目落在某个父级仓库内（如部署目录本身是 git 仓库）不视为已初始化。
- **全自动 init（多用户）**：两条上传路径（整包 zip / 分片会话）落位成功后调用 `schedule_auto_init(user_id, project_id)`，经 `asyncio.create_task` 后台执行 `git init` + 写 `.gitignore` + `git add -A` + 初始提交（best-effort，失败仅记日志，不影响上传事务；功能开关关闭或无事件循环时跳过）。项目自带 `.git` 时原样保留。
- **手动兜底**：版本管理页对未初始化项目提供「初始化版本控制」按钮（单用户模式无上传流，主要靠此入口）。
- **`.gitignore` 模板**：LaTeX 编译产物规则（`*.aux`/`*.log`/`*.synctex.gz` 等，不含 `*.pdf`——图表 PDF 常需入库）**及应用内部元数据目录 `.latexcellence/`**（手动主文件标记等）；项目已有 `.gitignore` 时尊重现状不覆盖（此场景的 `.latexcellence/` 排除由 project_manager 写主文件标记时主动兜底补写），原子写入（临时文件 + replace）。
- **变更列表隐藏应用元数据**：`_list_changes` 对 `.latexcellence/` 前缀的条目一律不展示、不计脏标记——与文件树/大纲扫描的隐藏目录口径一致，兜底项目自带 `.gitignore` 未排除或标记文件已被跟踪的仓库。

## .git 配额豁免与独立上限

- **豁免**：项目空间配额与 `size_bytes` 记账不含 `.git`（`quota.dir_size(path, exclude_dirs={".git"})`）。应用点：整包/分片上传落位后的 `size_bytes`、`project_size_on_disk`（对账与写后刷新随之豁免）、回收站恢复重算。解压期配额预检保持保守口径（含 `.git`）。
- **独立上限**：`features.git_manage.git_dir_max_mb`（默认 200MB）。
  - **上传侧**：落位后 `quota.enforce_git_dir_cap()` 校验 `.git` 实际体积，超限 → 整包拒绝（413 `git_history_too_large`，项目不落库、目录清场）。
  - **初始化侧**：init 在 `.gitignore` 就位后经 porcelain 预估初始提交体积（未压缩口径），超限 → 413 并整体回滚（删除本次创建的 `.git` 与本模块写入的 `.gitignore`，项目回到未初始化状态，可调大上限后重试）。
  - **提交侧**：commit 前预估 `当前 .git 体积 + Σ(新增/修改文件 stat 大小)`（删除不计；未压缩口径偏保守），超限 → 413，不产生提交。

## 安全性

- 无用户输入的 ref/hash（不做 checkout/restore 类命令）；唯一自由文本是 commit message，以单参数 argv 传递（无 shell，无注入面）。
- **commit 身份**：每次经 `git -c user.name=… -c user.email=…` 注入，不落全局配置。多用户取操作者平台用户名 + `<user_id>@users.latexcellence.local`；单用户固定 `LaTeXcellence`。
- 多用户模式下面向用户的错误消息一律过 `redact_command_output_for_user`；`index.lock` 并发冲突映射为 409 `git_repository_locked` 而非透传原始 stderr。

## 数据流

```
status: workspace_id → 打开项目根 → 自带 .git? → porcelain v1 -z -uall 解析
        （XY 状态码 → added/modified/deleted；路径经 project_prefix 转项目内相对路径，
         前缀外变更不展示不计脏）+ branch --show-current + .git 体积 + log -1
init:   自带 .git → 409；否则 init → 写 .gitignore（若无）→ porcelain 预估体积
        超限 → 413 并回滚（删除 .git 与本模块写入的 .gitignore）→ add -A → 初始提交
commit: porcelain 无变更 → 400；上限预估超限 → 413；add -A → commit → log -1 取 hash
log:    log --max-count/--skip 分页；空仓库（无提交）返回空列表
```

## Pydantic 模型

- `GitChangeItem`（path, kind）
- `GitLastCommit`（short_hash, subject, author, date）
- `GitStatusResult`（initialized, current_branch, is_dirty, changes, git_dir_size_bytes, git_dir_max_bytes, last_commit；未初始化时 initialized=false 其余空值）
- `GitCommitRequest`（message，1–500 字符）
- `GitCommitResult`（short_hash, subject, committed_count, git_dir_size_bytes）
- `GitLogEntry`（hash, short_hash, subject, author_name, date_iso）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `get_status(workspace_id)` | 状态总览（未初始化时 initialized=false） |
| `init_repository(workspace_id)` | 为当前打开项目初始化版本库（已初始化 409） |
| `commit_all(workspace_id, message)` | add -A + commit（空变更 400 / 空消息 422 / 超上限 413 / index.lock 409） |
| `get_log(workspace_id, limit, offset)` | 提交历史分页 |
| `schedule_auto_init(user_id, project_id)` | 上传落位后调度后台自动初始化（best-effort） |
| `wait_auto_init_tasks()` | 等待全部自动初始化任务结束（测试用） |

## API 路由

Prefix: `/api/git-manage`, Tag: `git-manage`。读端点 `require_workspace_read`（owner/editor/viewer 可读），写端点 `require_workspace_write`（viewer 403）。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/status` | 版本控制状态（initialized/changes/.git 用量/最近提交） |
| POST | `/init` | 初始化版本控制（已初始化 → 409；预估超上限 → 413） |
| POST | `/commit` | 一键全部提交（body `{message}`） |
| GET | `/log` | 提交历史（limit/offset 分页） |

**前端**：`VersionManagePage.jsx`（路由 `/versions`）展示状态卡（分支、脏标记、.git 用量进度条、最近提交）、变更文件列表（点击跳 `/editor?file=<path>`）、提交表单（`useCanWrite` 控制 viewer 禁用）与分页历史；历史条目「对比此版本与工作区」跳 `/git-diff?base=<hash>&target=__WORKTREE__`（GitDiffPage 经 `useSearchParams` 预填后清除参数）。上传自动初始化对用户无感。

## 配置项

`config.json` 中 `features.git_manage` 部分：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用 Git 版本管理模块（含上传自动初始化与 `/api/git-manage/*` 路由） |
| `git_dir_max_mb` | int | `200` | 单项目 `.git` 目录大小上限（MB），超限拒绝上传/初始化/提交 |
