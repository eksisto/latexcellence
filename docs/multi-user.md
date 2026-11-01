# 多用户模式设计（multi-user）

> **状态：v1 与 v2 均已实施。** 本文是多用户模式的权威设计参考，记录模式边界、核心机制
> 与关键决策，供维护与查询。实现细节见 [backend-core.md](backend-core.md)「多用户模式
> 基础设施」；沙箱 / LLM 治理 / 反向补丁撤销 / 多实例的执行模型深读见
> [DESIGN.md](../DESIGN.md) §12–§15；部署与运维见 [deployment.md](deployment.md)。
> 历史方案文档 multi-user-plan.md（v1）与 multi-user-v2-plan.md（v2）已合并改写为本文，
> 阶段划分、实施记录等过程性内容已移除。如行为与本文冲突，先修订本文再动代码。

## 1. 模式边界与运行形态

### 1.1 双模式开关

`multi_user.enabled` 一键切换，单用户模式行为完全保持现状（本地任意目录打开 + 共享访问令牌）；多用户模式：

- 启动时初始化 SQLite 与用户目录，`/api/*` 强制会话认证（公开端点除外）；
- 禁用任意路径打开（`/api/project/open` 仅接受 `project_id`）与磁盘浏览（`/api/project/browse` 403）；
- 配置接口仅管理员可访问；共享令牌逻辑跳过，改走会话 Cookie + CSRF。

### 1.2 运行形态

- v1 单进程（uvicorn `workers=1`）；v2 支持多实例 Compose 副本扩展（§10）——跨请求状态全部外置共享 SQLite，无需粘性会话；
- 多用户模式下工作区绑定为 `workspace_id → (user_id, project_id, owner_id, root_path)`，每次请求**实时查库**校验权限（不缓存，权限变更即时生效）。

### 1.3 服务器路径脱敏

多用户模式不向用户暴露服务器路径：`root_path` / `git_root` / `sandbox` 等响应字段置 `null`；面向用户的错误消息只用相对路径；编译日志、traceback 展示前经前缀脱敏（`tex_compile.sanitize_output_paths()`）。完整路径只进服务端日志。

## 2. 认证与账号

- **注册与审批**：用户名 + 密码（argon2id）注册为 `pending`，管理员批准后 `active` 方可登录；`registration_enabled=false` 时只能由管理员创建。注册自动审批（IP 策略：同 IP 账号上限 + CIDR 白名单 + 离线地理白名单叠加判定「无恶意」即自动通过，只降级为人工、绝不直接拒绝）见 [backend-core.md](backend-core.md)「注册自动审批要点」与 [deployment.md](deployment.md)「注册自动审批」。
- **会话**：随机令牌只存 SHA-256；Cookie `HttpOnly` + `SameSite=Lax`（HTTPS 下 `Secure`），默认 7 天、记住我 30 天；CSRF 双提交（自定义请求头 + 可读 Cookie，常量时间比较）；禁用 / 重置密码即时吊销全部会话。
- **登录限流**：每用户名 + 每 IP 分钟级滑动窗口（`rate_limit_events` 表，跨副本生效）；恒定时长校验（用户名不存在也执行占位 argon2 验证）防时序枚举。
- **管理员**：首次启动且用户表为空时从 `LATEXCELLENCE_ADMIN_USERNAME/PASSWORD` 环境变量引导；CLI `python -m backend.manage`（create-admin / reset-password / list-users / set-status / migrate-legacy / db-status）；管理面板提供审批、禁用、两步式重置密码、配额调整、删除用户（项目转管理员、衍生数据归档后清理）。

## 3. 用户空间、配额与清理

### 3.1 目录布局

```
data/
├── latexcellence.db
├── staging/                          # 上传解压暂存区
└── users/<user_id>/
    ├── projects/<project_id>/        # 项目根（project_id 派生路径，禁止用户提交服务器路径）
    ├── writing_chat_sessions/  ├── style_modify_sessions/
    ├── templates/  └── plots/        # 四类衍生数据，按用户隔离
```

