# 多用户模式 v2 设计方案

> 状态：设计完成，待实施
> 日期：2026-08-22
> 前置文档：[multi-user-plan.md](multi-user-plan.md)（v1，已全部实施）
> 适用范围：Docker 沙箱、LLM 每用户治理、rar 上传、共享项目撤销恢复（反向补丁）、衍生数据记账与清理、大文件分片上传、SMTP 邮件、多进程/多实例部署

---

## 1. 背景与事实基础

### 1.1 v1 交付回顾与硬约束

v1 已交付双模式切换、认证审批、用户空间隔离、配额、权限协作、回收站、管理后台与迁移工具。v1 明确以**单进程**为运行前提（`docs/deployment.md` 警告不要多 worker/多实例启动），并预留了本文档的七项能力。

v1 遗留缺口（P7 评审记录）：每用户 LLM 限流/并发上限（`llm_rate_limit_per_minute` / `llm_max_concurrency_per_user`）仅有配置项与设置界面，未强制执行；实际 LLM 并发由全局 `llm.max_concurrency` 控制。

### 1.2 代码事实基线（2026-08 代码探查结论）

以下事实是本方案各设计的直接依据（行号以当前代码为准）：

**外部命令执行点**

| 族 | 收口点 | 说明 |
| --- | --- | --- |
| TeX 编译 | `backend/utils/tex_compile.py::run_tex_compile` | 唯一编译入口，上游调用方：compile 自动编译、plot_gen TikZ 编译、template_gen Agent 编译、style_modify shadow 编译、git_diff 差异 PDF 渲染 |
| git | `backend/modules/git_diff/service/process.py::_run_process` | 全部为本地只读子命令（rev-parse/status/log/show/archive 等），无网络操作，已有 revision 白名单防注入 |
| latexdiff | `git_diff/service/diff.py::_run_latexdiff` | 文本进文本出，固定 120s 超时 |
| pandoc | `modules/format_converter/service.py` | stdin/临时文件方式转换用户直传内容 |
| plot Python | `modules/plot_gen/executor.py::_run_script` + `sandbox_guard.py` | 执行 LLM 生成的脚本；现有审计钩子拦截命令执行/网络/ctypes/越界读写，但无 CPU/内存限制 |
| latexindent | `utils/latexindent_utils.py` | 格式化 LLM 输出/OCR 结果 |

TeX 可执行文件全部靠后端进程 PATH 裸名解析，无路径配置项。压缩包解压（zip/py7zr）在进程内完成，不调外部命令。

**撤销机制**：撤销栈在每个工作区专属的 `BackupManager` 内存中（`file_ops.py`，整文件全文快照，每文件每栈上限 50 层），无持久化、无操作者归属、无时间戳元数据。所有项目文件写路径统一经过 `LatexProject.apply_change` / `apply_whole_files`（写前 push 快照）。style_modify 的 `OperationMeta`（operations 目录 before/applied 全文 + 前后 MD5 + workspace 归属 + 状态机，撤销前校验"当前 MD5 == applied_md5"）是现成的操作级回滚范本。共享项目禁用撤销的 guard 在 `access_control.reject_undo_redo_if_shared`，但 writing_chat 编辑工具/apply-edit 与 style_modify apply/undo 这些 AI 写路径未被该禁令覆盖。

**进程内全局状态**：多实例部署下的关键失效点包括——workspace 绑定注册表（A 进程打开、B 进程查不到）、逐段检查 CheckOperationContext（operation_id 跨进程找不到）、登录限流计数器（阈值被副本数放大）、writing_chat pending 工具确认（SSE 在 A 等确认、确认请求打到 B 永不触发）、DDG challenge 存储、配置热重载仅作用于单进程。后台清理任务重复执行本身幂等安全。`sessions` 表无过期删除任务；SQLite schema 仅靠 `CREATE IF NOT EXISTS` 建立，无版本迁移机制。

**上传管线**：`POST /api/projects` 整包 multipart 流式落 staging（1 MiB chunk 边写边限大小），zip 逐条目预检+限拷贝、7z 整包解压+监视线程硬中断；解压安全校验链、配额记账、命名去重、原子落位均可被分片上传复用；缺"上传会话"持久实体。staging 兜底清理按 mtime 保留 1 小时。

**衍生数据记账**：每次会话创建/用量查询都对四个衍生目录做全量递归扫描（无缓存），项目空间则有 `projects.size_bytes` 缓存 + 每小时对账；这是多用户规模化最现实的性能瓶颈。

**日志**：`TimedRotatingFileHandler` 多进程共写同一文件在轮转时会产生 rename 竞争，非多进程安全。

---

## 2. 目标与非目标

### 目标（v2）

- Docker 沙箱隔离**全部**外部命令执行（latexmk/texfot、Python 绘图、pandoc、git/latexdiff、latexindent、模板与样式编译），按容器/用户/全局限制资源；
- 每用户 LLM 限流与并发上限强制执行（补齐 v1 P7 缺口）；
- rar 压缩包上传支持（unar）；
- 共享项目撤销/重做恢复：反向补丁语义，只回退当前工作区自己的修改，冲突即拒绝；
- 衍生数据用量实时记账优化（消除每请求全盘扫描）+ 更细粒度清理策略与自助清理入口；
- 大文件分片上传与断点续传（含浏览器目录直传原生分片化）；
- SMTP 邮件通知（审批、重置密码告知、配额预警）与自助找回密码；
- 多进程/多实例部署支持（单机 Docker Compose 一等公民，状态外置到 SQLite，无需粘性会话）。

### 非目标（v2 明确不做）

- 不做多机横向扩展（共享文件系统 NFS、外部 Postgres/Redis）；单机 compose 为一等公民，裸机部署保留文档支持；
- 不引入 Redis 或消息队列；
- 不做项目级永久编辑历史（轻量 git 化）、段级 diff 反向补丁、三方合并撤销；
- 不做注册邮箱验证（邮箱仍可选）；
- 不做沙箱容器 warm pool（每编译一次一个容器）;
- 不做管理员强制清理任意用户衍生数据；
- 单用户模式行为保持现状（除 sandbox.mode=auto 时可选走沙箱）。

## 3. 关键决策记录

