# LateXcellence Docker 沙箱镜像体系

本目录提供多用户模式 v2 的 Docker 沙箱镜像交付物：后端把全部外部命令执行（LaTeX 编译、pandoc 转换、Python 绘图、git/latexdiff、latexindent）迁入容器，按用途拆分镜像、仓库内锁定版本、构建参数化。沙箱执行模型与安全约束见 `docs/multi-user-v2-plan.md` §5。

## 镜像用途对照表

| 镜像（构建目录） | 默认镜像名 | 用途 | 关键工具链 |
| --- | --- | --- | --- |
| `backend/` | `latexcellence-backend` | 应用本体（FastAPI 后端 + 前端静态产物），上传解压用 unar | python:3.12-slim + requirements.txt + unar + git |
| `tex/` | `latexcellence-tex` | LaTeX 编译与 TeX 侧工具沙箱 | TeX Live（install-tl scheme-medium）+ latexmk / texfot / latexdiff / latexindent / biber + git |
| `convert/` | `latexcellence-convert` | 格式转换（format_converter 模块） | pandoc 官方发布物（版本锁定） |
| `plot/` | `latexcellence-plot` | 科研绘图 Python 沙箱（plot_gen 模块） | numpy / pandas / scipy / matplotlib / seaborn / colorcet / openpyxl / xlrd + Noto CJK 字体 |

镜像名通过 `config.json` 的 `sandbox.image_*` 配置项引用；`backend` 镜像本身由部署形态（compose 等）引用，不占用 `sandbox.image_*`。

## 部署硬性前提：同路径挂载视图（重要）

沙箱采用"同绝对路径 bind mount"契约（宿主路径即容器内路径），要求**后端进程与 Docker daemon 看到同一文件系统视图**：

- ✅ 支持：Linux 原生部署（后端直接跑在宿主上）。
- ❌ 不支持：后端自身容器化且把数据卷挂在非宿主同名路径（如 compose 的 `/app/data`）——daemon 视角不存在该路径，容器创建必然失败。运行期会在启动预检（bind-mount 实测）中发现并视同不可达：`sandbox.mode=auto` 回落本地执行并告警，`mode=docker` 快速失败（503）。此形态下请改用 `mode=local`，或让后端容器以 host 路径前缀一致的方式挂载数据卷。

## 构建方法

首次使用先赋可执行权限：

```bash
chmod +x docker/build.sh
```

常用构建命令：

```bash
cd <仓库根目录>

./docker/build.sh                    # 构建全部镜像
./docker/build.sh tex plot           # 只构建指定目标

# 国内网络环境：透传 pip 镜像源（对 backend / plot 生效）
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./docker/build.sh backend plot

# TeX Live 换国内 CTAN 源（版本默认跟随 tlnet 当年发布）
docker build \
  --build-arg CTAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/texlive/tlnet \
  -t latexcellence-tex:latest docker/tex

# 锁定旧版 TeX Live：tlnet 只提供当年安装器，须改用该年冻结归档
# （各年归档索引见 https://tug.org/historic/，常用路径形如
#   <mirror>/historic/systems/texlive/<year>/tlnet-final）
docker build \
  --build-arg TEXLIVE_VERSION=2025 \
  --build-arg CTAN_MIRROR=https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2025/tlnet-final \
  -t latexcellence-tex:2025 docker/tex
```

tag 规则：每个目标同时打 `latexcellence-<target>:YYYY.MM` 与 `latexcellence-<target>:latest` 双标签（`YYYY.MM` 取构建当日）。生产建议引用 `YYYY.MM` 标签，`latest` 仅作本地开发便利。

### 多架构构建（buildx）

所有 Dockerfile 不写死基础镜像架构，Linux amd64/arm64 均可原生构建；跨架构产物用 buildx 产出 manifest list：