`<owner_id>/.edit_logs/` 存反向补丁快照（§8），不进项目目录与下载包。所有按用户/项目拼接路径经统一解析入口（`path_guard` 封装），默认拒绝符号链接。单用户存量全局路径保留原样供单用户模式使用（`userspace.user_resource_root()` 双模式解析）。

### 3.2 项目空间配额

- 只统计活动项目解压后数据量（缓存 `projects.size_bytes`，后台对账纠偏）；回收站不计配额，删除即腾出额度，恢复超限禁止；
- 写回 / 撤销恢复 / 转交均做配额校验，超限硬失败（413 `quota_exceeded` / `quota_exceeded_recover`），写回超限回滚本次修改（不允许部分写入）。

### 3.3 衍生数据记账与自助清理

四类衍生目录用量入 `user_derived_usage` 缓存表：配额校验与 `/api/auth/me` 用量 O(1) 读缓存；触达某分类时检查点增量刷新（只重扫被触达目录）；维护循环每小时全量对账纠偏。工作台展示分类用量条，用户可自助清理本人指定分类（`POST /api/auth/cleanup-derived-data`）；过期会话删除与写作会话保留期清理（`writing_chat_retention_days`，默认 0 = 仅统计）并入维护循环。

## 4. 权限与协作

权限只作用于用户数据文件读写，不限制功能模块使用：

| 动作 | owner | editor | viewer |
| --- | --- | --- | --- |
| 查看 / 编译 / 检查 / 下载等全部只读与功能入口 | ✓ | ✓ | ✓ |
| 应用更改、Agent 编辑、撤销/重做、上传覆盖 | ✓ | ✓ | ✗（403 `no_permission`） |
| 管理协作者、改名、删除/恢复、转交 | ✓ | ✗ | ✗ |

- 协作者按用户名添加（editor / viewer），权限**实时查库**，变更下一次请求立即生效；
- 项目转交：校验接收方配额（超限 413），项目目录迁移到新 owner 空间，原 owner 转 viewer 或退出；已打开的 workspace 绑定同步更新；
- viewer 前端禁用写操作按钮，后端 403 兜底；写作助手 / Agent 工具每次写操作前实时校验写权限。

## 5. 上传管线

### 5.1 安全解压（失败即拒绝，不落用户空间）

- 入口 `POST /api/projects`（zip / 7z / rar）与浏览器目录直传；压缩包流式写入 staging，不整包载入内存；
- 校验清单：大小 / 文件数 / 单文件 / 解压总量 ≤ 剩余配额 / 压缩比（炸弹）/ 绝对路径 / `..` / 符号链接 / 设备文件 / 加密包 / 分卷包 / 大小写重名 / 解压限时；zip 文件名 UTF-8 → GBK → cp437 解码链；
- 解压后项目根归一化（单顶层目录 → 该目录为根，否则暂存区为根，两种打包习惯均兼容）；项目命名与同 owner 活动项目去重（`xx (2)`，唯一索引 + 重试并发安全）。

### 5.2 分片上传与断点续传

五端点（init / part / status / complete / abort，`/api/projects/upload-sessions`）：manifest 预检 + 配额预检建会话；逐片 `X-Part-MD5` 校验、幂等覆盖、成功续期；status 返回已收位图供续传；complete 条件态拦截并发 → 全量 MD5 复核 → 复用 §5.1 同一解压校验链落位。分片阶段不占配额（complete 二次复核兜底）；会话 TTL 由维护循环清理，通用 mtime 兜底清理跳过带标记目录。前端分片队列（并发 3、暂停/续传、localStorage 持久 upload_id）。

### 5.3 rar 解压（回退层）

解压器选择：unar 优先、缺失回退 bsdtar（皆缺报 `rar_extractor_missing`）；解压命令经 SandboxRunner 在 convert 镜像内执行（压缩包只读挂载），宿主侧监视任务超配额/超时硬中断；RAR4 加密/分卷头链预扫确定性拒绝，bsdtar 分支产物文件名归一化。设计与实测见 [rar-extractor-fallback.md](rar-extractor-fallback.md)。