| 主题 | 决策 |
| --- | --- |
| 部署形态 | 单机 Docker Compose 一等公民（后端容器化 + 官方 compose），同时保留裸机部署文档；开发机无 Docker 可正常运行（回落模式） |
| 扩展单元 | 容器副本（默认 replicas=2，可调；容器内 uvicorn workers=1）；状态外置后任意副本可服务任意请求，无需粘性会话 |
| 共享状态存储 | 全部入 SQLite(WAL)（新表）+ flock 文件锁（同机共享卷有效）；不引入 Redis |
| 日志 | 容器模式只写 stdout/stderr 交 docker logs 收集；裸机保持现有按天轮转（单进程前提不变） |
| schema 演进 | 引入轻量版本迁移机制（PRAGMA user_version + 有序迁移步骤），替代纯 IF NOT EXISTS |
| LLM 治理 | 纳入 v2，与沙箱同阶段落地；计数入 SQLite；超限 429（并发超限不排队） |
| 阶段顺序 | 安全优先：P0 配置与迁移骨架 → P1 沙箱+LLM 治理 → P2 rar → P3 反向补丁 → P4 记账清理 → P5 分片上传 → P6 SMTP → P7 多实例收口 |
| 沙箱运行时 | 宿主 Docker daemon；`sandbox.mode = auto \| docker \| local`：auto 探测到 Docker 即用（单用户也受益），否则回落本地执行；多用户生产环境文档推荐 docker |
| 沙箱镜像 | 按用途拆分：backend / tex / convert / plot 各自独立 Dockerfile，仓库内版本锁定，构建参数化 |
| TeX Live 方案 | scheme-medium + 参数化常用宏包清单（构建期安装），镜像约 1–1.5GB |
| 沙箱资源默认 | 单容器 1 CPU / 1GB 内存 / 256 pids / 无网络 / tmpfs 限容；每用户并发沙箱 1、全局并发池 4；全部可配置 |
| 沙箱范围 | 全部外部命令进沙箱（含 git）；local 回落模式下保持现状行为 |
| 挂载语义 | bind mount 使用**同绝对路径**（host_path:host_path），容器内外路径一致，日志解析/错误定位零改动；容器以宿主后端同 uid 运行 |
| rar 工具 | unar（Debian main 源，原生 RAR5）；后端镜像内置；stdin 关闭防交互密码挂起；加密/分卷拒绝沿用 v1 口径 |
| 撤销语义 | 冲突即拒绝：当前文件 MD5 必须等于自己该操作的 after_md5 才可回退；LIFO 按操作逆序；redo 对称恢复 |
| 补丁存储 | SQLite 存操作索引，before/after 全文快照存磁盘（用户空间 `.edit_logs/`，不进项目目录、不进下载包）；默认每工作区 50 条，TTL 跟随工作区会话（24h 无活动过期） |
| 共享撤销禁令 | 反向补丁落地后解除；全部写路径（含 writing_chat 工具/apply-edit、style_modify）统一记账；配额超限回滚改用不污染撤销栈的直接恢复 |
| 分片上传 | 自研 chunk API（init/part/status/complete/abort）；每片 MD5 校验；前端 localStorage 记 upload_id 续传；目录直传弃 JSZip 整包改原生逐文件分片；仅多用户模式提供 |
| 邮件范围 | 待审批提醒、审批结果、重置密码告知、自助忘记密码、配额预警（80%/95% 迟滞）；不做注册邮箱验证；SMTP 未配置时全链路优雅降级 |
| 邮箱唯一性 | 非空邮箱加部分唯一索引；存量重复不迁移，自助找回遇重复提示联系管理员 |
| 邮件语言 | 模板提供中/英两套，跟随站点默认语言发送 |
| 记账优化 | 分类用量入 DB 缓存 + 检查点只扫触达子目录 + 每小时对账；消除每请求全盘 rglob |
| 清理形态 | 后台 TTL/对账优化 + 工作台分类用量展示 + 按类自助清理；不做管理员强制清理；writing_chat 会话保留期默认 0（不自动删） |
| 测试 | 新增 docker pytest marker（无 daemon 自动 skip）；Service 层继续 Mock LLM；compose 冒烟脚本验证多副本 |
| CPU 架构 | amd64 与 arm64（aarch64）双架构一等支持；沙箱镜像提供多架构构建路径，验收在两种架构上各跑冒烟 |

## 4. 总体架构

### 4.1 目标部署拓扑（P7 收口后）

```
浏览器 ──HTTPS──▶ 反向代理（Caddy/nginx，可选容器）
                      │  （服务名 DNS 解析到全部健康副本）
              ┌───────┴────────┐
              ▼                ▼
        backend 副本 A     backend 副本 B      ← compose 同一 service，--scale app=N
              │                │
              ├── SQLite（WAL，共享卷 data/）：业务表 + v2 新表（绑定/限流/上下文/队列…）
              ├── data/users/ 共享卷：项目空间、衍生空间、.edit_logs/
              ├── data/staging/ 共享卷：分片与解压暂存
              └── /var/run/docker.sock（只读挂载）──▶ Docker daemon
                                                        ├── latexcellence-tex 容器（bind mount 项目目录）
                                                        ├── latexcellence-convert / -plot 容器
                                                        └── （sandbox.mode=local 时无）
```

要点：

- SQLite 数据库文件、用户空间、staging 位于同一宿主 bind 卷，多副本通过 WAL + busy_timeout 共享访问；flock 用于跨副本会话互斥（同机共享卷上有效）；
- 后端容器挂载 `/var/run/docker.sock` 以驱动沙箱容器；风险声明见 §16；
- SSE 长连接天然粘在所属连接的副本上，无需应用层粘性；跨请求小状态（pending 确认、challenge、operation context）入 SQLite 后任意副本可续。

### 4.2 配置结构新增总览

```json
{
  "sandbox": {
    "mode": "auto",
    "image_tex": "latexcellence-tex:latest",
    "image_convert": "latexcellence-convert:latest",
    "image_plot": "latexcellence-plot:latest",
    "cpus": 1.0,
    "memory_mb": 1024,
    "pids_limit": 256,
    "tmpfs_mb": 512,
    "max_concurrent_global": 4,
    "max_concurrent_per_user": 1,
    "startup_grace_seconds": 10
  },
  "smtp": {
    "enabled": false,
    "host": "",
    "port": 465,
    "encryption": "ssl",
    "username": "",
    "password": "",
    "from_address": "",
    "from_name": "LateXcellence",
    "site_base_url": "",
    "max_send_per_minute": 10,
    "retry_attempts": 3
  },
  "multi_user": {
    "...现有 19 项保持不变...": "",
    "edit_log_max_ops": 50,
    "edit_log_max_op_mb": 8,
    "edit_log_ttl_hours": 24,
    "upload_session_ttl_hours": 24,
    "chunk_part_max_mb": 8,
    "writing_chat_retention_days": 0,
    "quota_warn_percent": 80,
    "quota_critical_percent": 95,
    "quota_alert_reset_percent": 75
  }
}
```

- `sandbox` 为顶层新节（单用户模式 auto 下同样生效）；`smtp` 为顶层新节；其余追加进现有 `multi_user` 节；
- `MultiUserConfig` 现为 `extra="forbid"`，新增字段需同步 `config.example.json`、设置分区组件、`SettingsDialog.jsx` 的 `setEnabledFeatures`、`WelcomePage.jsx` 的 `featureGroups`（按 AGENTS.md 配置同步规范）；
- `sandbox.password` 类敏感项（smtp.password）沿用现有掩码协议（`***` 前缀保留原值）。

### 4.3 数据模型新增总览（SQLite）

所有新表纳入 §12.9 的版本迁移框架建立：

