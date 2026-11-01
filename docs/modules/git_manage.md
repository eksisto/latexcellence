# Git 版本管理模块 `git_manage/`

为项目提供最小完整的 Git 版本控制：自动/手动初始化、状态与变更文件列表、一键全部提交、提交历史，以及**源码级变更查看与选择性恢复**（工作区 vs HEAD 的单文件 diff、按 hunk 撤销、整文件恢复、单个提交的只读源码浏览），并与 Git 差异高亮（`git_diff`）联动（历史条目可一键预填对比）。不做文件级暂存、分支与远端操作。历史提交不可恢复（只读）。

本模块**不调用 LLM**，也不写回用户的 LaTeX 源文件——文件写入仅有两处：init 时生成 `.gitignore`（仅当项目没有时），恢复时在 `.latexcellence/tmp/` 下落盘临时 patch（用后即删）。

## 与 git_diff 的复用关系

git_manage 不复制 git 执行层，直接复用 `backend/modules/git_diff/service/` 的共享设施：

| 复用项 | 来源 | 说明 |
| --- | --- | --- |
| `run_git` / `run_git_checked` | `git_diff/service/process.py` | git 子进程统一经 SandboxRunner（image_kind=tex）执行，自动获得超时与 `LC_ALL=C` 稳定输出；只读子命令（status/log/diff/show 等查询类）豁免每用户并发租约，init/add/commit/restore/apply 写操作照常占用；env 注入 `safe.directory=*` 硬化容器 uid 差异 |
| `redact_command_output_for_user` | `git_diff/service/process.py` | 多用户模式下命令输出/错误消息的路径脱敏统一口 |
| `get_project_context` | `git_diff/service/revisions.py` | 仓库定位（含 project_prefix 子目录场景） |

不把函数搬进新的共享 util，直接复用 git_diff 的公开执行层接口（这批函数原名带下划线，随 git_manage 接入已提升为公开名，`_run_process` 等仅同包使用的实现保持私有）。`test_git_diff.py` 以 `monkeypatch.setattr(git_diff_process, "run_git", …)` 为锚点 mock git 调用。

## 初始化语义

- **已初始化判定以"项目根自带 `.git`"为准**（`.git` 目录或文件，兼容 worktree/submodule），而非 git 的父级仓库发现语义——项目落在某个父级仓库内（如部署目录本身是 git 仓库）不视为已初始化。
- **全自动 init（多用户）**：两条上传路径（整包 zip / 分片会话）落位成功后调用 `schedule_auto_init(user_id, project_id)`，经 `asyncio.create_task` 后台执行 `git init` + 写 `.gitignore` + `git add -A` + 初始提交（best-effort，失败仅记日志，不影响上传事务；功能开关关闭或无事件循环时跳过）。项目自带 `.git` 时原样保留。
- **手动兜底**：版本管理页对未初始化项目提供「初始化版本控制」按钮（单用户模式无上传流，主要靠此入口）。
- **`.gitignore` 模板**：LaTeX 编译产物规则（`*.aux`/`*.log`/`*.synctex.gz` 等，不含 `*.pdf`——图表 PDF 常需入库）**及应用内部元数据目录 `.latexcellence/`**（手动主文件标记等）。项目已有 `.gitignore` 时不覆盖（此场景的 `.latexcellence/` 排除由 project_manager 写主文件标记时主动兜底补写），原子写入（临时文件 + replace）。
- **变更列表隐藏应用元数据**：`_list_changes` 对 `.latexcellence/` 前缀的条目一律不展示、不计脏标记——与文件树/大纲扫描的隐藏目录口径一致，兜底项目自带 `.gitignore` 未排除或标记文件已被跟踪的仓库。

## 源码级 diff 查看与选择性恢复

### diff 语义