## 6. Docker 沙箱执行

多用户模式下全部外部命令（latexmk/texfot、git/latexdiff、pandoc、plot Python、latexindent、rar 解压）经 `backend/utils/sandbox/` 的 SandboxRunner 执行：`mode = auto | docker | local`（auto 探测 daemon 命中即容器，否则透明回落 local）；容器无网络、只读根 fs + tmpfs、CPU/内存/pids 限额、drop capabilities、宿主同 uid 运行；并发经全局信号量 + 每用户租约（`sandbox_slots` 表）双重限制。按用途拆分四镜像（backend/tex/convert/plot，仓库内锁版本）。docker 后端另有容器预创建池 warm pool 降低冷启延迟（形状匹配复用，每副本独立建池与对账）。

执行模型与关键决策深读见 [DESIGN.md](../DESIGN.md) §12；镜像构建见 [docker/README.md](../docker/README.md)；运维见 [deployment.md](deployment.md)「Docker 沙箱」。

## 7. LLM 每用户治理

每用户滑动窗口限流（`llm_rate_limit_per_minute`）+ 并发租约（`llm_max_concurrency_per_user`），在 `llm_client` 全部公共入口经 `admission_gate()` 单事务原子准入；超限 429 + Retry-After（被拒调用不落行不耗配额）；登录限流同表计数。仅多用户模式生效，单用户零依赖直通。

模型深读见 [DESIGN.md](../DESIGN.md) §13；错误翻译与前端文案见 [backend-core.md](backend-core.md)「LLM 每用户治理」。

## 8. 共享项目反向补丁撤销

多用户模式写路径统一记账：`apply_change` / `apply_whole_files` 成功写盘后按请求级当前用户落一条 op（`workspace_edit_ops` 表 + `<owner>/.edit_logs/` 快照，事务内多文件写共享 op_group 整组回滚）；AI 旁路（写作助手编辑、style_modify）经统一记账自然覆盖。undo/redo 只回退**当前用户自己的**修改：

- 前置校验组内每文件当前 MD5 == 该操作后哈希，冲突即拒绝（409，绝不静默覆盖他人修改）；恢复超配额拒绝（413）；LIFO 整组回滚、redo 对称重放；
- 条数上限 + TTL（无活动超 `edit_log_ttl_hours` 整栈清理）+ 项目删除级联清理；
- v1 的「共享项目禁用撤销」禁令由此解除；单用户模式仍走内存撤销栈，行为不变。

状态机与边界深读见 [DESIGN.md](../DESIGN.md) §14；接口行为见 [backend-core.md](backend-core.md)「反向补丁撤销要点」。

## 9. 邮件通知与自助找回

- **队列化发送**：业务方写 `email_outbox` 行即返回，lifespan 发送协程经 aiosmtplib 投递（指数退避重试、失败留档、全局限速）；`smtp.enabled=false` 时全链路优雅降级。多副本按消息原子认领防重复投递。密码经 `LATEXCELLENCE_SMTP_PASSWORD` 环境变量注入，不落盘。
- **事件矩阵**：待审批提醒（5 分钟聚合发全部 admin）/ 审批结果 / 管理员重置密码 / 找回令牌（每邮箱 5 分钟去抖）/ 配额预警 80%·95%（迟滞：低于 75% 清标记可重发）。
- **自助找回**：forgot-password 公开端点，令牌只存哈希、TTL 1 小时、单次消费防重放；响应恒定时长 + 统一 `sent/unavailable` 语义防枚举；reset 成功即吊销全部会话。
- **邮箱唯一性**：部分唯一索引（空值豁免，存量重复告警跳过、人工修正后重启自愈）；改绑需当前密码确认。

部署配置见 [deployment.md](deployment.md)「SMTP 邮件通知」；模块细节见 [backend-core.md](backend-core.md)「邮件与自助找回要点」。