```bash
# 双架构构建并推送（需已 docker login 目标仓库）
BUILDX_MULTI_ARCH=1 ./docker/build.sh --push

# 自定义平台列表 / 推送目标（在 tag 上加 registry 前缀时自行调整 config.json 引用）
BUILDX_MULTI_ARCH=1 BUILDX_PLATFORMS=linux/amd64,linux/arm64 ./docker/build.sh --push

# 单架构加载到本地 daemon（--load 不支持多平台 manifest）
BUILDX_MULTI_ARCH=1 BUILDX_PLATFORMS=linux/arm64 ./docker/build.sh --load
```

说明：

- `BUILDX_MULTI_ARCH=1` 未带 `--push/--load` 时仅产出构建缓存，不会加载到本地 daemon；
- tex 镜像的 install-tl 在 arm64 上自动选择 `aarch64-linux` 二进制集；convert 镜像按 `TARGETARCH` 自动选择 pandoc 的 `linux-amd64` / `linux-arm64` 发布物；
- 无 buildx 插件或未启用该变量时，脚本回落普通 `docker build`（当前宿主架构）。

## 离线导入导出（air-gapped 部署）

在有外网的构建机上构建后导出：

```bash
# 导出（可多个镜像打入同一 tar；建议同时带上 YYYY.MM 与 latest 标签）
docker save -o latexcellence-sandbox-images.tar \
  latexcellence-backend:2026.02 latexcellence-backend:latest \
  latexcellence-tex:2026.02 latexcellence-tex:latest \
  latexcellence-convert:2026.02 latexcellence-convert:latest \
  latexcellence-plot:2026.02 latexcellence-plot:latest
gzip latexcellence-sandbox-images.tar   # 可选压缩

# 在离线目标机导入
gunzip latexcellence-sandbox-images.tar.gz
docker load -i latexcellence-sandbox-images.tar
```

导入后确认 `config.json` 中 `sandbox.image_*` 指向的镜像名与标签已存在（`docker images | grep latexcellence`）。

## 升级流程

以升级 pandoc 为例（其余同理）：

1. **改版本参数**：编辑对应 Dockerfile 顶部的 `ARG` 默认值（如 convert 的 `PANDOC_VERSION`、tex 的 `TEXLIVE_VERSION` / `CTAN_MIRROR` / `EXTRA_TL_PACKAGES`）；pip 类依赖同步修改 `requirements.txt`（backend）或 plot 镜像内的包清单。
2. **重建镜像**：`./docker/build.sh convert`（或全部重建）。tex / convert 镜像构建末尾内置自检，缺工具或缺 `.sty` 会直接让构建失败。
3. **更新配置**：修改 `config.json` 的 `sandbox.image_*` 指向新 tag（如 `latexcellence-convert:2026.03`）；保持镜像名不变、只滚动标签亦可。
4. **重启生效**：重启后端进程/容器使新配置加载（配置热重载覆盖运行参数，但镜像引用变更建议随重启完成）。旧镜像保留一段时间以便回滚，确认稳定后再 `docker rmi` 清理。

## 缺 TeX 宏包排查指南

沙箱运行期无网络（见下节），缺宏包只能通过"改清单 → 重建镜像"解决：