- **性能约束**：沙箱（docker）下每次 git 调用都是一次容器往返，file-diff 采用 **diff-first** 排序——常见路径（已跟踪文件的修改/删除）只跑一次 `git diff HEAD`，kind 从 diff 头的 `new file mode` / `deleted file mode` 推导。空 diff 才补一次 status 区分干净/未跟踪。diff 失败（unborn HEAD）按无基线处理。全程 cwd=项目根 + cwd 相对 pathspec，不需要仓库根定位，省去 `get_project_context` 的 rev-parse——常见路径单次调用完成。commit 模式与 commit-files 同理单次 `git show` 完成（哈希过正则白名单后由 git 自身校验，无效哈希按退出码映射 400；commit-files 用 `--relative` 直接输出项目内相对路径并排除子树外变更）。
- **工作区模式**（`commit` 缺省）：单文件 `git diff HEAD`（无暂存 UI，index 与 HEAD 恒一致）。**未跟踪文件**（porcelain `??`）用 `git diff --no-index /dev/null` 生成"全文新增" diff——新文件没有可恢复的基线，`restorable=false`。
- **commit 模式**：`git show <hash>`（该提交 vs 其父提交），只读、`restorable` 恒 false。哈希经正则白名单校验后原样作为单一 argv 传给 git show，无效哈希由 git 报错映射为 400 `git_commit_not_found`。文件清单端点 `get_commit_files` 同源（`--name-status --relative`），`.latexcellence/` 等隐藏前缀不展示（子树外变更由 `--relative` 天然排除，与 status 口径一致）。两处 `git show` 均带 `--first-parent`：merge 提交默认输出空 combined diff，会被误判为「无变更」，按第一父提交呈现变更（git < 2.31 无此含义，优雅退化为空输出而非报错）。
- **解析**：服务端把 unified diff 解析为结构化 hunk（`context`/`add`/`del` 行 + 行文本），每个 hunk 同时携带**原文切片 `raw`**（含 hunk 头与 `\ No newline` 标记，原样回传用于恢复）。文件头（diff --git/index/---/+++）不返回——恢复时由服务端重新生成，客户端永远不提供 patch 头。
- **降级展示**：二进制 diff（无 hunk 头、`Binary files … differ` 标记）→ `is_binary=true` 无 hunk；工作区文件 >2MB → `too_large=true`；diff 超 2000 行在 **hunk 边界**截断（`truncated=true`，已返回的 raw 均为完整 hunk）；单 hunk 即超限时 hunks 为空、仅可整文件恢复。页面数据过期（文件已无变更）时 `kind` 返回空串而非报错。
- **无提交仓库**（unborn HEAD）：`git diff HEAD` 报错即视为无基线，存在的工作区文件按"全文新增"展示，不可恢复。

### 恢复语义（fail-closed）

- **整文件**（`mode=file`）：`git restore --source=HEAD --staged --worktree -- <path>`，暂存区与工作区一并回退——只回退工作区会让残留的暂存条目在恢复后仍显示为已变更（`A → AD` / `M → M`），「已恢复」提示失实。staged-added（HEAD 无此文件）经 `--staged` 连同 index 条目一起清除，整文件消失。
- **按 hunk**（`mode=hunks`）：只回退工作区、不动暂存区（部分撤销不越权改写用户的暂存状态；提交路径统一 `add -A` 以工作区为准，残留暂存不会复活已撤销的 hunk）。服务端以**相同参数重新生成 diff**，前端回传的 hunk raw 切片必须**逐字命中**当前 diff（按位置排序去重后拼接 patch，文件头取自服务端 diff）→ `git apply -R --check` 预检 → `git apply -R` → 删除临时 patch。任一环节失配即 409 `git_worktree_changed`——**工作区在展示与恢复之间被任何一方改动（本人编辑器自动保存/协作者写入）时，恢复绝不覆盖新编辑**。`git apply` 默认拒绝仓库外路径，patch 文件以相对路径传参（兼容 project_prefix 子目录场景，`.latexcellence/` 已 gitignore 且 status 兜底过滤）。
- **守卫**：未跟踪文件 400 `git_file_untracked`、无变更 400 `git_no_changes`、unborn HEAD 400 `git_restore_no_base`、切片非法 400/422 `git_patch_invalid`、路径越界/隐藏前缀 400 `git_file_path_invalid`。
- 前端联动：恢复成功后刷新 status，并检查编辑器 store 中该文件的 tab——缓冲区干净则静默重载磁盘内容，有未保存修改则弹确认（重新加载丢弃 / 保留编辑，后者由保存路径的 409 乐观锁兜底）。