## 10. 多实例与状态外置

跨请求状态全部外置共享 SQLite（WAL），副本扩展无需粘性会话：

| 状态 | 外置方案 |
| --- | --- |
| 工作区绑定 | `workspace_bindings` 表（权威）+ 进程内 LatexProject 可重建缓存 |
| 逐段检查上下文 | `operation_contexts` 表（进程 dict 降级 L1 缓存） |
| 写作助手确认 / 澄清 / DDG challenge | `pending_interactions` 表 + 短间隔轮询（替代进程内 Event） |
| 登录 / LLM / 注册 / 找回限流 | `rate_limit_events` 表滑动窗口 |
| 会话互斥 | 会话目录旁 flock 锁文件（进程内 asyncio.Lock 叠加） |
| 后台维护循环 | `maintenance_lease` 选主，持租约者执行（warm pool 孤儿对账除外——每副本职责） |
| 配置热重载 | `get_config()` 探测 config.json mtime+size 指纹变化即重载（`multi_user.enabled` 锚定启动值，仍需重启） |
| 日志 | 容器模式 env `LATEXCELLENCE_LOG_STDOUT=1` 切纯 stdout（TimedRotatingFileHandler 非多进程安全） |

编排：仓库根 `docker-compose.yml`（`--scale app=N` 副本扩展 + 可选 Caddy 反代 profile），冒烟脚本 `tools/compose-smoke.sh`。深读见 [DESIGN.md](../DESIGN.md) §15；部署步骤见 [deployment.md](deployment.md)「多实例部署」。

## 11. 数据模型与迁移

### 11.1 SQLite 表总览

| 表 | 用途 |
| --- | --- |
| `users` / `sessions` / `projects` / `project_collaborators` | 账号（argon2 哈希、配额覆盖、注册/登录 IP 审计）、会话（令牌哈希）、项目（`UNIQUE(owner_id, name) WHERE active`、size_bytes 缓存、回收站）、协作者角色 |
| `workspace_edit_ops` | 反向补丁操作索引（seq 定序、op_group、前后 MD5、状态机） |
| `user_derived_usage` | 衍生用量缓存（(user_id, subdir) 主键） |
| `upload_sessions` | 分片上传会话（manifest JSON、位图对位、TTL） |
| `email_outbox` / `password_reset_tokens` / `user_alert_state` | 邮件队列（多副本认领）、找回令牌（哈希）、预警迟滞标记 |
| `rate_limit_events` / `llm_concurrency_leases` | 滑动窗口限流 / LLM 并发租约 |
| `sandbox_slots` | 沙箱并发池租约 |
| `workspace_bindings` / `operation_contexts` / `pending_interactions` / `maintenance_lease` | 多实例状态外置（§10） |

字段级 schema 以 `backend/core/multiuser/db.py` 为准。

### 11.2 版本迁移机制

`PRAGMA user_version` + 有序幂等迁移步骤（禁止散落 ALTER）；`python -m backend.manage db-status` 查看当前版本与待应用步骤。

### 11.3 存量迁移

`python -m backend.manage migrate-legacy`：把存量全局目录（写作会话 / 样式会话 / 模板 / 绘图 / formats / prompts）幂等复制到管理员用户空间（已存在跳过、不覆盖新数据、可 `--dry-run` 预演、输出报告）。config 中无可迁移的工作区记录，旧项目由用户手动上传。

## 12. API 概览

| 组 | 端点 |
| --- | --- |
| 公开 | `GET /api/health`、`GET /api/config/status`（模式与能力透出）、`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/forgot-password`、`POST /api/auth/reset-password` |
| 登录后 | `GET /api/auth/me`（配额与分类用量）、`POST /api/auth/logout`、`PUT /api/auth/profile`（邮箱改绑）、`POST /api/auth/cleanup-derived-data` |
| 项目 | `GET/POST /api/projects`、上传下载回收站协作转交（`/api/projects/{id}/...`）、分片上传五端点（`/api/projects/upload-sessions/...`） |
| 撤销（行为变更） | `POST /api/project/undo` / `redo` 反向补丁回滚/重放；`can-undo` / `can-redo` / `undo-status` 实时按本人补丁栈计算 |
| 管理员 | `GET /api/admin/users`、审批 / 禁用 / 两步式重置密码 / 配额 / 删除，`GET/POST /api/config` |

