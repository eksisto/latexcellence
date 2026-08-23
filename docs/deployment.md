# LateXcellence — 部署指南

> 本文档覆盖三种部署形态：单用户本地部署（默认）、多用户单进程裸机部署与多用户多实例部署（Docker Compose）。
> 多用户模式的完整设计见 [multi-user-plan.md](multi-user-plan.md) 与 [multi-user-v2-plan.md](multi-user-v2-plan.md)，实现细节见 [backend-core.md](backend-core.md)。

**本文目录**：

- [部署形态总览](#部署形态总览)
- [单用户本地部署（默认）](#单用户本地部署默认)
- [多用户服务器部署（单进程裸机）](#多用户服务器部署单进程裸机)
  - [前置条件](#前置条件)
  - [构建与启动](#构建与启动)
  - [管理员引导](#管理员引导)
  - [存量数据迁移](#存量数据迁移)
- [多实例部署（Docker Compose，v2 P7）](#多实例部署docker-composev2-p7)
  - [运行形态](#运行形态)
  - [编排步骤](#编排步骤)
  - [副本扩展与反向代理](#副本扩展与反向代理)
  - [多实例冒烟验收](#多实例冒烟验收)
- [CPU 架构支持（amd64 / arm64）](#cpu-架构支持amd64--arm64)
- [Docker 沙箱（v2 P1a）](#docker-沙箱v2-p1a)
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
| 多用户单进程（裸机） | 小规模团队共享一台服务器 | 用户名 + 密码会话 Cookie | 服务器 `data/users/<user_id>/` |
| 多用户多实例（Compose） | 团队规模增长 / 需要滚动重启与弹性扩缩 | 同上（无粘性会话，任意副本可服务任意请求） | 宿主 `./data` bind 卷（SQLite WAL + 用户空间），全部副本共享 |

三种形态由 `config.json` 的 `multi_user.enabled` 开关与启动方式决定；单用户模式行为与历史版本完全一致。

---

## 单用户本地部署（默认）

单用户模式即 README「快速开始」描述的方式：

- **开发模式**：`python main.py config.json` + `cd frontend && npm run dev`；
- **生产模式**：`server.dev_mode=false` + `npm run build` 后由 FastAPI 挂载 `static/` 同源提供；
- **前后端分离**：前端静态站点部署在公网，后端保持 `127.0.0.1` 绑定 + 共享访问令牌，详见 README 与 [development.md](development.md) 的「开发/生产模式」章节。

单用户模式下无需 HTTPS（数据不出本机），但若通过局域网访问建议至少设置 `security.access_token`。

---

## 多用户服务器部署（单进程裸机）

### 前置条件

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.12+ | 后端运行时 |
| Node.js | 18+ | 仅构建前端时需要（服务器可直接使用仓库内 `static/` 产物） |
| TeX Live | 含 xelatex、latexmk、latexindent、latexdiff | 编译、代码审查、git diff 功能需要 |
| Pandoc | 可选 | Markdown 转 LaTeX 功能需要 |
| Python 包 | `pip install -r requirements.txt` | 已含 `argon2-cffi`（密码哈希）与 `py7zr`（7z 上传解压） |
| unar | 多用户模式接收 rar 上传时必需 | 系统包（如 Debian `apt install unar`）；缺失时 rar 上传返回明确错误（`unar_missing`），zip/7z 不受影响；Docker 后端镜像已内置 |

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
- 单进程前提：裸机形态以 `workers=1` 运行（`python main.py config.json` 即是）。需要多 worker / 多实例时**不要**直接起多个进程，改走下文 Compose 形态——v2 P7 已把跨请求状态外置共享 SQLite，多实例开箱即用，但裸机手工多进程缺少共享卷与统一编排，不在支持范围。

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

## 多实例部署（Docker Compose，v2 P7）

v2 P7 起，多进程 / 多副本部署是一等公民：跨请求共享状态（工作区绑定、逐段检查上下文、写作助手工具确认与澄清、DDG 验证码、登录/LLM 限流、沙箱与 LLM 并发租约、邮件队列）全部外置到共享 SQLite（WAL），任意副本可服务任意请求，**无需粘性会话**。SSE 长连接天然粘在所属连接的副本上，应用层无需处理。

### 运行形态

仓库根 `docker-compose.yml` 提供单机编排：

| 服务 | 说明 |
| --- | --- |
| `app` | 后端本体（`docker/backend/Dockerfile` 构建，与 `docker/build.sh` 同口径 tag `latexcellence-backend:latest`）。healthcheck 打 `/api/health`；环境变量 `LATEXCELLENCE_LOG_STDOUT=1` 强制日志走 stdout/stderr（容器模式交 `docker logs` 收集——`TimedRotatingFileHandler` 多进程共写同一文件在轮转时有 rename 竞争，不可用） |
| `caddy`（profile `proxy`） | 可选反向代理：服务名 DNS 自动重解析到全部健康副本并负载均衡，SSE 已关缓冲 |

关键挂载：

- `./data:/app/data`：SQLite 数据库 + 用户空间 + 暂存区的唯一权威位置，多副本经 WAL + `busy_timeout` 共享访问；
- `./config.json:/app/config.json:ro`：配置运行时注入（凭证不打进镜像）；
- `/var/run/docker.sock:/var/run/docker.sock:ro`：沙箱模式 `auto`/`docker` 需要（只读挂载；风险声明见下文）。无 Docker daemon 的环境删除此行并将 `sandbox.mode` 设为 `local`。

### 编排步骤

```bash
# 0. 前置：安装 Docker 与 compose 插件；准备 config.json（multi_user.enabled=true，
#    server.dev_mode=false）；构建前端产物 static/（打进镜像）
cd frontend && npm install && npm run build && cd ..

# 1. 构建镜像（首次或代码更新后）
docker compose build app          # 或 ./docker/build.sh backend

# 2. 沙箱镜像（编译/绘图/转换功能需要，TeX 大镜像构建耗时较长）
./docker/build.sh                 # 见上文「Docker 沙箱」与 docker/README.md（支持离线 save/load）

# 3. 启动（管理员引导凭证经环境变量注入，仅首次建库时消费）
export LATEXCELLENCE_ADMIN_USERNAME="admin"
export LATEXCELLENCE_ADMIN_PASSWORD="<强密码>"
docker compose --profile proxy up -d --scale app=2   # 附带 Caddy，对外端口默认 8080
```

- 副本数按需调整：`docker compose up -d --scale app=4`（滚动扩缩容，状态在共享卷上，无需停机迁移）；
- 单副本本地试跑可复制 `docker-compose.override.example.yml` 为 `docker-compose.override.yml` 发布 `127.0.0.1:8000` 端口直连；
- 配置修改：编辑宿主 `config.json` 即可，**全部副本经热重载自动生效**（`get_config()` 探测文件 mtime+size 变化即重载）。例外：`multi_user.enabled` 决定认证中间件与数据库初始化等启动期形态，变更仍需重启（热重载会保持启动值并告警）。

### 副本扩展与反向代理

多副本下反代必须满足两点：

1. **服务名每请求重解析**：Caddy 的 `reverse_proxy app:8000` 天然如此（Docker 内嵌 DNS 返回全部健康副本，扩缩容零改动）；nginx 必须用 upstream 变量 + `resolver 127.0.0.11`（直接写死 `upstream server app:8000` 会在启动时解析一次并缓存单个 IP，扩容副本永远不被调度）。
2. **SSE 关闭缓冲**：`flush_interval -1`（Caddy）/ `proxy_buffering off`（nginx），否则逐段检查、Agent 与写作助手的流式事件被攒批延迟。

两种反代的完整可用样例见 [docker/Caddyfile.example](../docker/Caddyfile.example) 与 [docker/nginx.conf.example](../docker/nginx.conf.example)（含上传体积上限对齐、健康探测、HTTPS 生产化说明）。通用注意事项见下文「HTTPS 与反向代理」。

### 多实例冒烟验收

```bash
./tools/compose-smoke.sh            # replicas=2 全链路：health → 注册 → 审批 → 登录
                                    # → 分片上传 → apply-change → undo → 下载校验
./tools/compose-smoke.sh --with-tex # 追加 TeX 编译冒烟（需已构建 latexcellence-tex 镜像）
```

脚本使用独立的数据目录与配置（不触碰宿主 `./data` 与 `config.json`），幂等清理现场。pytest 层面的编排最小验证在 `tests/test_docker/test_compose_deploy.py`（marker `docker`，无 daemon 自动 skip）：compose 配置合法性 + 两副本各自健康 + 跨副本注册/审批/登录。

---

## CPU 架构支持（amd64 / arm64）

后端在 amd64 与 arm64（aarch64）服务器上均可部署，两者均为一等支持目标。代码层面无任何 CPU 架构相关分支（唯一的平台分叉是 Windows/POSIX 进程管理路径，POSIX 路径在 aarch64 Linux 原样可用）：

| 依赖 | arm64 可用性 |
| --- | --- |
| Python 3.12+ | 主流发行版均提供 aarch64 构建 |
| Python 依赖（fastapi / uvicorn / pydantic / argon2-cffi / Pillow / numpy 等） | PyPI 均有 aarch64 wheel 或为纯 Python 实现，无需本地编译 |
| TeX Live | Debian/Ubuntu 官方源全架构支持；上游 `install-tl` 提供 `aarch64-linux` 二进制集；latexmk / latexdiff / texfot 为 Perl 脚本，架构无关 |
| Pandoc | 发行版源或官方发布物均有 arm64 版本 |
| Node.js（仅前端构建期需要） | 官方提供 arm64 二进制；构建产物为静态文件，与运行架构无关 |

注意事项：

- **联网搜索的浏览器回退池（Playwright，可选功能）**：arm64 Linux 上 Chromium/Firefox 可用，WebKit 的 arm64 支持随版本变动；该能力初始化失败仅降级并记 warning，不影响主流程。部署后建议跑一次写作助手联网搜索冒烟，确认实际可用性；
- **发布冒烟**：若构建/发布环境与生产架构不同，建议在目标架构上完成一次最小冒烟（启动 → 登录 → 上传 → 编译）再对外提供服务；
- v2 的 Docker 沙箱镜像同样支持在 arm64 上原生构建（TeX Live `aarch64-linux` 二进制集与 pandoc 官方 arm64 发布物），多架构 buildx 构建见 [docker/README.md](../docker/README.md)。

---

## Docker 沙箱（v2 P1a）

v2 起全部外部命令执行（latexmk/texfot 编译、git/latexdiff、pandoc、Python 绘图、latexindent）统一经 SandboxRunner，可进 Docker 容器隔离执行。由顶层 `sandbox` 配置节控制：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `auto` | `auto`：探测到 Docker daemon 即容器执行，不可达回落本地进程并告警；`docker`：强制容器执行，daemon 不可达返回 503 `sandbox_unavailable`（不静默回落）；`local`：保持本地子进程（行为与 v1 一致） |
| `image_tex` / `image_convert` / `image_plot` | `latexcellence-*:latest` | 三类沙箱镜像名（tex=编译/git/latexdiff/latexindent；convert=pandoc；plot=Python 绘图） |
| `cpus` / `memory_mb` / `pids_limit` / `tmpfs_mb` | 1.0 / 1024 / 256 / 512 | 单容器资源上限 |
| `max_concurrent_global` / `max_concurrent_per_user` | 4 / 1 | 沙箱并发池上限（每用户租约存 SQLite，超限返回 429 `sandbox_busy`） |
| `startup_grace_seconds` | 10 | 容器冷启动宽限（叠加在各功能既有超时之上） |

### 镜像构建

```bash
cd docker/
./build.sh                 # 构建全部四镜像，tag 为 latexcellence-*:YYYY.MM + latest
./build.sh tex             # 只构建 tex 镜像
BUILDX_MULTI_ARCH=1 ./build.sh --load tex   # buildx 双架构（amd64+arm64）
```

镜像体系、离线导入导出（`docker save/load`）、升级流程与缺 TeX 宏包排查指南见 [docker/README.md](../docker/README.md)。TeX 宏包清单在 `docker/tex/Dockerfile` 的 `EXTRA_TL_PACKAGES` 构建参数中维护——运行期不装包（沙箱无网络原则），缺宏包时改清单重建镜像。

### 容器安全基线（后端强制注入）

- `--network none`：无网络；
- 根文件系统只读 + tmpfs `/tmp`（容量 `tmpfs_mb`）：产物只能落在挂载的项目目录；
- `--memory` / `--cpus` / `--pids-limit` 资源限额，OOM 终止翻译为明确错误提示；
- `--security-opt no-new-privileges`、drop ALL capabilities、非 root（与宿主后端同 uid:gid，产物属主一致）。

### 平台支持矩阵与前提

- **支持**：Linux 原生 amd64 / arm64；
- **Windows**：建议 WSL2 内部署（Docker Desktop 的路径翻译会破坏"容器内外同绝对路径"挂载语义，不受支持）；
- **macOS**：Docker Desktop 同理不支持同路径挂载，请保持 `mode=local` 或 `auto`（自动回落）；
- 后端需能访问 `/var/run/docker.sock`（部署在容器内时以只读方式挂载该 socket）。

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

多实例（Compose）形态的反代样例：服务名 DNS 重解析 + 健康探测的完整配置见 [docker/Caddyfile.example](../docker/Caddyfile.example) 与 [docker/nginx.conf.example](../docker/nginx.conf.example)。

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
> **v2 P1a 起提供 Docker 沙箱**：`sandbox.mode=auto`（探测到 Docker 即用）或 `docker` 时，下列外部命令全部在无网络、只读根文件系统、资源限额的容器内执行，适用范围可相应放宽；`local` 模式（含 auto 回落）不改变 v1 风险结论。

多用户模式下，用户上传的 LaTeX 项目属于**不可信内容**，而以下功能会在服务器上执行外部命令：

| 功能 | 执行的外部命令 | 风险点 |
| --- | --- | --- |
| 自动编译 / git diff / 模板与样式编译 | latexmk（含 `\write18` shell-escape 可能性）、xelatex、latexdiff、git | TeX 宏包可在编译期执行任意命令 |
| 科研绘图 | Python（matplotlib 等绘图脚本） | 上传数据驱动的脚本执行 |
| 格式转换 | pandoc | 转换器漏洞或嵌入脚本 |
| 源码格式化 | latexindent.pl | Perl 执行环境 |
| rar 上传解压（多用户 v2 P2） | unar | 解析攻击者完全可控的压缩包字节流，转换器漏洞风险与 pandoc 同级 |

> **已知例外：unar 未入沙箱**。自 v2 P1a 起，上表中 latexmk / xelatex / latexdiff / git、绘图 Python、pandoc、latexindent 等外部命令均已由 SandboxRunner（Docker 沙箱）收口；唯 **unar 目前仍是后端进程的子进程直跑，未纳入沙箱**（v2 P2 例外项），因此**不与上表其他命令共享同一风险口径**——即使沙箱已启用，unar 仍以宿主后端进程身份解析攻击者可控的字节流。现有缓解措施：
>
> - stdin 关闭（`/dev/null`），防加密包交互式索要密码挂起；
> - 独立进程组启动，限时 / 超解压配额即 SIGKILL 整组硬中断；
> - 解压只写入独立暂存区，落位用户空间前经全量安全复核；
> - RAR4 头链加密 / 分卷标志预扫（按块头声明的数据区长度整段跳过，不误拒带恢复记录等服务数据的合法包）。
>
> **跟踪项：将 unar 纳入 SandboxRunner 收口**（尚未实现，后续版本落地）。验收标准：
>
> - convert 镜像内置 unar；
> - 归档暂存文件以只读方式挂载进容器，解压目录以可写方式挂载；
> - 解压改经 `get_sandbox_runner().execute` 执行，沿用无网络、只读根文件系统、资源限额的安全基线；
> - 真容器冒烟测试覆盖 rar 上传解压全链路（含正常包解压与加密 / 分卷包拒绝路径）。

v1 通过以下措施收敛攻击面，但**不能替代沙箱**：

- 注册审批制 + 管理员管控（可禁用/删除账号，即时吊销会话）；
- 登录限流（每用户名 + 每 IP 每分钟 5 次，可配）、恒定时长校验防用户名枚举；
- argon2id 密码哈希、会话令牌仅存 SHA-256、HttpOnly + SameSite=Lax + HTTPS Secure Cookie、双提交 CSRF；
- 用户空间路径隔离（统一经 `path_guard` 解析，符号链接默认拒绝）、任意路径打开与磁盘浏览在多用户模式关闭；
- 上传安全管线（路径穿越 / 符号链接 / 加密包 / 分卷包 / 压缩炸弹 / 大小写冲突拦截，大小、文件数、解压总量、时长受限）；
- 项目 / 衍生数据双配额，写回超限硬失败。

**部署要求**：`sandbox.mode=local`（或 auto 回落生效）时，仅向可信成员开放（团队 / 课题组内部），配合 HTTPS 与账号审批使用；不要面向公网匿名用户提供服务。启用 Docker 沙箱（auto 命中 / docker 模式）后，外部命令已在容器内隔离，但仍建议保持账号审批与 HTTPS——沙箱隔离的是"命令执行"，不改变认证、配额与数据隔离的必要性。

**Docker socket 风险（启用沙箱时必读）**：后端驱动沙箱容器需要访问 `/var/run/docker.sock`，该权限等价于授予 root 级能力（可创建任意容器挂载宿主文件系统）。缓解措施：镜像最小化且不含凭证、socket 以只读方式挂载、宿主 Docker daemon 保持最新安全补丁。rootless Docker / Podman 作为后续演进方向（v2 不承诺）。沙箱不可用时的降级路径见上文 `mode=local` 的风险口径。

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
- 恢复时保持目录结构不变即可，项目目录名即 `project_id`，与库中记录一致；
- **多实例部署的停机窗口**：数据库快照与 `data/users/` 打包必须落在同一时间点，而多副本会持续写入——备份窗口内先缩容到单副本（`docker compose up -d --scale app=1`，秒级完成、不丢状态），完成快照后再扩回去；直接对活跃多副本做在线备份可能得到项目文件与 op 记录不一致的快照；
- Compose 形态下 `logs/` 不存在（stdout 日志），日志留存交给 `docker logs` 的日志驱动（如 `local`/`json-file` 的 max-size/max-file 配置）。

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
| `login_rate_limit_per_minute` | 5 | 登录限流（次/分钟，按用户名+IP；v2 P1b 起计数入 `rate_limit_events` 表，多实例部署不再被副本数放大） |
| `llm_rate_limit_per_minute` | 20 | 每用户 LLM 请求限流（次/分钟，60s 滑动窗口；超限返回 429 并附 Retry-After。多用户模式强制执行） |
| `llm_max_concurrency_per_user` | 2 | 每用户最大并发 LLM 任务数（租约制，超限直接 429 不排队；仅多用户模式生效） |

后台维护循环每小时执行一次：项目大小对账（修正缓存漂移）、衍生用量对账与配额预警检查、回收站过期清理、暂存区清理、删除用户归档清理、沙箱并发租约（`sandbox_slots`）过期清理、LLM 并发租约（`llm_concurrency_leases`）过期清理与限流事件（`rate_limit_events`）保留期清理、自助找回令牌与邮件留档清理、逐段检查上下文与待定交互 TTL 清理、崩溃副本残留的 sending 邮件行回收。**多实例部署下经 `maintenance_lease` 表选主：持租约的副本执行循环体，其余副本跳过本轮；租约到期自动易主，持有人崩溃后最迟一个租约 TTL 内接管。** 模块各自的 cleanup_loop（模板/绘图/样式会话 TTL 清理）保持每副本独立运行（幂等无害）。

LLM 每用户治理（v2 P1b）：多用户模式下每次 LLM 调用前在单事务内完成"滑动窗口限流校验 + 事件落账 + 并发租约获取"（`core/multiuser/llm_governance.py`），请求级用户上下文由 `AccessSecurityMiddleware` 会话验证成功后全局注入，覆盖全部 `/api` 端点；被拒绝的调用不落行、不消耗配额。429 响应遵循既有结构化错误约定（`detail.error` = `llm_rate_limited` / `llm_concurrency_limited`）、附 `Retry-After` 头与秒数明细，前端三语言文案展示等待时间。单用户模式完全跳过治理（零数据库依赖）。P7 多实例阶段同一共享卷上天然跨副本生效，无需修改。

## SMTP 邮件通知（v2 P6）

`smtp` 配置段（设置页管理员分区或直接编辑 config.json；保存即生效，无需重启）：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | false | 总开关；false 时全部通知静默跳过、登录页隐藏找回入口 |
| `host` / `port` | "" / 465 | SMTP 服务器地址与端口（host 为空同样视为未启用） |
| `encryption` | ssl | `ssl`（隐式 TLS）/ `starttls` / `none`（仅本地调试） |
| `username` / `password` | "" | 认证凭据；password 走 `***` 掩码协议（设置页回显掩码，原样保存即保留原值） |
| `from_address` / `from_name` | "" / LateXcellence | 发件人（from_address 留空回退 username） |
| `site_base_url` | "" | 站点对外地址，用于拼接找回链接；留空时邮件退化为相对路径提示，**建议配置**否则找回链接不可直接点击 |
| `max_send_per_minute` / `retry_attempts` | 10 / 3 | 发送限速（滚动 60s 窗口）与失败重试次数（指数退避） |
| `template_language` | zh | 邮件模板语言（zh/en） |

启用后行为：注册待审批（全部管理员，5 分钟聚合）、审批通过/拒绝/禁用、管理员重置密码（含临时密码）、自助找回令牌（每邮箱 5 分钟 1 次）、配额预警 80%/95%（迟滞，低于 75% 重置）均经 `email_outbox` 队列异步投递。

**运维要点**：

- 投递失败重试 `retry_attempts` 次后留档 failed（`email_outbox.last_error`），排查 SQL：`SELECT recipient, subject, last_error, attempts FROM email_outbox WHERE status='failed'`；sent/failed 留档保留 7 天自动清理。
- 邮件发送依赖后端进程出站网络可达 SMTP 服务器；容器部署需放行对应出站端口（465/587）。
- `site_base_url` 必须是用户浏览器可达的地址（经反向代理时填对外域名），否则找回邮件中的链接无法直接使用。
- 未启用 SMTP 不影响任何主流程：注册/审批/登录/上传照常，找回入口自动隐藏，忘记密码的用户由管理员经管理面板重置。

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

网页管理面板（admin 登录后可见）覆盖同样能力：待审批列表、批准/拒绝、禁用/启用、重置密码（两步式：先仅生成临时密码预览，管理员确认后才写入并吊销会话）、配额调整、删除用户（项目转交管理员、衍生数据归档 30 天后清理）。

### 日志

- 裸机：`logs/`（按天旋转，保留 30 天，`logging.dir` 可改）；容器 / 多实例：设置 `LATEXCELLENCE_LOG_STDOUT=1` 只输出 stdout/stderr，交 `docker logs` 收集（多进程共写轮转文件不安全，compose 编排已默认开启）；
- 排查登录问题关注 `multiuser.auth` 的限流与认证告警；上传失败关注 `invalid_archive` / `quota_exceeded` 相关记录。

### 监控建议（多实例部署）

| 观测点 | 含义与处置 |
| --- | --- |
| `database is locked` / busy 冲突日志频率 | SQLite 写锁竞争信号。两副本规模下偶发属正常；频率上升说明写吞吐逼近上限——先排查异常高频写入（如限流事件），再评估缩副本或迁移 Postgres（v3 议题） |
| `email_outbox` 中 `status='failed'` 行数 | 邮件投递失败留档。`SELECT COUNT(*) FROM email_outbox WHERE status='failed'` 持续增长时检查 SMTP 配置、出站网络与 `last_error` 明细 |
| 沙箱并发池水位 | `sandbox_slots` 活跃行数 vs `max_concurrent_global`：`SELECT COUNT(*) FROM sandbox_slots WHERE expires_at > now`。频繁打满会返回 429 `sandbox_busy`，按需调大全局上限 |
| 维护选主状态 | `SELECT holder, expires_at FROM maintenance_lease`：确认只有一个活跃持有者且 `expires_at` 在滚动续期；长期无持有者说明全部副本的维护循环异常（查日志 `Multi-user maintenance cycle failed`） |
| 待定交互堆积 | `pending_interactions` 行数应在个位数量级；持续增长说明 SSE 等待侧异常退出未消费（过期清理兜底，但仍应排查） |

### 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 登录后刷新即掉线 | 多用户模式要求前端与 API **同源**（Cookie 会话）；若页面与 API 不同域名/端口，`SameSite=Lax` 会阻止跨站携带会话 Cookie。检查是否经同一域名反代访问 |
| 浏览器提示 Cookie 缺少 Secure | 反代未传递 `X-Forwarded-Proto: https`，后端无法判定 HTTPS（功能可用但安全弱化）；补上该头 |
| SSE 进度不动、最后一次性吐出 | 反代开启了响应缓冲；关闭 `proxy_buffering`（nginx）或设 `flush_interval -1`（Caddy） |
| 大压缩包上传 413 | 反代请求体上限低于 `max_upload_archive_mb`；调大 `client_max_body_size` |
| 所有用户共享同一登录限流计数 | 反代后 IP 维度退化所致（见上文），属预期行为；「每用户名」维度仍独立计数 |
| 注册后无法登录 | 正常流程：`pending` 状态需管理员批准；管理面板 → 待审批 |
