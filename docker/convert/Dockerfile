# ─────────────────────────────────────────────────────────────
# LateXcellence pandoc 转换沙箱镜像（convert）
# 基础镜像不写死架构：amd64 / arm64 均可原生构建
#
# 构建示例：
#   docker build -t latexcellence-convert:latest docker/convert
#   # 锁定版本 + sha256 校验（推荐，值取自 GitHub release 校验信息）：
#   docker build --build-arg PANDOC_VERSION=3.6.4 \
#                --build-arg PANDOC_SHA256=<64位十六进制> \
#                -t latexcellence-convert:latest docker/convert
# ─────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

# 版本锁定；升级时确认官方 release 存在对应架构发布物即可覆盖
ARG PANDOC_VERSION=3.6.4
# 可选 sha256 校验：传入 64 位十六进制摘要后启用校验，留空跳过。
# 摘要来源：https://github.com/jgm/pandoc/releases/tag/<PANDOC_VERSION> 页面的
# pandoc-<ver>-linux-<arch>.tar.gz 对应条目（各架构摘要不同）。
ARG PANDOC_SHA256=""

# TARGETARCH 为 BuildKit 内置平台参数（amd64 / arm64），需在 stage 内重新声明方可用于 RUN。
# pandoc 官方发布物命名与 Docker TARGETARCH 是恒等映射：
#   linux/amd64 → TARGETARCH=amd64 → pandoc-<ver>-linux-amd64.tar.gz
#   linux/arm64 → TARGETARCH=arm64 → pandoc-<ver>-linux-arm64.tar.gz
# （pandoc 自 2.10 起提供 arm64 官方发布物，命名沿用 linux-arm64。）
ARG TARGETARCH

# 可选 Debian apt 国内镜像源（构建时如 --build-arg DEBIAN_MIRROR=https://mirrors.ustc.edu.cn/debian）
ARG DEBIAN_MIRROR=""

LABEL org.opencontainers.image.title="LateXcellence Convert Sandbox" \
      org.opencontainers.image.source="https://github.com/eksisto/latexcellence.git" \
      org.opencontainers.image.description="pandoc 转换沙箱：锁定版本 ${PANDOC_VERSION} 官方发布物"

# pandoc 官方 tarball 结构：pandoc-<ver>/{bin/pandoc, share/man/man1/pandoc.1.gz}
# 二进制仅动态链接 glibc 系基础库（libgmp 等已由 slim 基础层自带），无需额外 apt 运行库；
# wget/ca-certificates 仅构建期下载使用，同层安装、同层清理列表。
# 可选 apt 换源（DEBIAN_MIRROR 非空时替换 deb.debian.org，# 分隔符避免与 URL 中 / 冲突）
RUN set -eux; \
    if [ -n "$DEBIAN_MIRROR" ]; then \
        find /etc/apt -type f \( -name "*.list" -o -name "*.sources" \) -exec sed -i \
            -e "s#http://deb\\.debian\\.org/debian#${DEBIAN_MIRROR}#g" \
            -e "s#https://deb\\.debian\\.org/debian#${DEBIAN_MIRROR}#g" \
            {} +; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates wget; \
    rm -rf /var/lib/apt/lists/*; \
    wget -qO /tmp/pandoc.tar.gz \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    if [ -n "$PANDOC_SHA256" ]; then \
        echo "${PANDOC_SHA256}  /tmp/pandoc.tar.gz" | sha256sum -c -; \
    fi; \
    mkdir /tmp/pandoc; \
    tar -xzf /tmp/pandoc.tar.gz -C /tmp/pandoc --strip-components=1; \
    install -m 0755 /tmp/pandoc/bin/pandoc /usr/local/bin/pandoc; \
    mkdir -p /usr/local/share/man/man1; \
    install -m 0644 /tmp/pandoc/share/man/man1/pandoc.1.gz /usr/local/share/man/man1/ 2>/dev/null || true; \
    pandoc --version >/dev/null; \
    rm -rf /tmp/pandoc /tmp/pandoc.tar.gz

# 不设 ENTRYPOINT：后端 SandboxRunner 统一以完整 argv（"pandoc -f ... -t ..."）
# 驱动容器，与 local 后端共用同一 ExecSpec 契约；镜像内只保证 pandoc 在 PATH。
# 若设 ENTRYPOINT ["pandoc"]，Docker 会把 runner 传入的 argv 追加为其参数，
# 实际执行变成 `pandoc pandoc -f ...`（第二个 pandoc 被当作输入文件名）而必然失败。
