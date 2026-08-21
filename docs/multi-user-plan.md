# 多用户模式设计方案

> 状态：实施中（P0 文档与配置已完成；P1 基础设施已实施并通过登录/审批全流程 API 测试；P2 用户空间与访问控制已实施并通过权限矩阵测试；P3 上传与配额已实施并通过上传安全用例与配额边界测试；P4 权限协作已实施并通过协作场景集成测试；P5 前端已实施并通过构建 + 前端测试 + 端到端 API 联调；P6 迁移与存量隔离已实施并通过迁移幂等 + 会话归属校验 + 存量回归测试；P7 待实施）
> 日期：2026-08-20
> 适用范围：后端双模式（单用户/多用户）、用户空间与上传、配额、权限协作、管理后台、迁移

## 1. 背景与现状

LateXcellence 目前是单用户本地部署形态：

- 前端通过 `frontend/src/api/connection.js` 配置 API 基地址和共享访问令牌（`security.access_token`）；
- 后端通过 `/api/project/open` 直接打开本地任意目录，`/api/project/browse` 可浏览服务器磁盘；
- `workspace_id` 只做多浏览器隔离，工作区状态保存在进程内 `WorkspaceManager`；
- `data/` 下的写作会话、模板、绘图、格式配置等目录均为全局路径，无用户归属；
- 多用户模式下需要执行外部命令的模块（latexmk、Python、pandoc、git/latexdiff 等）面向的是不可信上传内容。

多用户模式的目标是：团队把后端部署到服务器后，用户直接通过浏览器访问前端页面连接后端；每个用户拥有独立的服务器空间，可打包上传本地 LaTeX 项目；项目支持私有与指定用户协作两种可见性；用户可管理自己的空间与项目权限。

## 2. 目标与非目标

### 目标（v1）

- 配置开关实现单用户 / 多用户双模式，单用户模式行为完全保持现状；
- 用户名 + 密码注册，注册后需管理员批准方可登录；支持管理员创建用户、禁用账号、重置密码、调整配额；
- 每个用户独立的项目空间与衍生数据空间，支持配额限制；
- 上传 zip / 7z 压缩包或浏览器目录直传，后端安全解压到用户空间；
- 项目权限：owner / editor / viewer，作用于用户数据文件的读写，不限制具体功能模块；
- 回收站（7 天）、项目下载、项目转交、同名项目自动命名 `xx (2)`；
- 多用户模式下禁用任意路径打开与磁盘浏览；配置接口仅管理员可访问；
- 一次性迁移工具，将存量 `data/` 数据归入管理员账号。

### 非目标（v1 明确不做，列入 v2）

- 不接 SMTP 邮件系统（无邮箱验证、无自助找回密码）；
- 不做 Docker 沙箱（v1 的前提是可信团队内部部署，详见安全章节）；
- 不支持 rar 压缩包（v2 通过 unar/7-Zip 支持）；
- 共享项目的撤销功能 v1 禁用，v2 用反向补丁恢复；
- 不承诺多进程 / 多实例部署（v1 单进程，10k 为注册量而非并发量）。

## 3. 关键决策记录