| 表 | 用途 | 引入阶段 |
| --- | --- | --- |
| `schema_version`（或 PRAGMA user_version） | schema 版本迁移 | P0 |
| `workspace_edit_ops` | 反向补丁操作索引（op_id、project_id、actor_user_id、workspace_id、op_group_id、source_module、status active/undone/failed、前后 MD5、created_at/undone_at） | P3 |
| `user_derived_usage(user_id, subdir, size_bytes, updated_at)` | 衍生用量缓存（按类） | P4 |
| `upload_sessions(id, user_id, name, kind, total_bytes, file_count, status, manifest JSON, created_at, last_activity_at)` | 分片上传会话 | P5 |
| `email_outbox(id, recipient, subject, body_text, body_html, status queued/sent/failed, attempts, last_error, created_at, sent_at)` | 邮件发送队列 | P6 |
| `password_reset_tokens(user_id, token_hash UNIQUE, expires_at, used_at)` | 自助找回令牌（只存哈希） | P6 |
| `user_alert_state(user_id, kind, sent_at)` | 配额预警迟滞状态 | P6 |
| `rate_limit_events(kind, identity, ts)` | 登录/LLM 限流滑动窗口事件 | P1b/P7 |
| `llm_concurrency_leases(task_id UNIQUE, user_id, acquired_at, expires_at)` | 每用户 LLM 并发租约 | P1b/P7 |
| `sandbox_slots(slot_id, user_id, acquired_at, expires_at)` | 沙箱并发池租约 | P1a/P7 |
| `operation_contexts(operation_id PK, payload JSON, created_at)` | 逐段检查上下文外置 | P7 |
| `pending_interactions(id, kind confirm/clarify/ddg_challenge, payload JSON, expires_at)` | 写作助手 pending 确认、DDG challenge 外置 | P7 |
| `workspace_bindings(workspace_id PK, user_id, project_id, owner_id, root_path, last_active_at)` | 工作区绑定持久化 | P7 |
| `maintenance_lease(key PK, holder, expires_at)` | 维护循环选主租约 | P7 |

users 表变更：新增非空邮箱部分唯一索引（`UNIQUE(email COLLATE NOCASE) WHERE email != ''`）。sessions 表补充过期删除任务（并入维护循环）。

### 4.4 API 新增与变更总览

| 方法/路径 | 阶段 | 说明 |
| --- | --- | --- |
| `POST /api/projects/upload-sessions` | P5 | 创建上传会话（manifest 预检 + 配额预检） |
| `PUT /api/projects/upload-sessions/{sid}/parts/{file_index}/{chunk_index}` | P5 | 上传分片（≤ chunk_part_max_mb，X-Part-MD5 校验，幂等覆盖，自动续期心跳） |
| `GET /api/projects/upload-sessions/{sid}` | P5 | 查询进度（已收分片位图），用于断点续传；同时续期 |
| `POST /api/projects/upload-sessions/{sid}/complete` | P5 | 校验完整性 → 拼装 → 复用解压校验链/归一化/配额复核/命名/落位 |
| `DELETE /api/projects/upload-sessions/{sid}` | P5 | 中止并清理 |
| `POST /api/auth/forgot-password` | P6 | 公开；按邮箱发重置令牌（限流 + 重复邮箱降级提示） |
| `POST /api/auth/reset-password` | P6 | 公开；凭令牌重置密码并吊销全部会话 |
| `PUT /api/auth/profile` | P6 | 绑定/修改邮箱（需当前密码确认） |
| `POST /api/auth/cleanup-derived-data` | P4 | 用户自助清理指定类别衍生数据 `{category}` |
| `GET /api/auth/me`（扩展） | P4 | 增加 `derived_usage` 分类明细 |
| `POST /api/project/undo` / `redo`（行为变更） | P3 | 多用户模式改为反向补丁回滚/重放；共享项目禁令解除；409 语义见 §8 |
| `GET /api/project/can-undo` / `can-redo` / `undo-status`（行为变更） | P3 | 返回基于补丁日志的真实可撤销/可重做状态 |

新增错误码（同步前端 zh / zh-TW / en 三语言 `errors` 命名空间）：`sandbox_unavailable`（503）、`sandbox_busy`（429，沙箱并发池满）、`upload_session_not_found`（404）、`upload_session_expired`（410）、`reset_token_invalid`（400）、`email_not_available`（400，未绑邮箱/重复邮箱时的找回降级提示）、`quota_exceeded_recover`（413，撤销导致超配额）等。P1b 补充：`llm_rate_limited`（429，LLM 限流）与 `llm_concurrency_limited`（429，LLM 并发满），均附 `Retry-After` 头与 `retry_after` 秒数明细。

### 4.5 与现有代码的衔接点

| 现有模块 | 变更 |
| --- | --- |
| `backend/utils/tex_compile.py` | 内部执行后端替换为 SandboxRunner（docker/local 双实现），调用方签名不动；退出码/结果契约翻译 |
| `modules/git_diff`、`format_converter`、`plot_gen`、`latexindent_utils` | 同上接入 SandboxRunner；plot_gen 审计钩子保留为纵深防御 |
| `core/workspace_manager.py` | 多用户模式绑定读写改为 DB 表 + 进程内 LatexProject 可重建缓存（P7）；补丁日志生命周期钩子（P3） |
| `core/project_manager.py`（apply_change / apply_whole_files） | 增加 actor 归属传播（ContextVar 取 current_user_id）；写成功后记补丁 op（P3，仅多用户模式） |
| `core/access_control.py` | 移除共享项目撤销禁令 guard，替换为补丁归属校验（P3） |
| `modules/projects/service.py` | 配额超限回滚不再调用 `project.undo`，改为直接内容恢复（P3）；上传管线旁增分片会话分支（P5） |
| `core/multiuser/quota.py` | 衍生用量改读 DB 缓存 + 检查点增量扫描；对账任务刷新缓存（P4） |
| `core/multiuser/maintenance.py` | 并入：编辑日志/上传会话/邮件队列/令牌/限流事件/租约清理、sessions 过期删除、配额预警检查、维护租约选主（P4–P7） |
| `core/multiuser/db.py` | 版本迁移框架 + 全部新表（P0 起） |
| `main.py` lifespan | 注册 sandbox runner 初始化/关闭、smtp 发送循环、新清理任务；容器模式 stdout 日志开关 |
| 前端 `UploadProjectDialog.jsx` | 分片队列（并发 3）、暂停/续传、进度位图展示；移除 JSZip 打包路径 |
| 前端工作台/管理面板 | 分类用量条 + 自助清理；忘记密码入口；设置页新增 smtp/sandbox 管理员分区 |

## 5. P1a — Docker 沙箱

### 5.1 执行入口设计

新增 `backend/utils/sandbox/` 包：

```
sandbox/
├── runner.py          # SandboxRunner：统一执行入口（探测、选后端、并发租约、结果翻译）
├── local_backend.py   # 本地执行后端 = 现有 subprocess 行为的封装（回落路径）
├── docker_backend.py  # Docker 执行后端（docker SDK）
└── models.py          # ExecSpec / ExecResult 契约（命令、cwd、挂载、env 白名单、timeout、stdout 截断）
```

- 各调用方把"要执行的命令 + 工作目录 + 超时"描述成 `ExecSpec`，由 runner 选择后端；`run_tex_compile` 是最大受益者（其内部 fork/进程树击杀逻辑归入 local backend）；
- `sandbox.mode=auto`：启动时与每若干分钟惰性探测 daemon 可达性（探测结果带 TTL 缓存），不可用即 transparent 回落 local 并打 warning；
- `mode=docker`：daemon 不可达时返回 503 `sandbox_unavailable`（不静默回落，保证生产承诺明确）；
- 结果契约归一：runner 把容器退出码/OOM 标志/超时翻译成现有字段（`timed_out` / `raw_output` / returncode），上层错误翻译管线不受影响。

