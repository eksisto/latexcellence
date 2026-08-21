# LateXcellence — 部署指南

> 本文档覆盖两种部署形态：单用户本地部署（默认）与多用户团队服务器部署。
> 多用户模式的完整设计见 [multi-user-plan.md](multi-user-plan.md)，实现细节见 [backend-core.md](backend-core.md)。

**本文目录**：

- [部署形态总览](#部署形态总览)
- [单用户本地部署（默认）](#单用户本地部署默认)
- [多用户服务器部署](#多用户服务器部署)
  - [前置条件](#前置条件)
  - [构建与启动](#构建与启动)
  - [管理员引导](#管理员引导)
  - [存量数据迁移](#存量数据迁移)
- [HTTPS 与反向代理](#https-与反向代理)
- [安全风险声明（必读）](#安全风险声明必读)
- [数据目录与备份](#数据目录与备份)
- [资源限制与限流配置](#资源限制与限流配置)
- [日常运维](#日常运维)

---

## 部署形态总览

| 形态 | 适用场景 | 认证方式 | 数据位置 |
| --- | --- | --- | --- |
| 单用户本地（默认） | 个人写作助手，前后端分离或全本地 | 共享访问令牌（可选） | 用户本机任意目录 |
| 多用户服务器 | 团队共享一台服务器，浏览器直接访问 | 用户名 + 密码会话 Cookie | 服务器 `data/users/<user_id>/` |

两种形态由 `config.json` 的 `multi_user.enabled` 开关切换；单用户模式行为与历史版本完全一致。

---

## 单用户本地部署（默认）

单用户模式即 README「快速开始」描述的方式：

- **开发模式**：`python main.py config.json` + `cd frontend && npm run dev`；
- **生产模式**：`server.dev_mode=false` + `npm run build` 后由 FastAPI 挂载 `static/` 同源提供；
- **前后端分离**：前端静态站点部署在公网，后端保持 `127.0.0.1` 绑定 + 共享访问令牌，详见 README 与 [development.md](development.md) 的「开发/生产模式」章节。

单用户模式下无需 HTTPS（数据不出本机），但若通过局域网访问建议至少设置 `security.access_token`。

---

## 多用户服务器部署

### 前置条件

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.12+ | 后端运行时 |
| Node.js | 18+ | 仅构建前端时需要（服务器可直接使用仓库内 `static/` 产物） |
| TeX Live | 含 xelatex、latexmk、latexindent、latexdiff | 编译、代码审查、git diff 功能需要 |
| Pandoc | 可选 | Markdown 转 LaTeX 功能需要 |
| Python 包 | `pip install -r requirements.txt` | 已含 `argon2-cffi`（密码哈希）与 `py7zr`（7z 上传解压） |

### 构建与启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 构建前端
cd frontend && npm install && npm run build && cd ..

# 3. 准备配置
cp config.example.json config.json
```

编辑 `config.json` 的关键项：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "dev_mode": false
  },
  "multi_user": {
    "enabled": true
  }
}
```

要点：

- `server.host` 保持 `127.0.0.1`，由反向代理对外提供 HTTPS（见下文）；直接暴露 uvicorn 到公网强烈不建议；
- `dev_mode` 必须为 `false`，否则不会挂载前端静态文件；
- `multi_user.enabled=true` 开启多用户模式。**需重启服务生效**：启动时初始化 SQLite（WAL）数据库、创建用户空间根目录并引导首个管理员；
- `multi_user` 配置段实行严格校验（未知键报错），防止拼写错误导致安全阈值静默回退到默认值；
- 单进程约束：v1 以 `workers=1` 运行（`python main.py config.json` 即是），SQLite WAL 与进程内工作区注册表均以单进程为前提，**不要**用多 worker / 多实例方式启动。

启动：

```bash
python main.py config.json
```

### 管理员引导

首次启动且用户表为空时，按以下优先级创建管理员：

1. **环境变量**（推荐）：

   ```bash
   export LATEXCELLENCE_ADMIN_USERNAME="admin"
   export LATEXCELLENCE_ADMIN_PASSWORD="<强密码>"
   python main.py config.json
   ```

2. **CLI 创建**（未设置环境变量时，日志会警告并等待 CLI）：

   ```bash
   python -m backend.manage create-admin admin --password "<强密码>"
   ```

创建管理员后即可打开站点完成登录；普通用户经注册页提交后进入 `pending` 状态，需管理员在管理面板批准后方可登录。关闭开放注册可设 `multi_user.registration_enabled=false`（仅管理员可建号）。

### 存量数据迁移

从单用户升级到多用户的服务器，可用一次性迁移工具把存量全局数据归入管理员账号：

```bash
python -m backend.manage migrate-legacy [--dry-run]
```

- 幂等可重复执行：目标已存在的文件全部跳过，不覆盖迁移后新产生的数据；
- 覆盖存量 `data/writing_chat_sessions/`、`data/style_modify_sessions/`、`data/templates/`、`data/plots/`、`data/formats/`、`data/prompts/` 等全局目录；
- 复制完成后逐文件校验并输出报告；源目录默认保留，确认无误后人工清理；
- 旧 LaTeX 项目不在自动迁移范围（config 中无工作区记录），由各用户在工作台重新上传。

---

## HTTPS 与反向代理

### 为什么必须 HTTPS

多用户模式的认证完全依赖 Cookie 会话：

- 会话 Cookie 为 `HttpOnly` + `SameSite=Lax`，HTTPS 下自动附加 `Secure` 标志；
- Secure 判定逻辑：请求 scheme 为 `https`，或标准头 `X-Forwarded-Proto: https`（反向代理终止 TLS 时依据此头判定）；
- CSRF 采用双提交模式（`latexcellence_csrf` Cookie + `X-Latexcellence-CSRF` 请求头），明文 HTTP 下该机制的保护意义大打折扣。

因此**公网/局域网部署必须经 TLS 反向代理**。

### nginx 示例

```nginx
server {
    listen 443 ssl;
    server_name latex.example.com;

    ssl_certificate     /etc/letsencrypt/live/latex.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/latex.example.com/privkey.pem;

    # 上传压缩包上限与 multi_user.max_upload_archive_mb 对齐
    client_max_body_size 500m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        # 关键：让后端正确判定 HTTPS 并给 Cookie 加 Secure 标志
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # SSE 必需：逐段检查 / Agent / 写作助手均为流式响应
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
}

server {
    listen 80;
    server_name latex.example.com;
    return 301 https://$host$request_uri;
}
```

### Caddy 示例（自动 HTTPS）

```caddy
latex.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
    request_body {
        max_size 500MB
    }
}
```

### 反向代理注意事项

- **登录限流的 IP 维度会退化**：登录限流按「用户名 + 客户端 IP」计数，反代之后 `request.client.host` 恒为代理 IP，「每 IP」维度失效，「每用户名」维度仍然有效。如需恢复 IP 维度，需在后端前接入受信代理真实 IP 传递（如 nginx `real_ip` 模块）并相应调整后端取值逻辑；
- **SSE 流式端点必须关闭缓冲**（`proxy_buffering off` 或 Caddy `flush_interval -1`），否则逐段检查、Agent 运行、写作助手的进度事件会被代理攒批延迟；
- **请求体上限**需 ≥ `multi_user.max_upload_archive_mb`（默认 500 MB），否则大压缩包上传会在代理层被截断（413）；
- WebSocket 不涉及（实时推送全部走 SSE）。

### 公开端点

多用户模式下无需登录即可访问的端点只有：

| 端点 | 用途 |
| --- | --- |
| `GET /api/health` | 健康检查 |
| `GET /api/config/status` | 模式与功能开关（不含密钥），登录页判断模式用 |
| `POST /api/auth/login` / `POST /api/auth/register` | 登录 / 注册 |

其余 `/api/*` 全部要求会话 Cookie；变更类请求额外要求 CSRF 头。`GET/POST /api/config` 仅管理员可访问。

---

## 安全风险声明（必读）

> **v1 不提供沙箱隔离，适用范围为可信团队内部部署。**

多用户模式下，用户上传的 LaTeX 项目属于**不可信内容**，而以下功能会在服务器上执行外部命令：

| 功能 | 执行的外部命令 | 风险点 |
| --- | --- | --- |
| 自动编译 / git diff / 模板与样式编译 | latexmk（含 `\write18` shell-escape 可能性）、xelatex、latexdiff、git | TeX 宏包可在编译期执行任意命令 |
| 科研绘图 | Python（matplotlib 等绘图脚本） | 上传数据驱动的脚本执行 |
| 格式转换 | pandoc | 转换器漏洞或嵌入脚本 |
| 源码格式化 | latexindent.pl | Perl 执行环境 |

v1 通过以下措施收敛攻击面，但**不能替代沙箱**：

- 注册审批制 + 管理员管控（可禁用/删除账号，即时吊销会话）；
- 登录限流（每用户名 + 每 IP 每分钟 5 次，可配）、恒定时长校验防用户名枚举；
- argon2id 密码哈希、会话令牌仅存 SHA-256、HttpOnly + SameSite=Lax + HTTPS Secure Cookie、双提交 CSRF；
- 用户空间路径隔离（统一经 `path_guard` 解析，符号链接默认拒绝）、任意路径打开与磁盘浏览在多用户模式关闭；
- 上传安全管线（路径穿越 / 符号链接 / 加密包 / 分卷包 / 压缩炸弹 / 大小写冲突拦截，大小、文件数、解压总量、时长受限）；
- 项目 / 衍生数据双配额，写回超限硬失败。

**部署要求**：仅向可信成员开放（团队 / 课题组内部），配合 HTTPS 与账号审批使用；不要面向公网匿名用户提供服务。面向更大范围开放部署需等待 v2 的 Docker 沙箱方案（隔离 latexmk / Python / pandoc / git 等全部外部命令，按用户/项目限制 CPU、内存、超时与网络）。

另请注意：LLM API Key 存于服务器 `config.json`，所有用户的 LLM 调用共用该 Key 出口，请自行评估用量与成本归属。

---

## 数据目录与备份

多用户模式的全部状态集中在 `data/` 下：

| 路径 | 内容 | 备份必要性 |
| --- | --- | --- |
| `data/latexcellence.db` | SQLite（WAL）：用户、会话、项目元数据、协作者关系 | 必须 |
| `data/users/<user_id>/projects/` | 全部用户项目文件 | 必须 |
| `data/users/<user_id>/` 其余子目录 | 写作会话、样式会话、模板、绘图等衍生数据 | 建议 |
| `data/staging/` | 上传暂存区（后台周期清理） | 无需 |
| `logs/` | 运行日志 | 按需 |

备份建议：

- SQLite 处于 WAL 模式，**不要**直接复制 `.db` 文件了事；停机备份最简单，在线备份用 `sqlite3 data/latexcellence.db ".backup 'backup.db'"`；
- `data/users/` 可与数据库快照同窗口整体打包（tar/rsync）；
- 恢复时保持目录结构不变即可，项目目录名即 `project_id`，与库中记录一致。

---

## 资源限制与限流配置

`multi_user` 配置段中与资源治理相关的项（除 `enabled` 需重启外均实时读取配置、支持热重载；设置页仅管理员可见）：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `default_project_quota_mb` | 500 | 每用户项目空间配额（MB），可按用户覆盖 |
| `default_derived_quota_mb` | 100 | 衍生数据（会话/模板/绘图）配额（MB） |
| `max_upload_archive_mb` | 500 | 压缩包大小上限（MB），需与反代请求体上限对齐 |
| `max_upload_files` | 20000 | 解压文件数上限 |
| `max_upload_file_size_mb` | 100 | 单文件解压大小上限（MB） |
| `max_compression_ratio` | 1000 | 压缩比上限（防压缩炸弹） |
| `recycle_bin_days` | 7 | 回收站保留天数 |
| `session_ttl_days` / `remember_ttl_days` | 7 / 30 | 会话有效期（天） |
| `login_rate_limit_per_minute` | 5 | 登录限流（次/分钟，按用户名+IP） |
| `llm_rate_limit_per_minute` | 20 | 每用户 LLM 请求限流（次/分钟）。**注意**：当前版本该阈值的每用户强制执行尚未落地，实际并发由全局 `llm.max_n` 控制，此配置为 v2 预留 |
| `llm_max_concurrency_per_user` | 2 | 每用户最大并发 LLM 任务数。同上，为 v2 预留 |

后台维护循环每小时执行一次：项目大小对账（修正缓存漂移）、回收站过期清理、暂存区清理、删除用户归档清理。

---

## 日常运维

### CLI 管理工具

```bash
python -m backend.manage create-admin <username> [--password xxx]   # 创建/提升管理员
python -m backend.manage reset-password <username> [--password xxx] # 重置密码并吊销其全部会话
python -m backend.manage list-users                                 # 列出用户
python -m backend.manage set-status <username> pending|active|disabled
python -m backend.manage migrate-legacy [--dry-run]                 # 存量数据迁移（幂等）
```

网页管理面板（admin 登录后可见）覆盖同样能力：待审批列表、批准/拒绝、禁用/启用、重置密码（一次性临时密码）、配额调整、删除用户（项目转交管理员、衍生数据归档 30 天后清理）。

### 日志

- 位置：`logs/`（按天旋转，保留 30 天，`logging.dir` 可改）；
- 排查登录问题关注 `multiuser.auth` 的限流与认证告警；上传失败关注 `invalid_archive` / `quota_exceeded` 相关记录。

### 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 登录后刷新即掉线 | 多用户模式要求前端与 API **同源**（Cookie 会话）；若页面与 API 不同域名/端口，`SameSite=Lax` 会阻止跨站携带会话 Cookie。检查是否经同一域名反代访问 |
| 浏览器提示 Cookie 缺少 Secure | 反代未传递 `X-Forwarded-Proto: https`，后端无法判定 HTTPS（功能可用但安全弱化）；补上该头 |
| SSE 进度不动、最后一次性吐出 | 反代开启了响应缓冲；关闭 `proxy_buffering`（nginx）或设 `flush_interval -1`（Caddy） |
| 大压缩包上传 413 | 反代请求体上限低于 `max_upload_archive_mb`；调大 `client_max_body_size` |
| 所有用户共享同一登录限流计数 | 反代后 IP 维度退化所致（见上文），属预期行为；「每用户名」维度仍独立计数 |
| 注册后无法登录 | 正常流程：`pending` 状态需管理员批准；管理面板 → 待审批 |