| 主题 | 决策 |
| --- | --- |
| 双模式 | `multi_user.enabled` 配置开关；单用户模式保持本地目录 + 共享令牌；多用户模式强制登录 |
| 前端连接 | 生产模式由 FastAPI 挂载 `static/` 同源提供；多用户模式下隐藏自定义 API 基地址设置 |
| 注册 | 开放注册但需管理员批准（pending → active）；管理员也可直接创建用户；无邀请码 |
| 认证 | 用户名 + 密码（argon2id）；HttpOnly + SameSite=Lax Cookie 会话；默认 7 天，记住我 30 天；CSRF 用自定义请求头 |
| 存储 | SQLite（WAL）+ 单进程 uvicorn（`workers=1`）；10k 注册量、同时在线规模较小 |
| 用户空间 | `data/users/<user_id>/`，项目与衍生数据按用户隔离 |
| 项目配额 | 默认 500 MB/用户，按解压后项目数据量计算；管理员可按用户覆盖 |
| 衍生数据配额 | 默认 100 MB/用户（写作会话、style_modify 会话、模板、绘图）；`web_cache` 全局共享不计入；`formats`/`prompts` 属配置不计入 |
| 回收站 | 项目删除进回收站，7 天后自动清除；回收站不计配额；恢复时超限则禁止恢复 |
| 写回超限 | 编辑/Agent 写文件导致项目超限时硬失败（403/413） |
| 上传 | zip 与 7z（v1，7z 用 py7zr）；rar v2；浏览器目录直传由前端 JSZip 打包后走同一管线 |
| 上传限制 | 压缩包 ≤ 500 MB；解压后总量 ≤ 剩余配额；文件数 ≤ 20000；单文件 ≤ 100 MB；压缩比 > 1000:1 判炸弹拒绝；加密/分卷压缩包拒绝；大小写冲突路径拒绝 |
| 压缩包布局 | 兼容“包含顶层目录”和“直接是项目内容”两种布局，解压后自动归一化项目根目录 |
| 同名项目 | 自动命名为 `xx (2)`，并发安全（唯一索引 + 重试） |
| 权限 | owner / editor / viewer 三档，按用户名添加；权限只约束用户数据文件读写，不限制功能使用 |
| 协作并发 | 保持 MD5 乐观锁（409 冲突提示）；共享项目 v1 禁用撤销/重做，私有项目保留 |
| LLM 治理 | 每用户请求限流（默认 20 次/分钟）+ 每用户最大并发 LLM 任务（默认 2），均可在配置中调整 |
| 执行安全 | v1 不做强沙箱，前提为可信团队部署；v2 Docker 沙箱隔离 latexmk / Python / pandoc / git 等外部命令 |
| 迁移 | config 不记录工作区（已核实代码，只有前端 localStorage 记录最近项目）；迁移工具只把存量 `data/` 数据归入管理员账号；旧项目由用户手动上传 |
| 管理入口 | 网页管理面板 + CLI 管理工具（`python -m backend.manage`） |

## 4. 总体架构

### 4.1 双模式切换

新增顶层配置 `multi_user`（默认 `enabled: false`）：

```json
{
  "multi_user": {
    "enabled": false,
    "db_path": "data/latexcellence.db",
    "users_root": "data/users/",
    "staging_root": "data/staging/",
    "default_project_quota_mb": 500,
    "default_derived_quota_mb": 100,
    "max_upload_archive_mb": 500,
    "max_upload_files": 20000,
    "max_upload_file_size_mb": 100,
    "max_compression_ratio": 1000,
    "recycle_bin_days": 7,
    "session_ttl_days": 7,
    "remember_ttl_days": 30,
    "llm_rate_limit_per_minute": 20,
    "llm_max_concurrency_per_user": 2,
    "login_rate_limit_per_minute": 5,
    "registration_enabled": true,
    "admin_username_env": "LATEXCELLENCE_ADMIN_USERNAME",
    "admin_password_env": "LATEXCELLENCE_ADMIN_PASSWORD"
  }
}
```

- 单用户模式：现有中间件、`/api/project/open` 任意路径、`/api/project/browse`、共享令牌、前端连接设置全部保持不变；
- 多用户模式：启动时初始化 SQLite 与用户目录，`/api/*` 强制会话认证（公开端点除外），任意路径打开与磁盘浏览禁用，配置接口仅管理员可访问。

### 4.2 运行形态

- v1 固定单进程（`uvicorn` `workers=1`），进程内 `WorkspaceManager` 继续使用；
- 多用户模式下工作区绑定关系为 `workspace_id → (user_id, project_id, project_root)`，创建时写入内存注册表，每次请求从注册表取绑定并**实时**查库校验权限（不缓存权限结果，权限变更立即生效）；
- SQLite 开启 WAL，索引覆盖登录、项目列表、权限查询等高频路径；
- 后续多实例（v2）需要把工作区绑定关系改为可持久化或粘性会话，本方案为其预留独立模块边界（见 16 节）。

### 4.3 用户空间目录布局

```
data/
├── latexcellence.db
├── staging/                          # 上传解压暂存区
└── users/
    └── <user_id>/
        ├── projects/
        │   └── <project_id>/         # 项目根目录（解压后直接落在这里）
        ├── writing_chat_sessions/
        ├── style_modify_sessions/
        ├── templates/
        └── plots/
```