### 5.2 调用点迁移映射

| 调用点 | 镜像 | 迁移要点 |
| --- | --- | --- |
| run_tex_compile（5 个上游） | tex | 项目目录同路径挂载；PDF/A 临时目录语义不变；产物直接落在挂载目录 |
| git_diff git 子命令 | tex（内置 git） | repo 只读挂载；worktree copytree 场景挂载副本目录 |
| latexdiff | tex | old/new.tex 所在临时目录挂载 |
| pandoc | convert | 输入临时文件/stdin，输出临时文件挂载 |
| latexindent | tex | 格式化临时文件所在目录挂载 |
| plot_gen Python | plot | 会话沙箱目录挂载；matplotlib 缓存/TMPDIR 重定向到容器内 tmpfs；审计钩子保留 |

template_gen 解析 .log 中文件路径的逻辑依赖容器内外路径一致——同绝对路径挂载保证 `_normalize_log_file` 等零改动。

### 5.3 镜像体系

```
docker/
├── backend/Dockerfile   # python:3.12-slim + requirements + unar；非 root 运行
├── tex/Dockerfile       # install-tl scheme-medium + ARG 化宏包清单（ctex/biblatex/pgf 等）
│                        #   + latexdiff + latexindent + texfot + git；版本锁定
├── convert/Dockerfile   # 锁定版本 pandoc 官方发布物
├── plot/Dockerfile      # python:3.12-slim + 科学计算栈（与后端 requirements 对齐子集）
│                        #   + fonts-noto-cjk 等字体
├── build.sh             # 统一构建与 tag（如 latexcellence-tex:YYYY.MM）
└── README.md            # 自定义宏包清单、离线导入导出、升级流程
```

- 镜像名/tag 由 `sandbox.image_*` 配置引用；部署文档提供"预构建镜像离线加载"路径（`docker save/load`）；
- TeX 宏包清单参数化：缺宏包场景由部署者改清单重建镜像（文档给排查步骤），不在运行时装包（沙箱无网络原则）；
- 多架构：各 Dockerfile 不写死基础镜像架构，支持在 amd64/arm64 上原生构建（可选 buildx 产出多架构 manifest）；TeX Live（`aarch64-linux` 二进制集）与 pandoc（官方 arm64 发布物）均可直接用于 arm64 构建，验收在两种架构上各执行编译/绘图冒烟。

### 5.4 资源限制与并发池

- 容器参数：`--cpus`、`--memory`、`--pids-limit`、`--network none`、`--read-only` + tmpfs（`/tmp` 等，容量 `tmpfs_mb`）、`--security-opt no-new-privileges`、drop capabilities、non-root（与宿主后端 uid:gid 一致，避免产物属主错乱）；
- 并发租约（`sandbox_slots` 表）：事务内校验"全局活跃数 < max_concurrent_global 且该用户活跃数 < max_concurrent_per_user"，成功插入带 TTL 的槽租约，执行结束删除；崩溃残留租约由 TTL 清理兜底；
- 获取不到租约返回 429 `sandbox_busy`（前端提示稍后再试）；
- 超时沿用各功能现有配置（`latexmk_timeout`、`tikz_compile_timeout`、`shadow_compile_timeout`、`agent_compile_timeout`、pandoc/latexdiff/latexindent 现值），runner 强制执行并在超时时 kill 容器。

### 5.5 退出码与异常翻译

| 情形 | 翻译 |
| --- | --- |
| exit 0 | 成功 |
| runner 超时 | `timed_out`（沿用现有语义与文案） |
| OOMKilled（137 + inspect 标志） | `sandbox_killed_oom` → 友好提示"内存超出沙箱限额"（新增错误码 + i18n） |
| 容器创建/启动失败 | `sandbox_unavailable`（503） |
| 其他非零退出 | 保持现有失败路径（错误日志交 `translate_compile_errors` 等既有管线） |

### 5.6 测试

- pytest marker `docker`：检测不到 daemon 自动 skip；有 daemon 时跑真容器编译冒烟（极小 LaTeX 样例）；
- SandboxRunner 契约测试：fake 后端验证 ExecSpec→ExecResult 翻译、超时、OOM 映射、租约获取/释放；
- local 回落回归：现有全部编译/绘图/转换测试必须原样通过（证明回落路径零行为变化）；
- 安全冒烟：`\write18` 尝试、网络访问、越界路径写入均失败且不留宿主痕迹。

## 6. P1b — LLM 每用户治理

- 计数：`rate_limit_events(kind='llm', identity=user_id, ts)` 滑动窗口；每次 LLM 调用前 INSERT + COUNT（60s 窗口 ≥ `llm_rate_limit_per_minute` 则 429，附 Retry-After）；
- 并发：`llm_concurrency_leases` 租约（task_id=本次请求 uuid），≥ `llm_max_concurrency_per_user` 直接 429 不排队；finally 释放，TTL 清理崩溃残留；
- 注入点：`llm_client` 外层封装处读取 userspace ContextVar 当前用户；仅多用户模式启用，单用户跳过；
- 旧内存登录限流（`auth.py` 的进程内字典）迁至同表（kind='login'），修复多实例下阈值放大问题（P7 完成跨副本生效，P1b 先落地表结构与单进程路径）；
- 前端：429 文案三语言（复用"请求过于频繁"语式 + Retry-After 展示）。

## 7. P2 — rar 压缩包支持

- 检测：`shutil.which("unar")`；缺失时报明确错误（`invalid_archive` 细分文案："服务器未安装 unar，无法解压 rar"）；
- 调用：`unar -f -q -o <extract_dir> -- <archive>`，stdin 接 `/dev/null` 防止加密包交互挂起；限时（复用解压 deadline）+ 进程树击杀；
- 加密/分卷：unar 失败输出匹配（password/volume 特征）映射为 v1 口径的 `archive_unsafe`/明确错误码；不解密任何受保护包；
- 解压完成后复用现有链路：`_validate_extracted` → `normalize_project_root` → 配额复核 → `move_into_user_space` → `create_project_row`；
- 大小/文件数预检：rar 无进程内目录清单，采用"先限量解压到暂存区 + 监视线程累计"（与 7z 监视同型），超限即杀进程清暂存；
- 测试 fixture：入库一个手工构造的小型 rar 样例（含正常布局/顶层目录布局两种）+ 加密样例（仅验证拒绝）。

## 8. P3 — 共享项目撤销恢复（反向补丁）

### 8.1 数据模型与磁盘布局

- 索引：`workspace_edit_ops`（见 §4.3）；一次 `apply_whole_files` 事务内的多文件写共享同一 `op_group_id`（一次撤销回滚整组）；
- 快照：`<users_root>/<owner_id>/.edit_logs/<project_id>/<op_id>/`，内含 `manifest.json`（files: [{path, before_md5, after_md5}]）与每个文件的 `<n>.before` / `<n>.after` 全文；owner 维度放置保证不进入项目目录与下载 zip；
- 单 op 快照总量 > `edit_log_max_op_mb` 时记 md5 但标记 `non_undoable`（撤销列表中展示"过大不可撤"）。