错误约定：统一错误码 + 前端三语 `errors` 命名空间翻译（`unauthorized` / `invalid_credentials` / `no_permission` / `file_conflict` / `quota_exceeded` / `invalid_archive` / `archive_unsafe` / `sandbox_unavailable` / `sandbox_busy` / `llm_rate_limited` / `upload_session_expired` / `quota_exceeded_recover` 等）。

## 13. 关键决策记录

| 主题 | 决策 |
| --- | --- |
| 双模式 | 配置开关切换；单用户零变化；多用户强制会话认证、禁任意路径与磁盘浏览 |
| 存储 | SQLite（WAL）单机共享卷；不引入 Redis / 消息队列 / Postgres（规模化信号出现再评估） |
| 注册 | 开放注册 + 管理员批准制；自动审批只降级不拒绝；无邀请码 |
| 会话安全 | 令牌只存哈希、CSRF 双提交、登录恒定时长防枚举、禁用即时吊销 |
| 用户空间 | 目录按 `project_id`（SHA256 长路径）派生，路径脱敏不暴露服务器结构 |
| 配额语义 | 项目 / 衍生双额度；写回与恢复超限硬失败且回滚；回收站不计配额 |
| 衍生记账 | DB 缓存 + 检查点增量 + 周期对账，消除逐请求全盘扫描 |
| 上传 | 流式落 staging；解压校验失败即拒绝；zip/7z/rar/目录统一分片协议；不跨解压器重试 |
| 执行安全 | 全部外部命令进 Docker 沙箱（无网络、只读根 fs、资源限额）；local 回落零行为变化 |
| 沙箱镜像 | 按用途拆分四镜像、仓库内锁版本、构建期装宏包（运行时无网络装包） |
| 撤销 | 反向补丁只回退本人修改，冲突即拒绝；写路径统一记账而非逐模块改造 |
| 多实例 | 状态外置 SQLite + flock + 维护选主；单机 compose 一等公民，不做多机横向扩展 |
| LLM 治理 | 限流与并发准入在 llm_client 外层单事务准入；超限 429 不排队 |
| 邮件 | outbox 队列异步发送、未配置优雅降级；找回防枚举（恒定时长 + 统一语义） |
| 自助清理 | 只开放本人数据自助清理，不做管理员强制清理 |

## 14. 非目标与已知限制

**非目标**（不做）：

- 多机横向扩展（NFS / 外部 Postgres / Redis）、注册邮箱验证、管理员强制清理用户数据；
- 项目级永久编辑历史、段级 diff 补丁、三方合并撤销；
- 用户级模型配置（档案与槽位为服务端全局配置，见 [multi-model.md](multi-model.md)）；
- 容器常驻复用（tmpfs 跨执行残留构成跨用户泄漏面，与「每次执行一个全新容器」卫生模型冲突）；
- rootless Docker / Podman（后续演进方向，不承诺）。

**已知限制与风险**：

- `/var/run/docker.sock` 挂载等同授予后端 root 等价能力（镜像最小化 + 只读挂载缓解，部署前必读 [deployment.md](deployment.md) 安全风险声明）；
- Docker Desktop 的路径翻译不受支持（同绝对路径挂载依赖 Linux / WSL2）；
- 沙箱 TeX 镜像按构建清单装宏包，缺宏包需改清单重建（排查步骤见 docker/README）；
- 多副本下 warm pool 各副本独立建池，空闲容器总量上浮为已知代价；
- `.edit_logs` 快照计入用户空间但不占配额，备份口径见 deployment.md；
- SQLite 写吞吐按单机两副本规模设计，busy_timeout 冲突日志频率出现劣化信号时，再评估升级。