- 存量单用户路径（`data/writing_chat_sessions/` 等）保持原样，供单用户模式使用；多用户模式下业务层不再读取全局路径；
- `data/web_cache/` 保持全局共享；`data/formats/`、`data/prompts/` 保持配置级全局；
- 所有按用户/项目拼接路径的代码必须经过新的统一解析入口（基于 `path_guard.resolve_project_path()` 的封装，如 `resolve_user_path()`），禁止直接拼接。

### 4.4 与现有代码的衔接点

| 现有模块 | 多用户模式下的改动 |
| --- | --- |
| `backend/core/config.py` | 新增 `MultiUserConfig` 子模型；`get_config()` 热重载机制不变 |
| `backend/core/security_middleware.py` | 多用户模式下跳过共享令牌逻辑，改为会话 Cookie 校验 + CSRF 请求头校验 |
| `backend/core/workspace_manager.py` | 增加 `(user_id, project_id)` 绑定与权限校验钩子 |
| `backend/modules/project_router.py` | `/open` 改为接收 `project_id`；`/browse` 多用户模式下返回 403；撤销/重做在共享项目中禁用 |
| `backend/utils/path_guard.py` | 保持不变，新增 `resolve_user_path()` 统一按用户根目录调用它 |
| `frontend/src/api/connection.js` | 多用户模式下固定同源、Cookie 会话、附加 CSRF 头；单用户模式逻辑保留 |
| `frontend/src/store/useStore.js` | 新增 `auth` 切片；多用户模式下以 `projectId` 代替 `projectPath` 恢复项目 |
| `main.py` | 多用户模式下注册认证/用户/项目/管理路由，启动时初始化 DB 与后台清理任务 |

## 5. 数据模型（SQLite）

### users

| 字段 | 说明 |
| --- | --- |
| `id` | UUID，主键 |
| `username` | 登录名，唯一（大小写不敏感），允许中文，2–32 字符 |
| `password_hash` | argon2id 哈希 |
| `email` | 可选，v1 仅展示不用于验证 |
| `role` | `admin` / `user` |
| `status` | `pending` / `active` / `disabled` |
| `project_quota_bytes` | 项目空间配额（NULL 使用全局默认） |
| `derived_quota_bytes` | 衍生数据配额（NULL 使用全局默认） |
| `created_at` / `updated_at` | 时间戳 |

### sessions

| 字段 | 说明 |
| --- | --- |
| `id` | UUID，主键 |
| `user_id` | 用户外键 |
| `token_hash` | 会话令牌的 SHA-256 哈希（唯一） |
| `expires_at` | 过期时间 |
| `remember` | 是否“记住我”（决定默认 7 天或 30 天） |
| `created_at` | 创建时间 |

### projects

| 字段 | 说明 |
| --- | --- |
| `id` | UUID，主键，同时是文件系统目录名 |
| `owner_id` | 所有者用户外键 |
| `name` | 项目名（同一 owner 的活动项目中唯一） |
| `size_bytes` | 项目目录当前大小（缓存值，后台任务定期对账） |
| `status` | `active` / `deleted` |
| `deleted_at` | 进入回收站时间 |
| `created_at` / `updated_at` | 时间戳 |

唯一性通过部分唯一索引实现：`UNIQUE (owner_id, name) WHERE status = 'active'`；同名上传生成 `xx (2)` 时在事务中重试。

### project_collaborators

| 字段 | 说明 |
| --- | --- |
| `project_id` | 项目外键 |
| `user_id` | 协作者用户外键 |
| `role` | `editor` / `viewer`（owner 存于 projects 表） |
| `created_at` | 创建时间 |

主键为 `(project_id, user_id)`。

### 衍生数据用量

衍生数据用量不逐文件入库，采用“创建时检查 + 后台定期扫描对账”：每个用户空间在会话/输出创建时快速统计所属衍生目录大小，超限则拒绝创建；清理任务（沿用各模块现有 TTL）删除过期产物后由对账任务刷新用量。

## 6. 认证与账号

### 6.1 注册与审批