### 8.2 写入拦截与 actor 归属

- `apply_change` / `apply_whole_files` 通过 ContextVar 取当前用户（现有 `userspace.current_user_id`），多用户模式下每次成功写后落一条 op（before 内容取自现有写前快照点）；
- 单用户模式完全不落盘，`BackupManager` 内存栈行为保持现状（私有快速路径不复用，两套语义互不干扰）；
- 覆盖面核验：逐段检查/写作助手工具/apply-edit/code_review/bib_manager/style_modify 全部经由上述两个写通道，自动获得记账，无需逐模块改造（探查已核对全量写路径）。

### 8.3 撤销/重做流程

- undo：取当前 (project, actor) 的最新 `active` op → 前置校验组：
  1. 写权限（viewer 403）；
  2. op 组内每文件存在且当前 MD5 == after_md5，否则 409 `file_conflict`（文案引导刷新重试；绝不静默覆盖他人修改）；
  3. 恢复后不超项目配额，否则 413 `quota_exceeded_recover`（op 保持 active 可重试）；
  → 以 before 内容经 `apply_whole_files(expected_md5s=after_md5)` 回滚 → 标记 `undone`；
- redo：对称（最新 `undone` op，校验当前 MD5 == before_md5，重放 after）；
- LIFO：只允许按栈序撤/重做最新一条自己的 op；他人 op 不出现在自己的栈中；
- `can-undo/can-redo/undo-status` 改为实时按上式计算（MD5 校验失败视为不可撤销但保留 op，待文件回到对应状态后可再撤）。

### 8.4 生命周期与耦合点改造

- 条数上限：每 (project, actor) 保留最近 `edit_log_max_ops` 条，溢出淘汰最旧（连带删快照文件）；
- TTL：跟随工作区绑定 `last_active_at`，超过 `edit_log_ttl_hours` 无活动的绑定连同其 ops 清理（维护循环执行）；
- 配额回滚改造：`projects/service.py` 的超限回滚改为直接用 before 内容恢复（不经 undo 通道、不产生 op），避免误回退他人修改；
- 禁令解除：移除 `reject_undo_redo_if_shared`，AI 旁路（写作助手工具/apply-edit、style_modify apply/undo）因统一记账而自然合规；style_modify 自身的 operation 撤销保留（其四道防线与本机制语义一致）；
- 项目转交：ops 按 actor_user_id 归属不变；转交后的 owner 只能撤销自己的 ops；
- 文件缺失（理论上无删除入口，防御性处理）：该 op 标记 `failed` 并在状态接口提示。

### 8.5 前端与 i18n

- `UndoRedoButtons` 交互不变（轮询 undo-status + 快捷键）；多用户共享项目不再隐藏按钮；
- 冲突提示、不可撤提示、"仅能撤销自己的修改"说明文案进三语言。

### 8.6 测试

- 两用户协作场景：A 改 → B 改 → A undo 被 409 拒绝 → B 未受影响；A 连续多次编辑逆序回退；redo 对称性；
- op_group 事务整体回滚；配额边界（恢复超限 413）；TTL/条数淘汰；转交后归属；
- v1 禁令测试反转为解禁断言；旁路路径记账覆盖断言。

## 9. P4 — 衍生数据记账优化与细粒度清理

### 9.1 记账模型

- `user_derived_usage` 缓存各分类大小；`enforce_derived_quota` 与 `GET /api/auth/me` 一律读缓存（O(1)）；
- 检查点增量：会话/模板/绘图创建、会话删除、自助清理等触达某分类时，仅扫描该用户该分类子目录刷新缓存（范围从"全部分类"缩到"被触达的单个目录"）；
- 每小时对账任务全量重算校正漂移（复用现有 maintenance 循环，套维护租约防止多副本重复扫描）；
- 顺带优化：7z 解压监视线程的周期 `dir_size` 全遍历改为采样计数器（解压侧已知写入量）。

### 9.2 清理策略

- 各模块现有 TTL 清理保持；新增 `writing_chat_retention_days`（默认 0 = 不自动删，仅统计）接入清理循环；
- 用户自助清理：工作台账号区展示分类用量条（写作会话/样式会话/模板/绘图）+「清理」按钮 → `POST /api/auth/cleanup-derived-data {category}`（二次确认对话框；删除该用户该分类下全部内容并刷新缓存）；
- 不做管理员强制清理（列入非目标）；
- `sessions` 表过期会话删除并入维护循环（修复无限增长）。

### 9.3 测试

- 缓存一致性（写/删/清后即时正确）、对账纠偏、并发触达竞态、自助清理权限（只能清自己）、配额边界行为不变。

## 10. P5 — 大文件分片上传与断点续传

### 10.1 协议设计

- init：携带 `name`、`kind`（zip/7z/directory）、`total_bytes`、`files[]`（相对路径 + size + md5 + 分块数）；服务端校验扩展名白名单、manifest 结构（path_guard 规则复用）、剩余配额 ≥ total_bytes，建 `upload_sessions` 行 + staging 目录；
- part：二进制体 ≤ `chunk_part_max_mb`，头 `X-Part-MD5` 校验；落 `<staging>/<sid>/parts/<file_index>-<chunk_index>.part`，幂等覆盖；成功即 touch 会话；
- status：返回每文件已收分块位图（前端据此续传）；GET 同时续期；
- complete：校验分块齐全（数量与 md5 抽全量复核）→ 拼装：zip/7z 拼出压缩包走现有 `extract_archive` 全链路；directory 按 manifest 拼出 extracted 布局后走 `normalize_project_root` + `_validate_extracted` → 配额二次复核 → `move_into_user_space` → `create_project_row`；
- abort：DELETE 删行 + 清 staging。

### 10.2 生命周期与安全

- 会话 TTL `upload_session_ttl_hours`（从最后活动算）；维护循环清理过期会话与孤儿 staging（现有 mtime 兜底保留）；
- 分片阶段不占配额（init 预检 + complete 二次复核之间的竞争由复核兜底，失败即整包拒绝）；
- 权限：会话归属校验（只能操作自己的 sid）。

### 10.3 前端改造

- `UploadProjectDialog`：webkitdirectory 收集相对路径 → 逐文件切片（8MiB）→ 并发 3 的分片队列 → 进度条按字节聚合；localStorage 持久 upload_id + manifest，页面刷新/断网后「继续上传」；
- zip/7z 文件直传同样走分片（不再一次性 multipart）；JSZip 客户端打包路径移除；
- 失败重试（指数退避）、暂停按钮、过期会话（410）引导重新选择。

## 11. P6 — SMTP 邮件通知与自助找回

### 11.1 邮件服务模块

- 新增 `backend/core/multiuser/mail.py`（或 `backend/modules/auth/mail.py`）：aiosmtplib 异步发送；
- 发送走 `email_outbox` 队列：业务方投递即返回，lifespan 启动的发送协程消费，指数退避重试 `retry_attempts` 次，失败留档 `failed` 供运维排查；全局限速 `max_send_per_minute`；
- `smtp.enabled=false`（或缺 host）：所有通知点静默跳过（debug 日志），自助找回入口在前端隐藏、后端返回 `email_not_available` 引导联系管理员——优雅降级不阻塞主流程。

