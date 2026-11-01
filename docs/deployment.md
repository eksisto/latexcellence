# LaTeXcellence — 后端部署指南

> 本文档面向想把 LaTeXcellence 部署到自己服务器上的使用者，覆盖三种部署形态的完整流程：单用户本地部署（默认）、多用户单进程裸机部署与多用户多实例部署（Docker Compose）。
>
> 多用户模式的设计背景见 [multi-user.md](multi-user.md)，实现细节见 [backend-core.md](backend-core.md)。**部署本身不需要阅读这些文档**——本文是自包含的。

**本文目录**：

- [部署形态总览与选型](#部署形态总览与选型)
- [快速开始（多用户最小可用路径）](#快速开始多用户最小可用路径)
- [环境准备](#环境准备)
- [单用户本地部署（默认）](#单用户本地部署默认)
- [多用户服务器部署（单进程裸机）](#多用户服务器部署单进程裸机)
  - [前置条件](#前置条件)
  - [安装与启动](#安装与启动)
  - [关键配置说明](#关键配置说明)
  - [multi_user 配置参考](#multi_user-配置参考)
  - [管理员引导](#管理员引导)
  - [使用 systemd 托管服务](#使用-systemd-托管服务)
  - [存量数据迁移（单用户升级为多用户）](#存量数据迁移单用户升级为多用户)
  - [部署验收清单](#部署验收清单)
- [多实例部署（Docker Compose）](#多实例部署docker-compose)
- [CPU 架构支持（amd64 / arm64）](#cpu-架构支持amd64--arm64)
- [Alpine 系发行版实测记录（postmarketOS 等）](#alpine-系发行版实测记录postmarketos-等)
- [Docker 沙箱](#docker-沙箱)
- [HTTPS 与反向代理](#https-与反向代理)
- [安全风险声明（必读）](#安全风险声明必读)
- [SMTP 邮件通知](#smtp-邮件通知)
- [数据目录与备份](#数据目录与备份)
- [环境变量参考](#环境变量参考)
- [升级与回滚](#升级与回滚)
- [日常运维](#日常运维)

---

## 部署形态总览与选型

| 形态 | 适用场景 | 认证方式 | 数据位置 |
| --- | --- | --- | --- |
| 单用户本地（默认） | 个人写作助手，前后端分离或全本地 | 共享访问令牌（可选） | 用户本机任意目录 |
| 多用户单进程（裸机） | 小规模团队共享一台服务器 | 用户名 + 密码会话 Cookie | 服务器 `data/users/<user_id>/` |
| 多用户多实例（Compose） | 团队规模增长 / 需要滚动重启与弹性扩缩 | 同上（无粘性会话，任意副本可服务任意请求） | 宿主 `./data` bind 卷（SQLite WAL + 用户空间），全部副本共享 |

选型建议：

- 只有一两个人使用 → **单用户**最简单，行为与历史版本完全一致；
- 一个课题组 / 团队共用一台服务器，需要各自独立的项目空间、配额与权限协作 → **多用户单进程**，一台机器一个进程足够支撑小规模使用；
- 用户量增长到单进程吃紧，或者希望滚动重启不中断服务 → **多实例 Compose**，跨请求状态已全部外置共享 SQLite，副本扩展开箱即用。

三种形态由 `config.json` 的 `multi_user.enabled` 开关与启动方式决定。

---

## 快速开始（多用户最小可用路径）

以下是从一台全新 Linux 服务器到「管理员可登录」的最短路径，每步的详细说明见对应章节：

```bashd
# 0. 安装系统依赖（Debian/Ubuntu 示例，见「环境准备」）
sudo apt install -y python3 python3-venv texlive-xetex texlive-latex-extra \
    texlive-extra-utils latexmk texlive-lang-chinese pandoc unar git

# 1. 获取代码、安装 Python 依赖
git clone https://github.com/eksisto/latexcellence.git && cd latexcellence
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 构建前端（多用户模式要求前后端同源，必须让后端托管静态页面）
cd frontend && npm install && npm run build && cd ..

# 3. 准备配置：开启多用户、关闭开发模式、填入 LLM Key
cp config.example.json config.json
# 编辑 config.json：server.dev_mode=false、multi_user.enabled=true、llm.api_key=...

# 4. 首次启动（环境变量引导首个管理员，仅在用户表为空时消费）
export LATEXCELLENCE_ADMIN_USERNAME="admin"
export LATEXCELLENCE_ADMIN_PASSWORD="<强密码>"
python main.py config.json

# 5. 验收
curl -s http://127.0.0.1:8000/api/health        # {"status":"ok",...}
curl -s http://127.0.0.1:8000/api/config/status # multi_user 应为 true
```

生产环境请继续完成两件事：用 systemd 把进程托管起来（见「使用 systemd 托管服务」），并在前面架一层 HTTPS 反向代理（见「HTTPS 与反向代理」）。多用户的会话 Cookie 在公网/局域网必须走 TLS。

---

## 环境准备

### 系统依赖

| 依赖 | 要求 | 用途 | 缺失时的表现 |
| --- | --- | --- | --- |
| Python | 3.12+ | 后端运行时 | 无法启动 |
| Node.js | 20+ | 仅构建前端时需要（产物为静态文件，运行期不需要 Node） | 无法产出 `static/`，生产模式无页面 |
| TeX Live | xelatex、latexmk、texfot | 自动编译、模板/样式编译、绘图 TikZ 编译 | 编译类功能报错 |
| TeX Live | latexdiff | git diff 功能的 latexdiff 引擎（默认 difflib 引擎不需要） | 切换到该引擎时报错 |
| TeX Live | latexindent | 源码格式化层 | 回落到内置轻量格式化器 |
| TeX Live 中文宏包 | xeCJK 等 | 编译中文 LaTeX 文档 | 中文文档编译缺宏包报错 |
| Pandoc | 可选 | Markdown/DOCX/HTML 转 LaTeX | 格式转换功能不可用 |
| unar | 多用户模式接收 rar 上传时的解压器之一（优先选用） | rar 解压 | rar 上传返回明确错误（`rar_extractor_missing`），zip/7z 不受影响 |
| libarchive-tools（提供 bsdtar） | 可选，rar 解压的另一选择；无 unar 的发行版（如 Alpine/postmarketOS）用它替代 | rar 解压（unar 缺失时自动回退） | 同上（两者皆缺才报错） |
| Docker | 可选 | 沙箱隔离外部命令执行（强烈建议多用户部署启用） | 沙箱回落本地子进程，见「安全风险声明」 |

Debian/Ubuntu 安装示例（其他发行版按包名对应关系调整；TeX 组件在不同发行版的包拆分可能不同。**Alpine 系 / musl libc 环境（含 postmarketOS）的完整实测差异见下文[「Alpine 系发行版实测记录」](#alpine-系发行版实测记录postmarketos-等)，逐条核对后再动手**）：

```bash
sudo apt update
# Python 与基础工具
sudo apt install -y python3 python3-venv python3-pip git curl
# TeX Live：xelatex(texlive-xetex)、latexindent/latexdiff/texfot(texlive-extra-utils)、
#           中文宏包(texlive-lang-chinese)，latexmk 为独立包
sudo apt install -y texlive-xetex texlive-latex-extra texlive-extra-utils \
    latexmk texlive-lang-chinese
# 可选：pandoc（格式转换）、unar 或 libarchive-tools（rar 上传解压，二选一）
sudo apt install -y pandoc unar
```

发行版源中的 Node.js 可能偏旧，构建前端建议通过 nvm 或 NodeSource 安装较新的 LTS 版本。

### Python 环境

建议使用虚拟环境，避免污染系统 Python：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

依赖清单已包含多用户模式所需的全部 Python 包（argon2 密码哈希、7z 解压、SMTP 异步发送、Docker SDK 等）。联网搜索的可选浏览器回退池基于 Playwright：Python 包已随依赖安装，但浏览器内核需要另行执行 `playwright install chromium`（或 `firefox`）安装。该能力默认关闭（`features.function_calling.web_search.enabled=false` 且其 `page_reading.browser.enabled=false`）。启用方法、资源上限与灰度建议见 [writing-chat-web-access-architecture.md](writing-chat-web-access-architecture.md) 与 [web-access-operations.md](web-access-operations.md)。

### 前端构建

```bash
cd frontend
npm install
npm run build
```

构建产物输出到仓库根的 `static/`。生产模式（`server.dev_mode=false`）下 FastAPI 会把它作为静态站点挂载，与 API 同源提供服务。挂载使用带 SPA 回退的 `SPAStaticFiles`（见 `backend/utils/spa_static_files.py`）：刷新 / 直达 `/formula-ocr` 这类 history 路由时回退 `index.html` 交给前端路由接管。静态资源缺失与 `/api/*` 未命中仍保持原样 404。

> 构建机与运行机分离时，在构建机上完成 `npm run build` 后，把整个仓库（含 `static/`）同步到服务器即可。服务器自身不需要装 Node。

---

## 单用户本地部署（默认）

单用户模式即 README「快速开始」描述的方式：

- **开发模式**：`python main.py config.json` + `cd frontend && npm run dev`；
- **生产模式**：`server.dev_mode=false` + `npm run build` 后由 FastAPI 挂载 `static/` 同源提供；
- **前后端分离**：前端静态站点部署在公网，后端保持 `127.0.0.1` 绑定 + 共享访问令牌，详见 [development.md](development.md) 的「开发/生产模式」章节。

单用户模式下无需 HTTPS（数据不出本机），但若通过局域网访问建议至少设置 `security.access_token`。

---

## 多用户服务器部署（单进程裸机）

### 前置条件

在「环境准备」的基础上，还需确认：

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| unar 或 bsdtar（libarchive-tools） | 至少安装其一（仅 `sandbox.mode=local` 及 auto 回落需要） | 多用户模式接收 rar 上传时必需，unar 优先、缺失自动回退 bsdtar（Docker 沙箱模式下解压在 convert 镜像内执行，镜像已内置双工具，宿主无需安装；Alpine 系源无 unar，用 `apk add libarchive-tools`） |
| Docker daemon | 可选但强烈建议 | 启用沙箱隔离（`sandbox.mode=auto/docker`）的前提，见「Docker 沙箱」与「安全风险声明」 |
| HTTPS 反代 | 公网/局域网部署必备 | 见「HTTPS 与反向代理」 |

### 安装与启动

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
  "llm": {
    "api_key": "<你的 API Key>",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash"
  },
  "multi_user": {
    "enabled": true
  }
}
```

要点：

- `server.host` 保持 `127.0.0.1`，由反向代理对外提供 HTTPS（见下文）；直接暴露 uvicorn 到公网强烈不建议；
- `dev_mode` 必须为 `false`，否则不会挂载前端静态文件；
- `llm.api_key` 也可不改配置、改用环境变量 `LX_LLM_KEY` 注入（优先级更高，且永不写盘，见「环境变量参考」）。多用户模式的 `smtp.password` 同理，用 `LATEXCELLENCE_SMTP_PASSWORD` 注入。联网搜索（Tavily）与学术数据源（OpenAlex / Semantic Scholar / CORE / Unpaywall / PubMed）的 API Key 亦同，用 `LATEXCELLENCE_TAVILY_API_KEY`、`LATEXCELLENCE_OPENALEX_API_KEY` 等注入；
- `multi_user.enabled=true` 开启多用户模式。**需重启服务生效**：启动时初始化 SQLite（WAL）数据库、创建用户空间根目录并引导首个管理员。其余 `multi_user` 配置项均支持配置热重载，改完保存即生效；
- **多用户模式下前端必须与 API 同源部署**（会话 Cookie 为 `SameSite=Lax`，跨域携带会被浏览器拦截）：由后端托管 `static/` 静态页面，经同一个域名反代对外。不要把前端单独发到另一个域名再跨域调 API（这是与单用户分离部署的关键差异）；
- `multi_user`、`sandbox`、`smtp` 三个配置段实行严格校验（未知键直接报错），防止拼写错误导致安全阈值静默回退到默认值；
- 单进程前提：裸机形态以单 worker 运行（`python main.py config.json` 即是）。需要多 worker / 多实例时**不要**直接起多个进程，改走下文 Compose 形态：跨请求状态已外置共享 SQLite，多实例开箱即用；裸机手工多进程缺少共享卷与统一编排，不在支持范围；
- 工作目录必须是仓库根：`static/`、`data/`、`logs/` 及各类默认路径均为相对路径，从其他目录启动会导致静态页与数据落点错乱。

启动：

```bash
python main.py config.json
```

### 关键配置说明

除 `multi_user` 外，部署时经常需要确认的配置段：

| 配置段 | 部署关注点 |
| --- | --- |
| `server.allowed_origins` | CORS 白名单，用于单用户前后端分离等跨域场景；多用户同源部署下浏览器不触发跨域，一般无需改动 |
| `security.access_token` | 单用户模式的共享访问令牌；多用户模式下不参与认证（由会话 Cookie + CSRF 取代），留空即可 |
| `logging.level` | 示例配置默认 `DEBUG` 便于排障，长期运行的生产环境建议改为 `INFO` 减少日志量 |
| `logging.dir` | 日志目录（默认 `logs/`，按天轮转保留一个月） |
| `features.*` | 各功能模块开关；不需要的功能可直接关闭（如 `web_search` 默认关闭） |
| `sandbox.mode` | 外部命令是否进 Docker 沙箱执行，见「Docker 沙箱」，多用户公网部署建议确认其为 `auto` 且 daemon 可达，或显式设为 `docker` |

### multi_user 配置参考

完整字段与默认值如下（与 `config.example.json` 一致；除 `enabled` 外均实时读取、支持热重载）：

**开关与存储位置**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 多用户模式总开关。决定认证中间件与数据库初始化等启动期形态，**变更需重启**（热重载会保持启动值并告警） |
| `db_path` | `data/latexcellence.db` | SQLite（WAL）数据库路径 |
| `users_root` | `data/users/` | 用户空间根目录（其下按 `<user_id>/` 组织） |
| `staging_root` | `data/staging/` | 上传解压暂存区（后台周期清理） |

**配额与上传安全**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `default_project_quota_mb` | `500` | 每用户项目空间配额（MB），可按用户覆盖 |
| `default_derived_quota_mb` | `100` | 衍生数据（写作会话/样式会话/模板/绘图）配额（MB） |
| `max_upload_archive_mb` | `500` | 压缩包大小上限（MB），需与反代请求体上限对齐 |
| `max_upload_files` | `20000` | 解压文件数上限 |
| `max_upload_file_size_mb` | `100` | 单文件解压大小上限（MB） |
| `max_compression_ratio` | `1000` | 压缩比上限（防压缩炸弹） |
| `chunk_part_max_mb` | `8` | 分片上传单片大小上限（MB），前端切片与之对齐 |
| `upload_session_ttl_hours` | `24` | 断点续传上传会话从最后活动算起的保留时长（小时） |

**回收站与会话有效期**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `recycle_bin_days` | `7` | 回收站保留天数 |
| `session_ttl_days` | `7` | 会话默认有效期（天） |
| `remember_ttl_days` | `30` | 「记住我」会话有效期（天） |

**限流与并发治理**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `login_rate_limit_per_minute` | `5` | 登录限流（次/分钟，按「用户名+IP」与「用户名全局」双维度；计数入共享库，多实例不被副本数放大） |
| `llm_rate_limit_per_minute` | `20` | 每用户 LLM 请求滑动窗口限流（次/分钟；超限返回 429 并附 Retry-After） |
| `llm_max_concurrency_per_user` | `2` | 每用户最大并发 LLM 任务数（租约制，超限直接 429 不排队） |

**注册与管理员引导**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `registration_enabled` | `true` | 是否开放注册（仍需管理员审批）；关闭后仅管理员可建号，登录/注册页隐藏注册入口 |
| `admin_username_env` | `LATEXCELLENCE_ADMIN_USERNAME` | 首个管理员用户名的环境变量名 |
| `admin_password_env` | `LATEXCELLENCE_ADMIN_PASSWORD` | 首个管理员密码的环境变量名 |

**撤销栈与衍生数据保留**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `edit_log_max_ops` | `50` | 每（项目 × 操作者）撤销栈保留的最大操作条数 |
| `edit_log_max_op_mb` | `8` | 单组操作快照总量上限（MB），超出只记索引并标记不可撤 |
| `edit_log_ttl_hours` | `24` | 撤销栈无活动后的保留时长（小时），超时由维护循环整栈清理 |
| `writing_chat_retention_days` | `0` | 写作会话保留天数；`0` 表示不自动删除，仅计入用量统计 |

**配额预警（配合 SMTP）**

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `quota_warn_percent` | `80` | 用量达到该百分比时发送预警邮件 |
| `quota_critical_percent` | `95` | 用量达到该百分比时发送紧急预警 |
| `quota_alert_reset_percent` | `75` | 迟滞回差：低于该百分比后清除预警标记，再次越线可重新发送 |

### 管理员引导

首次启动且用户表为空时，按以下优先级创建管理员：

1. **环境变量**（推荐）：

   ```bash
   export LATEXCELLENCE_ADMIN_USERNAME="admin"
   export LATEXCELLENCE_ADMIN_PASSWORD="<强密码>"
   python main.py config.json
   ```

   环境变量名可经 `admin_username_env` / `admin_password_env` 覆盖。仅在用户表为空时消费——之后删除这两个环境变量不影响已有账号，保留也无副作用（幂等跳过）。

2. **CLI 创建**（未设置环境变量时，日志会警告并等待 CLI）：

   ```bash
   python -m backend.manage create-admin admin --password "<强密码>"
   ```

创建管理员后即可打开站点完成登录。普通用户经注册页提交后进入 `pending` 状态，需管理员在管理面板批准后方可登录。关闭开放注册可设 `multi_user.registration_enabled=false`（仅管理员可建号）。

### 使用 systemd 托管服务

裸机长期运行建议交给 systemd 管理（开机自启、崩溃拉起）：

```ini
# /etc/systemd/system/latexcellence.service
[Unit]
Description=LaTeXcellence backend (multi-user)
After=network-online.target
Wants=network-online.target
# 若启用 Docker 沙箱（sandbox.mode=auto/docker），追加：
# After=docker.service
# Requires=docker.service

[Service]
Type=simple
User=latex
Group=latex
WorkingDirectory=/opt/latexcellence
# 管理员引导变量只在首次建库时消费，之后可移除
Environment=LATEXCELLENCE_ADMIN_USERNAME=admin
Environment=LATEXCELLENCE_ADMIN_PASSWORD=CHANGE_ME_STRONG_PASSWORD
# 可选：API Key 经环境变量注入，不落盘
# Environment=LX_LLM_KEY=sk-xxxx
ExecStart=/opt/latexcellence/.venv/bin/python main.py config.json
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now latexcellence
journalctl -u latexcellence -f        # 跟踪启动日志
```

注意：

- `WorkingDirectory` 必须是仓库根（原因见「安装与启动」要点）；
- 运行账号需要对仓库下的 `data/` 与 `logs/` 有写权限。若启用沙箱，该账号还需能访问 `/var/run/docker.sock`（通常加入 `docker` 组——该组成员等同 root 级权限，见「安全风险声明」）。

### 存量数据迁移（单用户升级为多用户）

从单用户升级到多用户的服务器，可用一次性迁移工具把存量全局数据归入管理员账号：

```bash
python -m backend.manage migrate-legacy [--dry-run]
```

- 幂等可重复执行：目标已存在的文件全部跳过，不覆盖迁移后新产生的数据；
- 覆盖存量 `data/writing_chat_sessions/`、`data/style_modify_sessions/`、`data/templates/`、`data/plots/`、`data/formats/`、`data/prompts/` 等全局目录；
- 复制完成后逐文件校验并输出报告。源目录默认保留，确认无误后人工清理；
- 旧 LaTeX 项目不在自动迁移范围（config 中无工作区记录），由各用户在工作台重新上传。

### 部署验收清单

对外服务前建议完整走一遍：

```bash
curl -s http://127.0.0.1:8000/api/health         # status=ok，返回模型名
curl -s http://127.0.0.1:8000/api/config/status  # multi_user=true、has_api_key=true
```

浏览器侧：管理员登录 → 注册一个测试账号 → 管理面板批准 → 测试账号登录 → 上传一个 zip 项目 → 跑一次错字查找与一次自动编译。若启用了 SMTP，再验证一封审批通知邮件能正常送达。全部通过后再放开反代对外。

---

## 多实例部署（Docker Compose）

多进程 / 多副本部署是一等公民：跨请求共享状态（工作区绑定、逐段检查上下文、写作助手工具确认与澄清、DDG 验证码、登录/LLM 限流、沙箱与 LLM 并发租约、邮件队列）全部外置到共享 SQLite（WAL）。任意副本可服务任意请求，**无需粘性会话**。SSE 长连接天然粘在所属连接的副本上，应用层无需处理。

### 运行形态

仓库根 `docker-compose.yml` 提供单机编排：

| 服务 | 说明 |
| --- | --- |
| `app` | 后端本体（`docker/backend/Dockerfile` 构建，与 `docker/build.sh` 同口径 tag `latexcellence-backend:latest`）。healthcheck 打 `/api/health`；环境变量 `LATEXCELLENCE_LOG_STDOUT=1` 强制日志走 stdout/stderr（容器模式交 `docker logs` 收集——按天轮转的文件日志在多进程共写同一文件时轮转 rename 有竞争，不可用） |
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
./docker/build.sh                 # 见「Docker 沙箱」与 docker/README.md（支持离线 save/load）

# 3. 启动（管理员引导凭证经环境变量注入，仅首次建库时消费）
export LATEXCELLENCE_ADMIN_USERNAME="admin"
export LATEXCELLENCE_ADMIN_PASSWORD="<强密码>"
docker compose --profile proxy up -d --scale app=2   # 附带 Caddy，对外端口默认 8080
```

- 副本数按需调整：`docker compose up -d --scale app=4`（滚动扩缩容，状态在共享卷上，无需停机迁移）；
- 单副本本地试跑可复制 `docker-compose.override.example.yml` 为 `docker-compose.override.yml` 发布 `127.0.0.1:8000` 端口直连；
- 配置修改：编辑宿主 `config.json` 即可，**全部副本经热重载自动生效**。例外：`multi_user.enabled` 决定认证中间件与数据库初始化等启动期形态，变更仍需重启（热重载会保持启动值并告警）。

compose 编排支持的环境变量（均有默认值，可不设）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LATEXCELLENCE_DATA_DIR` | `./data` | 数据卷的宿主路径 |
| `LATEXCELLENCE_CONFIG_PATH` | `./config.json` | 注入容器的配置文件路径 |
| `LATEXCELLENCE_PROXY_PORT` | `8080` | Caddy 对外端口 |
| `LATEXCELLENCE_CADDYFILE` | `./docker/Caddyfile.example` | Caddy 挂载的配置文件 |
| `LATEXCELLENCE_ADMIN_USERNAME` / `LATEXCELLENCE_ADMIN_PASSWORD` | 空 | 透传给 app，首个管理员引导 |

### 不用 Compose 的单容器运行

只想跑一个容器时，等价于：

```bash
# 前提：已在仓库根完成 npm run build（镜像构建会 COPY static/）
docker build -t latexcellence-backend:latest .
docker run -d --name latexcellence \
  -p 127.0.0.1:8000:8000 \
  -e LATEXCELLENCE_LOG_STDOUT=1 \
  -e LATEXCELLENCE_ADMIN_USERNAME=admin \
  -e LATEXCELLENCE_ADMIN_PASSWORD='<强密码>' \
  -v ./config.json:/app/config.json:ro \
  -v ./data:/app/data \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  latexcellence-backend:latest
```

后端镜像基于 `python:3.12-slim`，内置 `git` 与 `unar`，以非 root 身份运行（UID/GID 默认 1000，构建时可用 `--build-arg UID/GID` 与宿主对齐，避免 bind 卷属主错乱）。国内网络可在构建时加 `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`。多副本需求出现后再迁回 Compose 形态。

### 副本扩展与反向代理

多副本下反代必须满足两点：

1. **服务名每请求重解析**：Caddy 的 `reverse_proxy app:8000` 天然如此（Docker 内嵌 DNS 返回全部健康副本，扩缩容零改动）。nginx 必须用 upstream 变量 + `resolver 127.0.0.11`（直接写死 `upstream server app:8000` 会在启动时解析一次并缓存单个 IP，扩容副本永远不被调度）。
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

- **联网搜索的浏览器回退池（Playwright，可选功能）**：arm64 Linux 上 Chromium/Firefox 可用，WebKit 的 arm64 支持随版本变动。该能力初始化失败仅降级并记 warning，不影响主流程。部署后建议跑一次写作助手联网搜索冒烟，确认实际可用性；
- **发布冒烟**：若构建/发布环境与生产架构不同，建议在目标架构上完成一次最小冒烟（启动 → 登录 → 上传 → 编译）再对外提供服务；
- Docker 沙箱镜像同样支持在 arm64 上原生构建（TeX Live `aarch64-linux` 二进制集与 pandoc 官方 arm64 发布物），多架构 buildx 构建见 [docker/README.md](../docker/README.md)。

---

## Alpine 系发行版实测记录（postmarketOS 等）

> 来自一台 postmarketOS 设备（aarch64、systemd），版本 v26.06（Alpine 3.24，Python 3.14）的全程踩坑实录。
> Debian/Ubuntu 用户无需阅读。目标是 Alpine 系或任何 **musl libc** 环境时，以下差异须逐条核对。

### 包管理器与包名对照

| 本文档（Debian/Ubuntu） | Alpine 对应 | 说明 |
| --- | --- | --- |
| apt | apk | 建议换国内镜像源 |
| python3-pip | py3-pip | `python3 -m venv` 直接可用（ensurepip 未被裁剪） |
| sqlite3 | sqlite | CLI 二进制仍叫 sqlite3 |
| unar | **无此包**；`apk add libarchive-tools` 提供 bsdtar 回退 | 后端按「unar 优先、缺失回退 bsdtar」选择解压器，两者皆缺报 `rar_extractor_missing`；zip/7z 不受影响 |
| texlive-xetex | texlive-xetex | 一致 |
| latexmk / texfot / latexindent（texlive-extra-utils） | texlive-binextra | 三者同在该子包 |
| texlive-latex-extra + texlive-lang-chinese | texlive-most + texlive-langextra | 体积大；实测该组合下 xeCJK/ctex 与中文编译完整可用，另需 `font-noto-cjk` 字体 |

### Python 依赖差异（musl libc，aarch64 实测）

- `py7zr`（**Python ≥ 3.14，即 v26.06 起必需**）：依赖 PEP 784 标准库 `compression.zstd`，而 Alpine 把 `_zstd` 扩展拆为独立包 `python3-zstd` 且 **v3.24 稳定源中没有**，需从 edge/main 单包安装（版本须与系统 Python 同为 3.14.x）。缺失时 py7zr import 即失败，7z 压缩包上传解压不可用；
- `playwright`：PyPI 无 musl-aarch64 wheel，**会导致整批 pip install 失败**。处理：剔除该行后安装其余依赖。影响仅限联网搜索的浏览器回退池（默认关闭），主流程无损；
- `matplotlib`：任何版本均无 musl-aarch64 wheel，源码编译需完整 C/C++ 工具链与若干 `-dev` 包。推荐改用发行版包（`apk add py3-matplotlib`），虚拟环境以 `python3 -m venv --system-site-packages .venv` 创建，pip 清单中剔除 matplotlib 行，系统版本满足约束时 pip 自动跳过；
- 其余二进制依赖（numpy / scipy / pandas / Pillow / lxml / uvloop 等）均有 musllinux aarch64 wheel，无需编译环境。

### 后台任务与会话清理陷阱

- 先确认 init 系统：`cat /proc/1/comm`。postmarketOS 默认 OpenRC，启用其 systemd 附加仓库后才是本文档的 systemd 形态，两者服务脚本不通用；
- systemd 发行版开启 KillUserProcesses 时，SSH 断开会**连带杀死会话内 nohup 的后台进程**（长安装脚本静默夭折且日志为空）。长耗时任务改用瞬态单元托管：

  ```bash
  doas systemd-run --unit=<名称> --collect \
      -p WorkingDirectory=/opt/latexcellence \
      -p StandardOutput=append:/tmp/<名称>.log \
      sh -c '<命令> 2>&1'
  systemctl is-active <名称>    # 轮询直至 inactive
  ```

- 脚本轮询进程存活时注意 pgrep/pkill `-f` 自匹配：模式会命中携带同一字符串的外层 shell 自身，造成死循环或误杀。用 `[x]` 方括号转义规避。

---

## Docker 沙箱

多用户部署强烈建议启用：全部外部命令（latexmk/texfot 编译、git/latexdiff、pandoc、Python 绘图、latexindent）统一经 SandboxRunner，可进一次性 Docker 容器隔离执行。由顶层 `sandbox` 配置节控制：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `auto` | `auto`：探测到 Docker daemon 即容器执行，不可达回落本地进程并告警；`docker`：强制容器执行，daemon 不可达返回 503 `sandbox_unavailable`（不静默回落）；`local`：保持本地子进程（行为与历史版本一致） |
| `image_tex` / `image_convert` / `image_plot` | `latexcellence-*:latest` | 三类沙箱镜像名（tex=编译/git/latexdiff/latexindent；convert=pandoc；plot=Python 绘图） |
| `cpus` / `memory_mb` / `pids_limit` / `tmpfs_mb` | 1.0 / 1024 / 256 / 512 | 单容器资源上限 |
| `max_concurrent_global` / `max_concurrent_per_user` | 4 / 1 | 沙箱并发池上限（每用户租约存 SQLite，超限返回 429 `sandbox_busy`；git 查询类只读子命令豁免每用户租约） |
| `startup_grace_seconds` | 10 | 容器冷启动宽限（叠加在各功能既有超时之上） |
| `warm_pool_enabled` | true | 容器预创建池（warm pool）：预先创建待命容器，命中时免去 create 直接 start，降低每次执行的启动等待。仅 docker 后端生效（auto 模式探测命中 docker 才启用；local 模式与开关无关）；关闭后每次执行即时建容器，行为与历史版本一致 |

### warm pool 运维说明（部署收口阶段三）

池容器是"已创建未启动"的一次性容器，统一带 label `latexcellence.warm_pool=1` 与副本标识（环境变量 `LATEXCELLENCE_SANDBOX_REPLICA_ID` 可显式指定，缺省按 hostname:随机后缀进程唯一生成）。只在执行某类命令后按该命令的容器形状补满（池深 = `max_concurrent_global`），不会为从未执行过的类别常驻容器。孤儿清理为每副本职责：副本启动期与运行期周期对账，按 label + 自身副本标识清退本副本遗留。副本异常退出后的残留容器由任一存活副本在超龄（默认 900s）后清退，也可手工 `docker container prune --filter "label=latexcellence.warm_pool=1"` 清理。多副本部署（`--scale app=N`）下各副本独立建池，空闲容器总量相应上浮（每副本每类别至多 `max_concurrent_global` 个 created 态容器，不占运行资源），为已知代价。冷启动收益可用 `.venv/bin/python tools/warm_pool_bench.py` 实测。

### 镜像构建

```bash
cd docker/
./build.sh                 # 构建全部镜像，tag 为 latexcellence-*:YYYY.MM + latest
./build.sh tex             # 只构建 tex 镜像
BUILDX_MULTI_ARCH=1 ./build.sh --load tex   # buildx 双架构（amd64+arm64）
```

镜像体系、离线导入导出（`docker save/load`）、升级流程与缺 TeX 宏包排查指南见 [docker/README.md](../docker/README.md)。TeX 宏包清单在 `docker/tex/Dockerfile` 的 `EXTRA_TL_PACKAGES` 构建参数中维护。运行期不装包（沙箱无网络原则），缺宏包时改清单重建镜像。

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

- **配置 `multi_user.trusted_proxies` 传递真实客户端 IP**：登录限流按「用户名 + 客户端 IP」与「用户名全局」双维度计数，注册限流、注册 IP 记录与自动审批策略同样依赖真实客户端 IP。反代之后 `request.client.host` 恒为代理地址，必须在配置中把代理地址（如 nginx 本机反代填 `127.0.0.1`，Caddy 容器部署填对应容器网段）列入 `multi_user.trusted_proxies`，后端才会信任其追加的 `X-Forwarded-For` 并解析出访客真实 IP。保持为空时后端恒用 TCP 对端地址（直连部署的安全默认，伪造头无法绕过）。上述白名单外的代理链会被忽略，多级代理需把每一跳都列入；
- **SSE 流式端点必须关闭缓冲**（`proxy_buffering off` 或 Caddy `flush_interval -1`），否则逐段检查、Agent 运行、写作助手的进度事件会被代理攒批延迟；
- **请求体上限**需 ≥ `multi_user.max_upload_archive_mb`（默认 500 MB），否则大压缩包上传会在代理层被截断（413）；
- WebSocket 不涉及（实时推送全部走 SSE）。

### 注册自动审批（IP 策略）部署

开启 `multi_user.auto_approve_enabled` 后，注册请求按 IP 策略判定「无恶意」即自动通过审批，否则转人工待审批（不会直接拒绝）。判定规则与全部配置项见 [backend-core.md](backend-core.md) 「注册自动审批要点」。部署侧需要准备：

- **真实 IP 链路**：先完成上文 `trusted_proxies` 配置。策略读到的若是代理 IP，白名单与地理规则全部失真；
- **ip2region 离线库**（推荐，国内精度高）：从 [ip2region](https://github.com/lionsoul2014/ip2region) 下载 xdb 文件，放到服务器可读路径后填入配置。**每个配置键只能指向一份 xdb 文件**：`multi_user.auto_approve_ip2region_db_path` 填 IPv4 库（`ip2region_v4.xdb`），`auto_approve_ip2region_db_path_v6` 填 IPv6 库（`ip2region_v6.xdb`，可选）。查询按注册 IP 的地址族自动路由到对应库（地址族由文件头自动识别，两键填反不影响正确性），**某地址族未配置库时该族注册回落 GeoLite2**（如只配 v4 库，IPv6 注册走 GeoLite2 判定）。两键任一配置后 ip2region 即优先生效：中国 IP 归属直接采用其结果（省/市级精度显著优于 GeoLite2，教育网等国内网段可定位到城市），非/未知中国才回落 GeoLite2。Python 读取器已随仓内嵌（官方 binding vendored），无需额外依赖；
- **GeoLite2 离线库**（国际城市数据更细）：在 [MaxMind](https://dev.maxmind.com/geoip/docs/databases/city-and-country/) 免费注册并下载 GeoLite2 City 的 `.mmdb` 文件，放到服务器可读路径（如 `data/GeoLite2-City.mmdb`），把路径填入 `multi_user.auto_approve_geo_db_path`。作为 ip2region 的回落源（非/未知中国时使用）。只配此库不配 ip2region 时行为与历史版本一致。两库文件均可定期重新下载覆盖，后端按文件 mtime 自动重载；
- **白名单口径**：固定机构场景优先用 CIDR 白名单（如校园网出口网段），无外部数据依赖且最可靠。城市/国家白名单适合成员分布较散的场景。共享出口 IP（校园/宿舍 NAT）请调大 `auto_approve_max_accounts_per_ip` 或改用网段白名单；
- **局域网部署**：内网地址在两个地理库中均无记录，会转人工。请用 CIDR 白名单表达可信内网网段。

### 公开端点

多用户模式下无需登录即可访问的端点只有：

| 端点 | 用途 |
| --- | --- |
| `GET /api/health` | 健康检查 |
| `GET /api/config/status` | 模式与功能开关（不含密钥），登录页判断模式用 |
| `POST /api/auth/login` / `POST /api/auth/register` | 登录 / 注册 |
| `POST /api/auth/forgot-password` / `POST /api/auth/reset-password` | 自助找回密码（请求令牌 / 凭令牌重置；未启用 SMTP 时入口隐藏但端点语义恒定防枚举） |

其余 `/api/*` 全部要求会话 Cookie；变更类请求额外要求 CSRF 头。`GET/POST /api/config` 仅管理员可访问。

---

## 安全风险声明（必读）

> **不启用沙箱时不提供命令隔离，适用范围为可信团队内部部署。**
> **启用 Docker 沙箱**（`sandbox.mode=auto` 命中或 `docker`）时，下列外部命令全部在无网络、只读根文件系统、资源限额的容器内执行，适用范围可相应放宽；`local` 模式（含 auto 回落）不改变上述风险结论。

多用户模式下，用户上传的 LaTeX 项目属于**不可信内容**，而以下功能会在服务器上执行外部命令：

| 功能 | 执行的外部命令 | 风险点 |
| --- | --- | --- |
| 自动编译 / git diff / 模板与样式编译 | latexmk（含 `\write18` shell-escape 可能性）、xelatex、latexdiff、git | TeX 宏包可在编译期执行任意命令 |
| 科研绘图 | Python（matplotlib 等绘图脚本） | 上传数据驱动的脚本执行 |
| 格式转换 | pandoc | 转换器漏洞或嵌入脚本 |
| 源码格式化 | latexindent.pl | Perl 执行环境 |
| rar 上传解压 | unar 或 bsdtar（convert 镜像内置） | 解析攻击者完全可控的压缩包字节流，转换器漏洞风险与 pandoc 同级 |

rar 解压已与其余外部命令同一收口：解压命令经 SandboxRunner 在 convert 镜像容器内执行（无网络、只读根文件系统、资源限额），压缩包以只读方式挂载、解压目录以可写方式挂载，解压产物落位用户空间前仍经全量安全复核。宿主侧监视任务超配额 / 超时即硬中断（杀容器 / 击杀进程组）。`local` 模式（含 auto 回落）下解压仍为宿主子进程，风险口径见上方总则。

除沙箱外，多用户模式通过以下措施收敛攻击面（**不能替代沙箱**）：

- 注册审批制 + 管理员管控（可禁用/删除账号，即时吊销会话）；
- 登录限流（每分钟 5 次，「用户名+IP」与「用户名全局」双维度，可配）、恒定时长校验防用户名枚举；
- argon2id 密码哈希、会话令牌仅存 SHA-256、HttpOnly + SameSite=Lax + HTTPS Secure Cookie、双提交 CSRF；
- 用户空间路径隔离（统一经 `path_guard` 解析，符号链接默认拒绝）、任意路径打开与磁盘浏览在多用户模式关闭；
- 上传安全管线（路径穿越 / 符号链接 / 加密包 / 分卷包 / 压缩炸弹 / 大小写冲突拦截，大小、文件数、解压总量、时长受限）；
- 项目 / 衍生数据双配额，写回超限硬失败。

**部署要求**：`sandbox.mode=local`（或 auto 回落生效）时，仅向可信成员开放（团队 / 课题组内部），配合 HTTPS 与账号审批使用。不要面向公网匿名用户提供服务。启用 Docker 沙箱（auto 命中 / docker 模式）后，外部命令已在容器内隔离，但仍建议保持账号审批与 HTTPS——沙箱隔离的是"命令执行"，不改变认证、配额与数据隔离的必要性。

**Docker socket 风险（启用沙箱时必读）**：后端驱动沙箱容器需要访问 `/var/run/docker.sock`，该权限等价于授予 root 级能力（可创建任意容器挂载宿主文件系统）。缓解措施：镜像最小化且不含凭证、socket 以只读方式挂载、宿主 Docker daemon 保持最新安全补丁。rootless Docker / Podman 作为后续演进方向（当前版本不承诺）。沙箱不可用时的降级路径见上文 `mode=local` 的风险口径。

LLM API Key 存于服务器 `config.json`（或经 `LX_LLM_KEY` 环境变量注入），所有用户的 LLM 调用共用该 Key 出口，请自行评估用量与成本归属。

---

## SMTP 邮件通知

`smtp` 配置段（设置页管理员分区或直接编辑 config.json；保存即热重载生效，无需重启）：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | false | 总开关；false 时全部通知静默跳过、登录页隐藏找回入口 |
| `host` / `port` | "" / 465 | SMTP 服务器地址与端口（host 为空同样视为未启用） |
| `encryption` | ssl | `ssl`（隐式 TLS）/ `starttls` / `none`（仅本地调试） |
| `username` / `password` | "" | 认证凭据；password 走 `***` 掩码协议（设置页回显掩码，原样保存即保留原值），也可经环境变量 `LATEXCELLENCE_SMTP_PASSWORD` 注入（见下文「环境变量参考」） |
| `from_address` / `from_name` | "" / LaTeXcellence | 发件人（from_address 留空回退 username） |
| `site_base_url` | "" | 站点对外地址，用于拼接找回链接；留空时邮件退化为相对路径提示，**建议配置**否则找回链接不可直接点击 |
| `max_send_per_minute` / `retry_attempts` | 10 / 3 | 发送限速（滚动 60s 窗口）与失败重试次数（指数退避） |
| `template_language` | zh | 邮件模板语言（zh/en） |

启用后行为：注册待审批（全部管理员，5 分钟聚合）、审批通过/拒绝/禁用、管理员重置密码（含临时密码）、自助找回令牌（每邮箱 5 分钟 1 次）、配额预警 80%/95%（迟滞，低于 75% 重置）均经 `email_outbox` 队列异步投递。

**运维要点**：

- 投递失败重试 `retry_attempts` 次后留档 failed（`email_outbox.last_error`），排查 SQL：`SELECT recipient, subject, last_error, attempts FROM email_outbox WHERE status='failed'`。sent/failed 留档保留 7 天自动清理。
- 邮件发送依赖后端进程出站网络可达 SMTP 服务器；容器部署需放行对应出站端口（465/587）。
- `password` 也可不改配置、改用环境变量 `LATEXCELLENCE_SMTP_PASSWORD` 注入（优先级更高，且永不写盘）。Compose 形态 config.json 为只读挂载，推荐此方式注入密码。
- `site_base_url` 必须是用户浏览器可达的地址（经反向代理时填对外域名），否则找回邮件中的链接无法直接使用。
- 未启用 SMTP 不影响任何主流程：注册/审批/登录/上传照常，找回入口自动隐藏，忘记密码的用户由管理员经管理面板重置。

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

- SQLite 处于 WAL 模式，**不要**直接复制 `.db` 文件了事。停机备份最简单，在线备份用 `sqlite3 data/latexcellence.db ".backup 'backup.db'"`；
- `data/users/` 可与数据库快照同窗口整体打包（tar/rsync）；
- 恢复时保持目录结构不变即可，项目目录名即 `project_id`，与库中记录一致；
- **多实例部署的停机窗口**：数据库快照与 `data/users/` 打包必须落在同一时间点，而多副本会持续写入。备份窗口内先缩容到单副本（`docker compose up -d --scale app=1`，秒级完成、不丢状态），完成快照后再扩回去。直接对活跃多副本做在线备份，可能得到项目文件与 op 记录不一致的快照；
- Compose 形态下 `logs/` 不存在（stdout 日志），日志留存交给 `docker logs` 的日志驱动（如 `local`/`json-file` 的 max-size/max-file 配置）。

---

## 环境变量参考

部署相关的全部环境变量（均可选，未设置时走配置文件默认值）：

这些变量可以逐个 export，也可以统一写进项目根目录的 `.env` 文件（模板见 `.env.example`）：配置解析时（`backend/core/config.py`）会经 `backend/core/env.py` 自动加载 `.env` 并注入 `os.environ`，服务端与离线评测工具一律生效。优先级为**真实环境变量 > `.env` 文件 > config.json**——`.env` 只补缺，不会覆盖 shell / systemd / compose 中已显式注入的值。

| 变量 | 何处使用 | 说明 |
| --- | --- | --- |
| `LX_LLM_KEY` | 所有形态 | 覆盖 `llm.api_key`，优先级最高；注入的 Key **永不写盘**（配置保存时自动清空该字段） |
| `LATEXCELLENCE_SMTP_PASSWORD` | 多用户模式 | 覆盖 `smtp.password`，优先级最高；注入的密码**永不写盘**（配置保存时自动清空该字段，与 `LX_LLM_KEY` 同一安全策略） |
| `LATEXCELLENCE_TAVILY_API_KEY` | 联网搜索启用时 | 覆盖 `features.function_calling.web_search.engines.tavily.api_key`；优先级与**永不写盘**策略同 `LX_LLM_KEY` |
| `LATEXCELLENCE_OPENALEX_API_KEY` 等学术数据源 Key | 学术检索启用时 | 覆盖 `features.function_calling.scholarly_research.providers.<provider>.api_key`，命名规则 `LATEXCELLENCE_<PROVIDER 大写>_API_KEY`，当前支持 OpenAlex / Semantic Scholar / Core / Unpaywall / PubMed（Crossref / arXiv / DataCite 不消费 API Key）；优先级与**永不写盘**策略同 `LX_LLM_KEY` |
| `LATEXCELLENCE_ADMIN_USERNAME` | 首次启动 | 引导首个管理员（仅在用户表为空时消费）；变量名可经 `multi_user.admin_username_env` 改 |
| `LATEXCELLENCE_ADMIN_PASSWORD` | 首次启动 / CLI | 同上；CLI（create-admin / reset-password）省略 `--password` 时也用它替代交互输入 |
| `LATEXCELLENCE_LOG_STDOUT` | 容器部署 | 设为 `1` 时日志只输出 stdout/stderr、不写文件（多进程共写轮转文件不安全，compose 编排已默认开启） |
| `LATEXCELLENCE_DATA_DIR` | Compose | 数据卷宿主路径（默认 `./data`） |
| `LATEXCELLENCE_CONFIG_PATH` | Compose | 配置文件挂载路径（默认 `./config.json`） |
| `LATEXCELLENCE_PROXY_PORT` | Compose | Caddy 对外端口（默认 `8080`） |
| `LATEXCELLENCE_CADDYFILE` | Compose | Caddy 挂载的 Caddyfile（默认 `./docker/Caddyfile.example`） |

注意：在设置页清空某个已由环境变量注入的敏感项（提交空值）按用户输入生效——内存值与磁盘字段会被清空，但**不会移除环境变量本身**。进程重启或 config.json 变更触发热重载后，环境变量值会重新注入并恢复生效。因此若想彻底停用某个 Key，应同时撤销对应的环境变量（如从 compose / systemd 配置中删除），而不是只在界面里清空。

---

## 升级与回滚

### 标准升级流程

```bash
# 0. 先备份（见「数据目录与备份」；多实例先缩容到单副本再做快照）

# 1. 更新代码
git pull            # 或解包新版本

# 2. 更新依赖（如有变化）
source .venv/bin/activate
pip install -r requirements.txt

# 3. 重新构建前端
cd frontend && npm install && npm run build && cd ..

# 4. 重启服务
sudo systemctl restart latexcellence      # 裸机
# 或 Compose：
# docker compose build app && docker compose up -d --scale app=<原副本数>

# 5. 自检
python -m backend.manage db-status        # 应显示 schema is up to date
curl -s http://127.0.0.1:8000/api/health
```

要点：

- **数据库迁移全自动**：启动流程幂等应用全部 schema 迁移，无需手工执行脚本。`db-status` 可查看当前 schema 版本与最新版本是否一致；
- 沙箱镜像与应用同仓库演进：TeX 宏包清单或工具链更新时需重新构建对应沙箱镜像（`./docker/build.sh`），否则沿用旧镜像继续服务；
- Compose 形态下 `config.json` 挂载自宿主且支持热重载，改配置不需要重建镜像。

### 回滚

- **代码回退前必须先用升级前的备份恢复 `data/`**：新版本启动会把数据库 schema 升级到新版，旧代码遇到新 schema 会在 `db-status` 报告不一致并持续告警；
- 恢复顺序：停服 → 还原 `data/`（含 `.db` 与 `data/users/`）→ 切回旧版本代码 → 启动；
- 沙箱镜像如被覆盖构建，按 tag 回退（`sandbox.image_*` 指回旧 tag 即可，无需重装）。

---

## 日常运维

### CLI 管理工具

所有命令支持 `--config <路径>` 指定配置文件（默认 `config.json`）。`--password` 省略时依次回退：管理员密码环境变量（`multi_user.admin_password_env`）→ 交互式输入。

```bash
python -m backend.manage create-admin <username> [--password xxx]   # 创建/提升管理员
python -m backend.manage reset-password <username> [--password xxx] # 重置密码并吊销其全部会话
python -m backend.manage list-users                                 # 列出用户（角色/状态/配额）
python -m backend.manage set-status <username> pending|active|disabled
python -m backend.manage migrate-legacy [--dry-run]                 # 存量数据迁移（幂等）
python -m backend.manage db-status                                  # 查看 schema 版本与待应用迁移
```

网页管理面板（admin 登录后可见）覆盖同样能力：待审批列表、批准/拒绝、禁用/启用、重置密码（两步式：先仅生成临时密码预览，管理员确认后才写入并吊销会话）、配额调整、删除用户（项目转交管理员、衍生数据归档一段时间后清理）。

### 日志

- 裸机：`logs/`（按天轮转，保留最近 30 天，`logging.dir` 可改）。容器 / 多实例：设置 `LATEXCELLENCE_LOG_STDOUT=1` 只输出 stdout/stderr，交 `docker logs` 收集（多进程共写轮转文件不安全，compose 编排已默认开启）；
- 排查登录问题关注 `multiuser.auth` 的限流与认证告警。上传失败关注 `invalid_archive` / `quota_exceeded` 相关记录。

### 监控建议（多实例部署）

| 观测点 | 含义与处置 |
| --- | --- |
| `database is locked` / busy 冲突日志频率 | SQLite 写锁竞争信号。两副本规模下偶发属正常；频率上升说明写吞吐逼近上限——先排查异常高频写入（如限流事件），再评估缩副本或迁移 Postgres（后续议题） |
| `email_outbox` 中 `status='failed'` 行数 | 邮件投递失败留档。`SELECT COUNT(*) FROM email_outbox WHERE status='failed'` 持续增长时检查 SMTP 配置、出站网络与 `last_error` 明细 |
| 沙箱并发池水位 | `sandbox_slots` 活跃行数 vs `max_concurrent_global`：`SELECT COUNT(*) FROM sandbox_slots WHERE expires_at > now`。频繁打满会返回 429 `sandbox_busy`，按需调大全局上限 |
| 维护选主状态 | `SELECT holder, expires_at FROM maintenance_lease`：确认只有一个活跃持有者且 `expires_at` 在滚动续期；长期无持有者说明全部副本的维护循环异常（查日志 `Multi-user maintenance cycle failed`） |
| 待定交互堆积 | `pending_interactions` 行数应在个位数量级；持续增长说明 SSE 等待侧异常退出未消费（过期清理兜底，但仍应排查） |

后台维护循环每小时执行一次：项目大小对账、衍生用量对账与配额预警检查、回收站过期清理、暂存区清理、删除用户归档清理、沙箱与 LLM 并发租约过期清理、限流事件保留期清理、自助找回令牌与邮件留档清理、逐段检查上下文与待定交互 TTL 清理、崩溃副本残留 sending 邮件行回收。多实例部署下经 `maintenance_lease` 表选主：持租约的副本执行循环体，其余副本跳过本轮。租约到期自动易主，持有人崩溃后最迟一个租约 TTL 内接管。各模块自身的 cleanup_loop（模板/绘图/样式会话 TTL 清理）保持每副本独立运行（幂等无害）。

LLM 每用户治理：多用户模式下每次 LLM 调用前在单事务内完成"滑动窗口限流校验 + 事件落账 + 并发租约获取"，请求级用户上下文由安全中间件会话验证成功后全局注入，覆盖全部 `/api` 端点。被拒绝的调用不落行、不消耗配额。429 响应附 `Retry-After` 头与秒数明细，前端三语言文案展示等待时间。单用户模式完全跳过治理（零数据库依赖）。

### 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 登录后刷新即掉线 | 多用户模式要求前端与 API **同源**（Cookie 会话）；若页面与 API 不同域名/端口，`SameSite=Lax` 会阻止跨站携带会话 Cookie。检查是否经同一域名反代访问 |
| 浏览器提示 Cookie 缺少 Secure | 反代未传递 `X-Forwarded-Proto: https`，后端无法判定 HTTPS（功能可用但安全弱化）；补上该头 |
| SSE 进度不动、最后一次性吐出 | 反代开启了响应缓冲；关闭 `proxy_buffering`（nginx）或设 `flush_interval -1`（Caddy） |
| 大压缩包上传 413 | 反代请求体上限低于 `max_upload_archive_mb`；调大 `client_max_body_size` |
| 所有用户共享同一登录限流计数 | 反代后 IP 维度退化所致（见上文），属预期行为；「用户名全局」维度仍独立计数 |
| 注册后无法登录 | 正常流程：`pending` 状态需管理员批准；管理面板 → 待审批 |
| 启动即报配置校验错误 | `multi_user` / `sandbox` / `smtp` 配置段严格校验，未知键直接报错；对照 `config.example.json` 检查拼写 |
| 生产模式页面 404，日志提示 static/ 为空 | 未构建前端，或启动时工作目录不是仓库根；先 `npm run build` 再从仓库根启动 |
| rar 上传报 `rar_extractor_missing` | local 模式（含 auto 回落）裸机未安装任何 rar 解压器：Debian 系 `apt install unar`，Alpine 系 `doas apk add libarchive-tools`（提供 bsdtar），装其一即可；Docker 沙箱模式下解压在 convert 镜像内执行、镜像已内置双工具，若报此错说明实际走了 local 回落 |