1. **从编译日志定位缺失文件**。典型报错形如：
   ```text
   ! LaTeX Error: File `threeparttable.sty' not found.
   ```
   或 BibTeX 资源类：
   ```text
   (biblatex)                The following files could not be found:
   (biblatex)                gb7714-2015.bbx
   ```
2. **找到对应 CTAN 包名**。注意 `.sty` 文件名 ≠ tlmgr 包名。在有网络的机器（如构建机宿主，非沙箱内）上查询：
   ```bash
   tlmgr search --global --file "threeparttable.sty"
   tlmgr search --global --file "gb7714-2015.bbx"
   ```
   也可在 [CTAN](https://ctan.org/search) 页面按文件名检索。常见易错对照：
   | 日志中的文件名 | tlmgr 包名 |
   | --- | --- |
   | `subcaption.sty` | `caption`（caption bundle 整体打包） |
   | `amssymb.sty` | `amsfonts` |
   | `algorithm.sty` / `algorithmic.sty` | `algorithms` |
   | `gb7714-2015.bbx/.cbx` | `biblatex-gb7714-2015` |
3. **加入宏包清单**。编辑 `docker/tex/Dockerfile`，将包名追加到 `EXTRA_TL_PACKAGES`（可追加到语义匹配的分组 `ARG`，或直接覆盖整个 `EXTRA_TL_PACKAGES`）。
4. **重建 tex 镜像**：`./docker/build.sh tex`。构建末尾的 `kpsewhich` 自检会验证清单内关键样式文件确实就位；随后按上文升级流程滚动 tag 并重启。
5. **验证**：重新触发一次此前失败的编译任务确认恢复。

> 反向提醒：若某宏包不再需要，可从清单移除以缩小镜像体积；但移除前请确认没有项目依赖它。

## 安全说明

- **运行期无网络**：沙箱容器由后端 runner 强制以 `--network none` 启动；镜像本身也不预置联网凭据或代理配置。TeX 编译无法访问 `\write18` 网络资源（shell-escape 白名单由后端编译参数控制），minted 所需的 pygmentize 已随镜像内置，无需联网。
- **只读根文件系统**：runner 以 `--read-only` 启动沙箱，可写空间仅限注入的 tmpfs（`/tmp` 等，容量由 `tmpfs_mb` 控制）与挂载的项目目录；tex/plot 镜像因此把 `HOME` 指向 `/tmp`。
- **能力裁剪**：runner 注入 drop capabilities 与 `no-new-privileges`，镜像不以特权模式运行为前提。
- **非 root 运行**：沙箱容器以后端传入的 `--user <uid>:<gid>` 运行（与宿主后端账号一致，保证产物属主正确）；backend 镜像内置 `latexcellence` 用户（UID/GID 可用 build-arg 对齐）。
- **无内置凭证**：`config.json`（含 API Key、令牌等）与 `data/` 一律运行时挂载，不打进任何镜像；请勿在镜像构建参数中传入敏感信息。

## 支持矩阵

| 平台 | 支持状态 | 说明 |
| --- | --- | --- |
| Linux 原生（amd64） | ✅ 一等支持 | 推荐 Debian 12+ / Ubuntu 22.04+ 宿主机 |
| Linux 原生（arm64） | ✅ 一等支持 | install-tl 自动选 aarch64 二进制集；pandoc 有官方 arm64 发布物；两种架构均应通过编译/绘图冒烟验收 |
| Windows | ⚠️ 建议 WSL2 | 在 WSL2 内按 Linux 原生方式部署；不建议直接在 Windows 容器模式运行 |
| macOS（Docker Desktop） | ❌ 沙箱不支持，回落 local 模式 | Docker Desktop 的路径翻译导致无法同绝对路径 bind mount（template_gen 解析日志路径依赖容器内外路径一致）；macOS 开发环境下 `sandbox.mode` 回落 local 执行，功能不受影响 |

## 其他注意事项

- backend 镜像只安装 Playwright 的 Python 包、不含浏览器内核：web_search 的浏览器回退池在容器内不可用，抓取自动走 HTTP 路径；确有需要可在部署文档基础上自行扩展镜像安装内核。
- plot 镜像 pip 包是仓库 `requirements.txt` 的对齐子集（数值计算与绘图部分），两侧版本下限需同步维护。
- convert 镜像不设 ENTRYPOINT：后端 SandboxRunner 以完整 argv 驱动容器。手工调试时命令完整给出即可：`docker run --rm -v "$PWD:/w" -w /w latexcellence-convert:<tag> pandoc -f md -t html in.md`。
- tex 镜像体积主要来自 scheme-medium + 中文字体/宏包，属预期；不要为缩小体积改用 scheme-basic，中文支持会整体缺失。