### 11.2 事件矩阵

| 事件 | 收件人 | 触发点 | 去抖 |
| --- | --- | --- | --- |
| 新用户待审批 | 全部 admin | 注册成功 | 每 5 分钟聚合（期间多个注册合并为一封） |
| 审批通过/拒绝/禁用 | 该用户 | 管理面板/CLI 操作 | 即时 |
| 管理员重置密码 | 该用户 | reset confirm | 即时，正文含临时密码与改密提醒 |
| 自助找回令牌 | 该用户邮箱 | forgot-password | 每邮箱 5 分钟 1 次 |
| 配额预警 80% | 该用户 | 对账任务检查 | 迟滞：低于 75% 重置标记后可再次发送 |
| 配额预警 95% | 该用户 + admin | 同上 | 同上 |

模板：中/英两套（跟随站点语言配置），存于 mail 模块内集中管理；链接前缀取 `site_base_url`（未配置则退化为相对路径提示文案）。

### 11.3 自助找回流程

1. 登录页「忘记密码」→ 输入用户名或邮箱 → `POST /api/auth/forgot-password`；
2. 服务端定位唯一用户（按用户名查邮箱，或按邮箱查用户）：无邮箱/多用户同邮箱 → 通用响应 + `email_not_available` 提示联系管理员（不泄露存在性）；命中 → 生成随机令牌，**只存 SHA-256**，TTL 1 小时、单次有效，投递邮件；
3. 用户经链接提交新密码 → `POST /api/auth/reset-password` 校验令牌 → argon2id 写入 + 吊销全部会话 + 令牌标记 used；
4. forgot/reset 均做 IP + 目标维度限流；响应恒定时长防枚举。

### 11.4 邮箱唯一性与设置

- 建 `UNIQUE(email COLLATE NOCASE) WHERE email != ''` 部分唯一索引（迁移步骤内创建，存量重复则迁移报警并列出冲突行，由管理员人工处理，不自动改数据）；
- 注册表单邮箱重复即时校验；`PUT /api/auth/profile` 修改邮箱需当前密码确认，同样过唯一校验。

### 11.5 测试

- mock SMTP server（本地 aiosmtpd fixture）验证投递/重试/限速/降级；
- 找回全流程（含令牌过期/重放/枚举防护/重复邮箱降级）；
- 迁移步骤在有/无重复邮箱两种存量下的行为。

## 12. P7 — 多进程/多实例部署

### 12.1 状态外置总表

| 状态 | 现状位置 | 方案 |
| --- | --- | --- |
| workspace 绑定 | `workspace_manager._bindings`（进程内） | `workspace_bindings` 表；进程内保留 LatexProject 可重建缓存，miss 时按 root_path 重新 open |
| CheckOperationContext | `base_paragraph_check` 进程 dict（TTL 1h） | `operation_contexts` 表，apply 跨副本可取；过期清理入维护循环 |
| writing_chat pending 确认/澄清 | 进程 dict + asyncio.Event（120s） | `pending_interactions` 表 + 短间隔轮询替代 Event |
| DDG challenge | 进程 dict（TTL 300s） | `pending_interactions(kind='ddg_challenge')` |
| 登录限流计数 | 进程 dict | `rate_limit_events(kind='login')` |
| LLM 限流/并发 | 未实现 | §6 表结构，本阶段起真正跨副本生效 |
| style_modify/template/plot 会话互斥 | 进程 asyncio.Lock | 会话目录旁 flock 锁文件（同机共享卷有效；进程内锁保留叠加） |
| 配置热重载 | 进程单例，改配置其他副本漂移 | `get_config()` 增加 config.json mtime+size 检查，变化即重载；`multi_user.enabled` 仍需重启（文档维持） |
| 后台任务重复执行 | 各副本各自跑（幂等） | `maintenance_lease` 选主：持租约者执行维护循环，减少重复扫描；模块 cleanup_loop 保持各自运行（幂等无害） |
| 日志 | TimedRotatingFileHandler（多进程不安全） | 容器模式 env 开关切纯 stdout；裸机维持单进程前提 |
| LLM client / httpx / Playwright / ToolRegistry / web_cache / 元数据缓存 | 进程内 | 保持进程内（无用户态或纯缓存，多副本安全） |

### 12.2 编排与验收

- 仓库根提供 `docker-compose.yml`（app 服务 + 可选 Caddy 反代；`./data` bind 卷；docker.sock 只读挂载；`LATEXCELLENCE_LOG_STDOUT=1`；healthcheck 打 `/api/health`）与 `docker-compose.override.example.yml`；
- 副本扩展：`docker compose up --scale app=N`；反代样例给出 Caddy（自动重解析服务名）与 nginx（upstream + 合理 resolver）两种；
- 验收标准：replicas=2 环境跑通全回归——登录/审批、打开项目、逐段检查 preview→apply（跨副本）、写作助手工具确认（跨副本）、上传（分片中途换副本）、undo、配额、管理面板；
- deployment.md 重写运行形态章节：单进程约束撤销，新增 compose 步骤、备份注意（多副本停机窗口）、监控建议（busy_timeout 冲突日志、outbox 失败数、沙箱池水位）。

### 12.3 schema 版本迁移机制

- `db.init_db()` 读 `PRAGMA user_version`：全新库 → 执行最新 SCHEMA 后置版本号；老库 → 按序执行迁移函数列表（每步幂等、可重入），逐级推进版本号；
- 所有 v2 新表/索引/列变更以迁移步骤形式登记，禁止散落 ALTER；CLI 提供 `python -m backend.manage db-status`（查看当前版本与待应用步骤）。

## 13. 测试总体策略

- 分层沿袭 v1：单元（Service Mock LLM）/ API 集成 / 多用户场景 / 新增 compose 冒烟（`tools/compose-smoke.sh`：起 replicas=2 → health → 注册审批 → 分片上传 → 编译 → 撤销 → 下载）；
- 沙箱测试双轨：无 Docker 环境 skip（marker `docker`），CI 与有 Docker 的开发机跑真容器最小用例；
- 架构冒烟：amd64 之外在 arm64 环境（或 CI runner）执行启动 → 登录 → 上传 → 编译的最小链路验证；Playwright WebKit 在 arm64 的可用性随版本变动，联网搜索冒烟时单独确认（该能力失败自动降级，不阻塞）；
- LaTeX 测试数据一律原始字符串；错误码新增同步三语言 `errors` 命名空间；
- 涉及外部二进制（unar）的 fixture 入库小型样本，不依赖构建环境装包。

## 14. 实施阶段