1. 用户提交用户名、密码（至少 8 位）、可选邮箱；
2. 写入 `users`，状态为 `pending`，返回“等待管理员批准”提示；
3. 管理员在管理面板看到待审批列表，批准后状态变为 `active`，用户可登录；管理员也可拒绝/禁用；
4. `registration_enabled=false` 时关闭注册入口，只能由管理员创建账号。

### 6.2 会话

- 登录成功生成随机 32 字节会话令牌，数据库只存哈希；
- Cookie：`HttpOnly`、`SameSite=Lax`、`Secure`（HTTPS 部署时）、`Path=/`；
- 所有 `/api/*` 变更类请求必须携带自定义请求头（如 `X-Latexcellence-CSRF`），防止跨站请求伪造；P1 落地为**双提交模式**：登录时另下发可被 JS 读取的 CSRF Cookie（`latexcellence_csrf`），中间件校验请求头值与该 Cookie 值一致（常量时间比较）；
- 登出删除服务端会话；管理员禁用/删除用户时吊销该用户全部会话；
- 登录接口限流：每用户名 + 每 IP 每分钟 5 次（可配置），失败返回 429；P1 对"用户名不存在"也执行一次占位哈希验证，登录校验恒定时长，防时序侧信道枚举用户名。

### 6.3 管理员

- 首次启动且用户表为空时，从环境变量 `LATEXCELLENCE_ADMIN_USERNAME` / `LATEXCELLENCE_ADMIN_PASSWORD` 创建管理员；未设置则日志警告并等待 CLI 创建；
- CLI：`python -m backend.manage create-admin`、`reset-password <username>`、`list-users`、`migrate-legacy`；
- 管理面板能力：待审批列表、启用/禁用、重置密码（生成临时密码）、调整配额、查看用量、删除用户；
- 删除用户：项目转交管理员并保留 30 天，衍生数据归档 30 天后清理，会话立即吊销。

## 7. 权限与协作

### 7.1 权限矩阵

权限只作用于“用户数据文件”（项目文件、会话文件等），不作用于具体功能模块：

| 动作 | owner | editor | viewer |
| --- | --- | --- | --- |
| 查看项目/文件树/段落/PDF、下载 zip | ✓ | ✓ | ✓ |
| 编译、逐段检查、表达优化、写作助手（只读部分）等所有功能入口 | ✓ | ✓ | ✓ |
| 应用更改、Agent 编辑、撤销/重做、上传覆盖 | ✓ | ✓ | ✗（403） |
| 管理协作者、改名、删除/恢复、转交 | ✓ | ✗ | ✗ |

补充规则：

- viewer 前端提前禁用写操作按钮；若绕过前端直接调用写接口，后端返回 403 `no_permission`；
- 写作助手/Agent 的编辑工具每次写操作前实时校验写权限，viewer 会收到明确错误，不产生部分写入；
- 共享项目（存在任意协作者）v1 禁用撤销/重做（owner 同样禁用）；私有项目保留；
- owner 可将项目转交给其他用户，转交后自己变为 viewer 或直接退出协作（前端确认）；
- 被共享者可以在项目列表看到项目及 owner 信息，权限变更后下一次请求立即生效（权限实时查库）。

### 7.2 访问控制实现

- 新增统一访问控制模块（如 `backend/core/access_control.py`），提供 `require_project_access(user_id, project_id, write=False)` 与 `get_projects_for_user(user_id)`；
- 所有读写项目文件、会话、产物、模板的入口必须经该模块校验，禁止绕过；
- 项目根目录由 `project_id` 派生（`data/users/<owner_id>/projects/<project_id>/`），协作者访问时不复制文件、不生成个人副本，直接读写同一份数据；
- 会话类资源（写作会话、style_modify 会话、绘图、模板）按 `user_id` 目录隔离，服务端按“资源路径必须位于当前用户衍生目录内”校验归属。

> P6 落地说明：在 `userspace.py` 提供请求级当前用户上下文（`set/get_current_user_id`）与统一解析入口 `user_resource_root(subdir, legacy_root)`；写作会话 / style_modify 会话 / 模板 / 绘图四类模块在解析资源根时调用它——多用户模式定向到 `<users_root>/<user_id>/<子目录>/` 并按用户隔离，单用户模式回退存量全局路径（行为不变）。依赖 `dependencies.current_user_context` 从认证会话注入当前用户，四个路由（writing-chat / style-modify / template-gen / plot-gen）统一挂载；后台清理任务通过 `userspace.iterate_user_resource_roots()` 枚举全部用户的资源目录。