## .git 配额豁免与独立上限

- **豁免**：项目空间配额与 `size_bytes` 记账不含 `.git`（`quota.dir_size(path, exclude_dirs={".git"})`）。应用点：整包/分片上传落位后的 `size_bytes`、`project_size_on_disk`（对账与写后刷新随之豁免）、回收站恢复重算。解压期配额预检保持保守口径（含 `.git`）。
- **独立上限**：`features.git_manage.git_dir_max_mb`（默认 200MB）。
  - **上传侧**：落位后 `quota.enforce_git_dir_cap()` 校验 `.git` 实际体积，超限 → 整包拒绝（413 `git_history_too_large`，项目不落库、目录清场）。
  - **初始化侧**：init 在 `.gitignore` 就位后经 porcelain 预估初始提交体积（未压缩口径），超限 → 413 并整体回滚（删除本次创建的 `.git` 与本模块写入的 `.gitignore`，项目回到未初始化状态，可调大上限后重试）。
  - **提交侧**：commit 前预估 `当前 .git 体积 + Σ(新增/修改文件 stat 大小)`（删除不计；未压缩口径偏保守），超限 → 413，不产生提交。

## 安全性

- commit 模式仅接受 hex 哈希（正则白名单 + rev-parse 校验）。自由文本为 commit message 与恢复请求的 hunk raw 切片，前者单参数 argv 传递、后者落盘 `.latexcellence/tmp/` 后以路径参数传给 `git apply`（均无 shell，无注入面；apply 默认拒绝越界路径）。
- **commit 身份**：每次经 `git -c user.name=… -c user.email=…` 注入，不落全局配置。多用户取操作者平台用户名 + `<user_id>@users.latexcellence.local`。单用户固定 `LaTeXcellence`。
- 多用户模式下面向用户的错误消息一律过 `redact_command_output_for_user`。`index.lock` 并发冲突映射为 409 `git_repository_locked` 而非透传原始 stderr。git apply 失败的原始 stderr 只进服务端日志。

## 数据流

```
status:      workspace_id → 打开项目根 → 自带 .git? → porcelain v1 -z -uall 解析
             （XY 状态码 → added/modified/deleted；路径经 project_prefix 转项目内相对路径，
              前缀外变更不展示不计脏）+ branch --show-current + .git 体积 + log -1
init:        自带 .git → 409；否则 init → 写 .gitignore（若无）→ porcelain 预估体积
             超限 → 413 并回滚（删除 .git 与本模块写入的 .gitignore）→ add -A → 初始提交
commit:      porcelain 无变更 → 400；上限预估超限 → 413；add -A → commit → log -1 取 hash
log:         log --max-count/--skip 分页；空仓库（无提交）返回空列表
file-diff:   路径校验（path_guard + 拒绝 .git/.latexcellence/目录）→
             commit 模式：哈希正则白名单 → git show --first-parent
             （退出码即校验，失败 400）→ 解析 hunk（只读）；
             工作区模式（diff-first，常见路径单次调用）：git diff HEAD →
             非空 → diff 头推导 kind + 解析；失败（unborn）→ 全文新增/空 kind；
             空输出 → 单次 status 区分干净("")/未跟踪(?? → --no-index 全文新增)；
             二进制/超长/超限按边界截断或降级
commit-files: 哈希正则白名单 → show --name-status --relative --first-parent
             （退出码即校验，失败 400）
             → 隐藏目录过滤 → 清单（上限 1000 条）
restore:     路径校验 → 单路径 porcelain 守卫（无变更 400 / ?? 400 / unborn 400）→
             file 模式：git restore --source=HEAD --staged --worktree
             （暂存区一并回退）；
             hunks 模式：重新生成 diff → raw 切片逐字命中（失配 409）→ 拼 patch
             （服务端文件头）→ 写 .latexcellence/tmp → apply -R --check（失配 409）
             → apply -R → 删临时 patch
```