| 阶段 | 内容 | 完成标准 |
| --- | --- | --- |
| P0 配置与迁移骨架（✅ 前置） | `sandbox`/`smtp` 配置节与 multi_user 新字段、config.example、设置分区 UI、schema 版本迁移框架 + `db-status` CLI | 配置校验与迁移框架测试绿，文档评审通过（P1a 实施时已落地 sandbox 节 + 迁移框架 + db-status；smtp 节随 P6） |
| P1a Docker 沙箱（✅ 已完成） | 四镜像 + build.sh、SandboxRunner 双后端、六类调用点接入、资源租约池、退出码翻译、降级探测 | 有 Docker：编译/绘图/转换/git diff 全链路容器内执行 + 安全冒烟通过；无 Docker：回落回归全绿。评审修复记录：移除 stdin 协议（pandoc 文本转换改临时文件输入）、convert 镜像不设 ENTRYPOINT（补容器冒烟哨兵）、启动预热填充后端解析缓存 |
| P1b LLM 治理（✅ 已完成） | `rate_limit_events(kind='llm')` 滑动窗口 + `llm_concurrency_leases` 租约（迁移 v3）、`llm_client` 全入口 `admission_gate()` 注入、登录限流迁同表（kind='login'）、维护循环清理、429 + Retry-After（main.py 全局异常处理器）、前端三语言文案 | 限流/并发/TTL 清理用例绿；被拒调用不落行不耗配额；单事务 BEGIN IMMEDIATE 准入，P7 多实例同卷天然跨副本生效 |
| P2 rar 支持（✅ 已完成） | unar 管线 + 错误映射 + fixtures | rar 正常/加密/分卷用例绿；缺二进制报错明确。实施记录：调用形如 `unar -f -q -D -o <dir> -- <file>`（`-D` 阻止 unar 在解压根外再包一层归档名目录，保持与 zip 管线一致的根归一化语义）；stdin 接 `/dev/null` + `start_new_session` 进程组限时/超配额 SIGKILL；加密/分卷在解压前经 RAR4 头链最小预扫确定性拒绝（不依赖失败输出措辞，头链遍历受 `max_files` 派生的块数上限约束防止超小块垃圾包 CPU 空转），RAR5 由输出关键词（password 等）与“成功但零产物”兜底；缺二进制返回独立错误码 `unar_missing`（400，三语言 i18n）而非复用 `invalid_archive`——前端结构化错误优先按码翻译，独立码才能让细分文案到达用户；fixtures 为纯字节手工构造的 RAR4 store 样例（`tools/rar_fixtures/make_fixtures.py` 生成入库），真实 unar 用例无二进制自动 skip，预扫/接线/监视线程用例任何环境都跑 |
| P3 反向补丁撤销（✅ 已完成） | 补丁日志、undo/redo 重写、解禁与旁路覆盖、配额回滚改造、前端/i18n | §8.6 场景测试全绿；v1 禁令测试反转。实施记录：迁移 v4 建 `workspace_edit_ops`（`seq` AUTOINCREMENT 严格定序 + `op_group_id` 整组回滚 + `non_undoable` 过大标记）；快照落 `<users_root>/<owner_id>/.edit_logs/<project_id>/<op_id>/`（manifest + `<n>.before`/`<n>.after` 全文）；记账统一挂接 `apply_change`/`apply_whole_files`（ContextVar 取当前用户，单用户/无用户上下文零落盘，before==after 跳过），AI 旁路自动覆盖无需逐模块改造；undo 前置校验"每文件当前 MD5 == after_md5"冲突即拒绝（409 `file_conflict`）、恢复超配额 413 `quota_exceeded_recover`（op 保持 active 可重试）、文件缺失防御性标记 `failed` 并在 undo-status 提示；redo 取"最近一次被撤销"组（`undone_at DESC, seq ASC`，同秒多次撤销按 seq 升序破平）对称重放、有更新 active op 视为已被取代；恢复写回复用 `apply_whole_files(expected_md5s=...)` 事务并抑制再记账；条数上限按组整体淘汰连带删快照、TTL 清理入维护循环（实施口径微调：以 op 栈自身最近活动 `MAX(created_at, undone_at)` 为过期基准——工作区绑定当前仍为进程内状态，P7 持久化绑定后可再切换到绑定 last_active 口径；效果一致：持续编辑刷新保留、闲置超期整栈清理）、项目永久删除/回收站清空级联清理；`apply-change` 配额回滚改为写前捕获全文 + 以本次 op 的 after_md5 作版本守卫的抑制记账 `apply_whole_files` 恢复（他人并发写入时放弃覆盖、只撤销本次记账并如实报错；无清单可退化 `restore_project_file()` 但告警）+ `discard_op()` 删除本次 op（失败写入不在栈留痕）；`reject_undo_redo_if_shared` 移除、v1 禁令测试反转为解禁断言；前端按钮交互不变（服务端实时状态自然解禁共享项目），新增错误码 `quota_exceeded_recover` / `op_not_undoable` 与确认弹窗文案三语言同步。审查修复记录：`purge_stack` 不再整目录 rmtree（共享项目其他 actor 快照同住 `<owner>/.edit_logs/<project_id>/`，只逐 op 删除 + 空目录收敛；项目级级联清理在全部 actor 栈清完后进行）；undo/redo/compute_status 组内同文件多条 op 聚合（取最小 seq before / 最大 seq after，消除整组永久不可撤）；事务内配额预检改纯读（消除 db_execute 隐式 COMMIT 破坏外层事务原子性 + 冗余全树扫描）；配额回滚加版本守卫（检查窗口内他人落盘则放弃覆盖）；多用户 undo/redo 分支补 FileConflictError → 409 映射；compute_status 快照改流式哈希 |
| P4 记账与清理（✅ 已完成） | 用量缓存 + 检查点 + 对账、自助清理 API/UI、sessions 清理 | 消除每请求全盘扫描；缓存一致性与清理用例绿。实施记录：迁移 v5 建 `user_derived_usage`（(user_id, subdir) 复合主键，FK 随用户级联删除）；配额校验与 `/api/auth/me` 一律读缓存 O(1)，缓存表全空时首读自动做一次全量引导（冷启动读数即准）；检查点增量刷新挂在全部触达点——写作会话创建（send 首次落盘后）/删除、style_modify 会话创建/删除、templates/plots 会话创建、三个模块 TTL 清理批量删除后按分类重扫受影响用户、自助清理——范围从"全部分类"缩到"被触达的单个目录"；配额前置校验携带 subdir 时先刷新该分类缓存行再比较（创建时边界严格性不放松，纯缓存读容忍跨分类漂移由对账兜底）；维护循环并入 `reconcile_derived_usage()` 全量对账纠偏、`purge_expired_sessions()` 过期会话删除（修复无限增长）、`writing_chat_retention_days`（默认 0=不自动删仅统计）闲置写作会话清理（mtime 口径，与既有 TTL 清理一致，新模块 `modules/writing_chat/cleanup.py`）；自助清理 `POST /api/auth/cleanup-derived-data {category}`（user 取自会话故无跨用户参数面，未知分类 400 `invalid_category` 三语言 i18n；低频动作返回前重扫本人全部分类保证明细整体准确）；`/api/auth/me` 的 usage 新增 `derived_usage` 分类明细；前端工作台账号区四分类用量条 + 清理按钮（ConfirmDialog 二次确认，成功后拉取 /me 同步）；设置分区新增保留期数值项（NumberField 支持 min=0，0=仅统计）；7z 监视线程周期 `dir_size` 全遍历改为 `os.scandir` 单趟采样计数器 + 超限短路（rar 监视线程不动）——计划中"解压侧已知写入量"的前提对 py7zr 整包写出路径不成立（无逐写入回调可挂，rar 侧为外部进程同理），按不引入 OS 钩子前提下的最小观测开销落地：单趟 lstat 替代 rglob 多次 stat、超限快速路径提前终止，最坏复杂度仍与产物规模线性，已在实施记录如实标注。审查修复记录：`migrate-legacy` 批量复制衍生数据到管理员空间后即时重扫其用量缓存（消除缓存表非空时迁移量滞留到下一轮对账、配额校验读偏小旧值的窗口）；写作会话保留期清理删除后即时重扫受影响用户缓存行（与三模块 TTL 清理口径一致，不再等下一轮对账）；补 §9.3 并发触达竞态（检查点×对账交错无异常、静止后收敛）与迁移触达用例 |
| P5 分片上传（✅ 已完成） | 五端点 + 会话模型 + 前端分片队列 + TTL 清理 | 断点续传/大文件/目录直传/中止用例绿；并发分片安全。实施记录：迁移 v6 建 `upload_sessions`（manifest JSON 列存 chunk_size 与逐文件 path/size/md5/chunks，`last_activity_at` 为 TTL 基准，part/status 成功即续期）；新模块 `core/multiuser/upload_sessions.py` 承载会话核心（init 结构与配额预检 / part 幂等落盘 + `X-Part-MD5` 校验 + 体积上限 / status 位图对位 / complete 拼装落位 / abort 清理），staging 布局 `<staging>/<sid>/{.upload_session 标记, parts/<file>-<chunk>.part, extracted/}`——带标记目录被通用 `clean_staging` mtime 兜底豁免（TTL 可长于暂存保留期，防误删活跃会话），生命周期由并入维护循环的 `purge_expired_upload_sessions()` 接管（过期会话行 + 目录 + "有标记无数据行"孤儿清扫）；complete 以条件 UPDATE（uploading → assembling）拦截并发重复提交：分块缺失 / 全量 MD5 复核不符恢复 uploading 保留现场可续传，进入拼装后任何失败（解压拒绝、配额二次复核超限、落位异常）整包拒绝并清场（用户空间残留按确定性路径删除 + staging + 行）；zip/7z 拼包走既有 `extract_archive` 全链路、directory 按 manifest 拼出 extracted 布局走 `normalize_project_root` + `_validate_extracted` 同一校验链；前端移除 JSZip 打包路径（依赖一并卸载），`UploadProjectDialog` 重写为分片队列：webkitdirectory 相对路径逐文件切片（大小对齐 `chunk_part_max_mb`）、自研增量 MD5 工具（WebCrypto 不提供 MD5，纯 JS RFC 1321 实现避免新增依赖，向量经 hashlib 对照验证）产出 manifest 全量 md5 与逐片 `X-Part-MD5`、并发 3 队列按字节聚合进度、指数退避重试、暂停/继续（AbortController 中断在途分片）、localStorage 持久 upload_id + 轻量清单，刷新后重新选择相同内容先 GET status 对位图再补缺（服务端清单为权威按相对路径对位，不依赖选择顺序），410 过期引导放弃会话重新选择；设置分区新增 `upload_session_ttl_hours` / `chunk_part_max_mb` 数值项（纯数值项参照 edit_log_* 先例无需动 setEnabledFeatures/featureGroups），config/status 透出 `chunk_part_max_mb` 供切片对齐；错误码 `upload_session_not_found`(404) / `upload_session_expired`(410) / `part_md5_mismatch` / `part_missing` 三语言 i18n + errorMessages.js 映射。偏离声明：kind 白名单在方案 §10.1 的 zip/7z/directory 之外纳入 rar——P2 已交付 rar 解压管线且压缩包直传统一走分片后若剔除 rar 将造成功能回退，拼装后复用同一条 extract_archive 链路，安全语义不变 |
| P6 SMTP 与找回 | 邮件模块 + outbox + 事件矩阵 + 找回流程 + 邮箱唯一迁移 + 模板 | mock SMTP 全流程绿；未配置降级回归绿；THIRD_PARTY_NOTICES 登记 |
| P7 多实例收口 | §12.1 外置清单、flock、配置漂移治理、compose、stdout 日志、deployment.md 重写 | replicas=2 全回归通过；compose 冒烟脚本绿 |