## 8. 上传与解压管线

### 8.1 入口

- `POST /api/projects`（multipart，字段 `archive`）接收 `.zip` / `.7z`；
- 浏览器目录直传：前端使用 `webkitdirectory` 收集文件，用 JSZip 在客户端打包后走同一个上传接口；
- 上传流式写入 `data/staging/<upload_id>/`，不整包载入内存；前端通过 XHR 展示上传进度。

### 8.2 解压校验（失败即拒绝，不落用户空间）

1. 压缩包大小 ≤ `max_upload_archive_mb`（500 MB）；
2. 文件数 ≤ 20000；单文件大小 ≤ 100 MB；解压总大小 ≤ 用户剩余项目配额；
3. 压缩比 > `max_compression_ratio`（1000:1）判为压缩炸弹，拒绝；
4. 拒绝绝对路径、`..` 路径、符号链接、设备文件、加密压缩包、分卷压缩包；
5. 解压过程中实时累计大小，超限立即中止并清理暂存区；
6. 文件名编码：zip 优先按 UTF-8 标志解码，失败时尝试 GBK，再回退 cp437；7z 由 py7zr 按 UTF-8 处理；
7. 大小写不敏感重名（如 `README.tex` 与 `readme.tex`）直接报错，提示用户处理；
8. 解压限时（如 120 秒），超时中止。

### 8.3 项目根目录归一化

解压完成后判断布局：

- 暂存区只有一个顶层目录且其中直接包含 LaTeX 文件/子目录 → 该顶层目录为项目根；
- 其余情况 → 暂存区本身为项目根。

两种打包方式（直接打包项目目录、进入目录打包全部内容）均兼容。

### 8.4 落盘与命名

1. 生成 `project_id`，目录为 `data/users/<user_id>/projects/<project_id>/`；
2. 原子移动（先复制到同分区目标再校验，或 rename）后写入 `projects` 行；
3. 项目名与同 owner 活动项目去重，生成 `xx (2)`、`xx (3)`；
4. 事务内处理唯一索引冲突，冲突则重试命名；
5. 失败清理暂存区并返回明确错误（`quota_exceeded` / `upload_too_large` / `invalid_archive` 等）。

## 9. 配额

### 9.1 项目空间（默认 500 MB/用户）

- 只统计活动项目解压后的数据量（缓存 `projects.size_bytes`）；
- 回收站项目不计入配额，删除立即腾出额度；恢复时若会超限则禁止恢复；
- 写回超限：`apply-change`、Agent 编辑等写操作按增量更新缓存大小，超出配额时硬失败（403/413）；
- 转交超限：项目转交前校验接收方剩余配额，会超限则拒绝转交（413），目录与归属保持不变；
- 后台对账任务定期重算各项目大小，修正缓存漂移。

### 9.2 衍生数据空间（默认 100 MB/用户）

- 统计 `writing_chat_sessions`、`style_modify_sessions`、`templates`、`plots` 四个目录；
- 在会话创建/输出写入时检查，超限拒绝创建并提示清理；
- 各模块现有 TTL 清理继续生效，对账任务刷新用量。

### 9.3 管理

- 全局默认值在配置中调整；管理员可按用户覆盖（NULL 表示跟随全局）；
- 前端“账号/空间”页展示项目与衍生数据用量条。

## 10. API 设计

### 10.1 公开端点（多用户模式）

| 方法/路径 | 说明 |
| --- | --- |
| `GET /api/health` | 健康检查（保持公开） |
| `GET /api/config/status` | 返回模式、功能开关（不含密钥；保持公开供登录页判断模式） |
| `POST /api/auth/register` | 注册（`registration_enabled=true` 时） |
| `POST /api/auth/login` | 登录，设置 Cookie |

### 10.2 登录后端点