## Pydantic 模型

- `GitChangeItem`（path, kind）
- `GitLastCommit`（short_hash, subject, author, date）
- `GitStatusResult`（initialized, current_branch, is_dirty, changes, git_dir_size_bytes, git_dir_max_bytes, last_commit；未初始化时 initialized=false 其余空值）
- `GitCommitRequest`（message，1–500 字符）
- `GitCommitResult`（short_hash, subject, committed_count, git_dir_size_bytes）
- `GitLogEntry`（hash, short_hash, subject, author_name, date_iso）
- `GitDiffLine`（type: context/add/del, text）
- `GitDiffHunk`（header, lines, raw 原文切片, additions, deletions）
- `GitFileDiffResult`（path, kind, is_binary, too_large, truncated, additions, deletions, hunks, restorable, commit_hash）
- `GitCommitFileItem`（path, kind）/ `GitCommitFilesResult`（hash, files, truncated）
- `GitRestoreRequest`（path, mode: file/hunks, hunks 原文切片）/ `GitRestoreResult`（path, mode, restored_hunks）

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `get_status(workspace_id)` | 状态总览（未初始化时 initialized=false） |
| `init_repository(workspace_id)` | 为当前打开项目初始化版本库（已初始化 409） |
| `commit_all(workspace_id, message)` | add -A + commit（空变更 400 / 空消息 422 / 超上限 413 / index.lock 409） |
| `get_log(workspace_id, limit, offset)` | 提交历史分页 |
| `get_file_diff(workspace_id, path, commit)` | 单文件源码级 diff（结构化 hunks + raw；降级标记） |
| `get_commit_files(workspace_id, commit)` | 单个提交变更文件清单（抽屉两级导航第一级） |
| `restore_changes(workspace_id, request)` | 整文件 / 按 hunk 恢复（fail-closed，过期切片 409） |
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
| GET | `/file-diff` | 单文件源码 diff（query `path`、可选 `commit`；结构化 hunks） |
| GET | `/commit-files` | 单个提交变更文件清单（query `commit`） |
| POST | `/restore` | 恢复工作区变更（body `{path, mode, hunks?}`；过期切片 409） |

**前端**：`VersionManagePage.jsx`（路由 `/versions`）展示状态卡（分支、脏标记、.git 用量进度条、最近提交）、变更文件列表（点行打开 diff 抽屉、行内图标跳 `/editor?file=<path>`）、提交表单（`useCanWrite` 控制 viewer 禁用）与分页历史。历史条目「查看源码变更」打开只读抽屉（文件清单 → 单文件 diff 两级导航），「对比此版本与工作区」跳 `/git-diff?base=<hash>&target=__WORKTREE__`（渲染级 PDF diff，与源码 diff 并存定位不同）。diff 抽屉组件 `components/versions/DiffDrawer.jsx`（worktree/commit 双模式）+ `DiffView.jsx`（自研 unified 渲染，hunk 勾选）+ `changeKind.js`（变更类型元数据共用）；右侧面板为 `components/ui/sheet.jsx`（Base UI Dialog 同构）。status 加载后前端并行**预取**变更文件（上限 10）的源码 diff 入内存缓存，点击抽屉命中即渲染、随后静默刷新保鲜。恢复/提交/刷新 status 均使缓存失效。上传自动初始化对用户无感。

## 配置项

`config.json` 中 `features.git_manage` 部分：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用 Git 版本管理模块（含上传自动初始化与 `/api/git-manage/*` 路由） |
| `git_dir_max_mb` | int | `200` | 单项目 `.git` 目录大小上限（MB），超限拒绝上传/初始化/提交 |

diff 截断（2000 行）、超大文件阈值（2MB）、提交文件清单上限（1000 条）为模块内常量，不作为配置项暴露。