依赖关系：P1b/P3/P5 建表依赖 P0 迁移框架；P7 依赖前面各阶段已把数据落 SQLite；其余相互独立可并行。

## 15. 文档同步要求

实施过程中按 AGENTS.md 与 maintain-docs 规范同步：

- 本方案落地后更新 `DEVELOPMENT.md` 索引、`PROJECT_SUMMARY.md`（目录结构新增 `docker/` 与 sandbox/mail 等模块）、`DESIGN.md`（沙箱执行模型、反向补丁状态机、多实例状态外置图）；
- 模块文档：`docs/backend-core.md`（sandbox 包、multiuser 新表）、`docs/modules/`（projects 上传分节、auth 找回流程）、`docs/deployment.md`（compose 形态、镜像构建、风险声明更新：沙箱落地后适用范围扩大，但 docker.sock 挂载风险必须显式声明）；
- 配置变更同步 `config.example.json` 与设置分区组件、`SettingsDialog.jsx`、`WelcomePage.jsx`；
- README.md 在 P1（沙箱）与 P7（多实例）两个里程碑做重大功能更新；
- 新依赖（docker SDK、aiosmtplib；系统包 unar 经镜像引入）登记 `THIRD_PARTY_NOTICES.md`。

## 16. 风险与开放问题

| 风险 | 应对 |
| --- | --- |
| `/var/run/docker.sock` 挂载等于授予后端 root 等价能力 | 镜像最小化 + 只读挂载 socket；文档显著声明；rootless Docker/Podman 作为后续演进记录（v2 不承诺） |
| TeX Live medium 缺宏包导致用户项目编译失败率上升 | 构建清单参数化 + 部署文档排查指南（从日志提取缺失宏包 → 加清单 → 重建）；统计编译失败原因分布作为运营指标 |
| Docker Desktop（Win/macOS）路径翻译破坏同路径挂载 | 官方支持矩阵注明 Linux 原生/docker；Windows 建议 WSL2 内部署；开发环境回落 local 模式不受影响 |
| SQLite 写吞吐上限（限流事件高频写） | 单机两副本规模下余量充足；监控 busy_timeout 冲突日志频率，出现劣化信号时再评估 Postgres/Redis（v3 议题） |
| unar 对非 UTF-8 文件名的解码行为与 zip 管线不一致 | P2 测试覆盖 GBK 样例；不一致时在文档标注已知差异 |
| 容器冷启开销（每编译约数百毫秒~秒级） | 相对 120s 编译预算可接受；不做 warm pool，若实测劣化明显再列为优化项 |
| 补丁快照磁盘占用 | 双上限（条数 + 单 op 字节）+ TTL + 维护循环清理；`.edit_logs` 计入用户空间但不占配额（运维文档说明备份口径） |