| 方法/路径 | 说明 |
| --- | --- |
| `GET /api/auth/me` | 当前用户信息、角色、配额与用量 |
| `POST /api/auth/logout` | 登出 |
| `GET /api/projects` | 我的项目 + 我被共享的项目（含 role、owner、用量、`status` 过滤回收站） |
| `POST /api/projects` | 上传创建项目 |
| `GET /api/projects/{project_id}/download` | 打包下载 zip（owner/editor/viewer 均可） |
| `POST /api/project/open` | 多用户模式只接收 `project_id`，返回文件树/主文档（保持现有返回结构） |
| `PUT /api/projects/{project_id}` | 重命名（owner） |
| `DELETE /api/projects/{project_id}` | 进回收站（owner） |
| `POST /api/projects/{project_id}/restore` | 恢复（owner，超限禁止） |
| `DELETE /api/projects/{project_id}/permanent` | 提前永久删除（owner） |
| `GET/POST/PUT/DELETE /api/projects/{project_id}/permissions` | 协作者管理（owner）：按用户名添加、改角色、移除 |
| `POST /api/projects/{project_id}/transfer` | 转交 owner |

### 10.3 管理员端点

| 方法/路径 | 说明 |
| --- | --- |
| `GET /api/admin/users` | 用户列表（含待审批、用量） |
| `POST /api/admin/users/{id}/approve` / `reject` | 审批 |
| `POST /api/admin/users/{id}/disable` / `enable` | 禁用/启用 |
| `POST /api/admin/users/{id}/reset-password` | 重置为临时密码 |
| `PUT /api/admin/users/{id}/quota` | 调整配额 |
| `DELETE /api/admin/users/{id}` | 删除用户（项目转管理员、数据归档 30 天） |

### 10.4 现有端点调整

- `POST /api/project/open`：多用户模式拒绝 `path` 字段，仅接受 `project_id`；
- `GET /api/project/browse`：多用户模式返回 403（防止浏览服务器任意磁盘）；
- `GET /api/workspaces`、`DELETE /api/workspace/{id}`：多用户模式仅管理员可见（调试用途）；
- `GET/POST /api/config`：多用户模式仅管理员可访问；
- 各功能模块端点契约（`workspace_id` + `file_path`）保持不变，但 `file_path` 一律在用户项目根内解析，且写入前校验写权限；
- SSE 端点（逐段检查、写作助手、Agent 等）依赖 Cookie 会话；流启动时校验项目访问权限，流内每次写操作单独校验。

### 10.5 错误约定

新增统一错误码并同步前端三语言翻译（`zh.json` / `zh-TW.json` / `en.json`）：

| 场景 | 状态码 | 错误码 |
| --- | --- | --- |
| 未登录/会话过期 | 401 | `unauthorized` |
| 无项目权限 / viewer 写操作 | 403 | `no_permission` |
| 文件被他人修改 | 409 | `file_conflict`（沿用） |
| 配额/上传超限 | 413 | `quota_exceeded` / `upload_too_large` |
| 压缩包非法 | 400 | `invalid_archive` / `archive_unsafe` |

## 11. 前端设计

### 11.1 新增页面与流程

- 登录页、注册页（含“待管理员批准”状态页）；
- 项目工作台：项目列表（我的 / 共享给我的，展示 owner 与 role）、上传入口、配额用量、回收站入口；
- 上传对话框：选择 zip/7z 文件或选择本地目录（`webkitdirectory` → JSZip 打包），显示上传进度；
- 项目权限对话框：按用户名搜索添加协作者、设置 editor/viewer、移除、转交 owner；
- 管理面板（仅 admin 可见）：待审批、用户管理、配额调整、重置密码、删除用户；
- 多用户模式下隐藏“连接设置/共享令牌”入口，项目打开从“选择本地文件夹”变为“从项目列表打开”。

### 11.2 状态与请求层

- Zustand 新增 `auth` 切片：`user`、`status`、`role`、`quota`、`usage`，退出登录时调用 `clearProject`；
- `connection.js`：多用户模式下 API 基地址固定为同源，不再读取自定义 base/token；`authHeaders()` 改为附加 CSRF 头；SSE 同源自动携带 Cookie，不再追加 token 查询参数；
- `App.jsx` 增加路由守卫：未登录 → 登录/注册页；pending → 等待页；admin → 管理入口；
- viewer 在前端隐藏/禁用所有写操作按钮（应用更改、撤销/重做、编辑工具等），后端 403 仍兜底；
- 项目状态以 `projectId` 恢复（单用户模式继续用 `projectPath`/`rootPath`），两套逻辑按模式分支。

### 11.3 i18n

所有新增文案与错误按既有规范加入 `zh.json`、`zh-TW.json`、`en.json`；后端新增错误码统一走 `translateError()`，禁止组件内硬编码中文兜底。

## 12. 安全

### 12.1 认证与会话

- argon2id 密码哈希；会话令牌仅存哈希；Cookie 设置 `HttpOnly`、`SameSite=Lax`、HTTPS 下 `Secure`；
- 变更类请求强制 CSRF 请求头；登录限流；管理员禁用账号即时吊销会话。

### 12.2 路径与数据隔离

- 多用户模式下所有项目路径由 `project_id` 派生，禁止用户提交服务器绝对路径；
- 所有按用户/项目拼接路径的代码经过统一解析入口（基于 `path_guard`），符号链接默认拒绝；
- 会话类资源严格按用户目录隔离并校验归属；
- `/api/project/browse`、`/open` 任意路径入口在多用户模式关闭。

### 12.3 上传安全

- 压缩包校验清单见 8.2（路径穿越、符号链接、炸弹、加密、编码、大小写冲突）；
- 解压限时、限文件数、限量，暂存区独立于用户空间，失败自动清理。

### 12.4 外部命令执行（重要前提）

v1 **不做强沙箱**。多用户部署后，上传的 LaTeX 项目属于不可信内容，而 latexmk（含 `\write18`）、plot_gen 的 Python、pandoc、git/latexdiff、template_gen/style_modify 编译等均可能在服务器上执行命令。因此：

- v1 的适用范围明确为**可信团队内部部署**（HTTPS + 账号审批制 + 管理员管控），并在部署文档中显著标注风险；
- v2 用 Docker 沙箱隔离上述全部外部命令执行路径，按用户/项目限制 CPU、内存、超时与网络，再开放更广泛的部署场景。

### 12.5 LLM 资源治理

- 每用户每分钟 LLM 请求数上限（默认 20，可配置）；
- 每用户最大并发 LLM 任务数（默认 2，可配置）；
- 超限返回 429，前端展示“请求过于频繁，请稍后再试”。

## 13. 迁移

### 13.1 已核实事实

- `project_router.py` 的 docstring 声称“路径持久化保存到 config.json”，但代码实际未写入；`save_config()` 仅由配置更新接口调用；
- `config.example.json` 不含任何工作区字段；
- “最近打开的项目”只保存在前端 localStorage（Zustand persist）。

结论：config 中没有可迁移的工作区记录，旧项目只能由用户在服务器上手动上传。

### 13.2 迁移工具（`python -m backend.manage migrate-legacy`）

1. 确保管理员账号存在（不存在则提示先 `create-admin`，工具自动取第一个管理员账号为目标）；
2. 将存量 `data/writing_chat_sessions/`、`data/style_modify_sessions/`、`data/templates/`、`data/plots/`、`data/formats/`、`data/prompts/` 复制到管理员用户空间对应目录（源路径取自对应配置项，prompts 取 `data/prompts/` 公共父目录）；
3. 复制完成后校验目录树（逐文件核对目标侧存在性），输出迁移报告；源目录默认保留，确认无误后人工清理；
4. 迁移可重复执行（幂等），目标已存在文件全部跳过，**不覆盖迁移后新产生的数据**；
5. 支持 `--dry-run` 预演（只统计不写盘）。

> P6 已落地：`backend/core/multiuser/migrate.py` + `python -m backend.manage migrate-legacy`，幂等与报告输出见 `tests/test_multiuser/test_p6_legacy_and_migrate.py`。

单用户模式不受影响，旧本地项目路径在单用户模式下继续可用。

## 14. 测试

> P1 已落地：`tests/test_multiuser/`（db schema、auth 核心、注册/审批/登录/CSRF API、管理员 API、CLI）。
> P6 已落地：`tests/test_multiuser/test_p6_legacy_and_migrate.py`（迁移幂等、会话资源按用户隔离、存量单用户回归）+ `test_manage_cli.py` 增补 migrate-legacy 用例。

### 后端单元测试

- 认证：注册审批流、密码哈希、会话过期/吊销、登录限流、CSRF 校验；
- 访问控制：owner/editor/viewer 矩阵、权限变更即时生效、共享项目撤销禁用；
- 配额：上传超限、写回超限、回收站恢复限制、衍生数据限额；
- 上传管线：zip 两种布局、7z 解压、GBK 文件名、zip-slip、符号链接、压缩炸弹、加密包、大小写冲突、`xx (2)` 命名并发；
- 迁移工具：幂等复制、报告输出；
- 所有 Service 层测试继续 Mock LLM，不发起真实网络请求；测试用 LaTeX 数据使用原始字符串。

### API 集成测试

- 登录/注册/审批全流程；未登录访问受保护接口返回 401；
- viewer 写接口返回 403；`/browse` 与任意路径 `/open` 在多用户模式返回 403；
- 上传 → 解压 → 打开 → 检查 → 应用 → 下载全链路；
- `/api/config` 多用户模式仅管理员可访问。

### 前端测试

- 路由守卫、auth 切片、上传进度、权限按钮禁用逻辑、i18n 新增键；
- 沿用现有测试结构（如 `frontend/test/`）。

## 15. 实施阶段

| 阶段 | 内容 | 完成标准 |
| --- | --- | --- |
| P0 文档与配置（✅ 已完成） | 配置模型、config.example、设置页结构、本文档落地 | 配置校验通过，文档评审完成 |
| P1 基础设施（✅ 已完成） | SQLite 初始化、认证（注册/审批/登录/会话/Cookie/CSRF）、管理员引导、CLI | 登录与审批全流程 API 测试通过 |
| P2 用户空间与访问控制（✅ 已完成） | 目录布局、统一路径解析、access_control、workspace 绑定、现有端点收紧 | 任意路径打开与浏览在多用户模式失效，权限矩阵测试通过 |
| P3 上传与配额（✅ 已完成） | 上传/解压管线、项目命名、配额记账、回收站、下载 | 上传安全用例全绿，配额边界行为正确 |
| P4 权限协作（✅ 已完成） | 协作者管理、转交、viewer 写保护、共享项目撤销禁用 | 协作场景集成测试通过 |
| P5 前端（✅ 已完成） | 登录/注册/工作台/上传/权限/管理面板、路由守卫、i18n、auth 切片 | 构建通过 + 前端测试通过 + 端到端 API 联调（登录/审批/上传/回收站/viewer 写保护） |
| P6 迁移与存量隔离（✅ 已完成） | migrate-legacy、会话类资源归属校验、存量路径改造 | 迁移工具幂等验证通过，存量功能回归通过 |
| P7 部署与文档 | 部署文档（HTTPS、风险声明）、README/DESIGN/模块文档同步 | 文档评审通过 |

## 16. v2 预留（明确记录）

- 共享项目撤销恢复（反向补丁，只回退当前工作区自己的修改）；
- rar 压缩包支持（Docker 镜像内置 unar / 7-Zip）；
- Docker 沙箱隔离全部外部命令执行（latexmk、Python、pandoc、git/latexdiff、模板与样式编译），按用户/项目限制资源；
- 多进程/多实例部署（工作区绑定持久化或粘性会话）；
- 大文件分片上传与断点续传；
- SMTP 邮件通知（审批、重置密码、配额预警）与自助找回；
- 衍生数据用量的实时记账优化与更细粒度清理策略。

## 17. 文档同步要求

实施过程中按 AGENTS.md 要求同步：

- 本项目文档加入 `DEVELOPMENT.md` 索引与 `PROJECT_SUMMARY.md` 目录结构；
- 模块落地后更新 `DESIGN.md` 与 `docs/` 下对应模块文档；
- 新增配置项时同步 `config.example.json`、`frontend/src/components/settings/` 设置分区、`SettingsDialog.jsx` 的 `setEnabledFeatures`、`WelcomePage.jsx` 的 `featureGroups`（多用户相关入口按需添加）、`backend/core/config.py`；
- README.md 在功能落地后进行重大功能说明更新。
