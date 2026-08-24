# ─────────────────────────────────────────────────────────────
# LateXcellence 科研绘图 Python 沙箱镜像（plot）
# 基础镜像不写死架构：amd64 / arm64 均可原生构建
#
# 构建示例：
#   docker build -t latexcellence-plot:latest docker/plot
#   # 可选国内 pip 源：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#                -t latexcellence-plot:latest docker/plot
#   # 可选 Debian apt 国内镜像源（deb822 与传统 list 均覆盖）：
#   docker build --build-arg DEBIAN_MIRROR=https://mirrors.ustc.edu.cn/debian \
#                -t latexcellence-plot:latest docker/plot
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

ARG PIP_INDEX_URL=""
ARG DEBIAN_MIRROR=""

LABEL org.opencontainers.image.title="LateXcellence Plot Sandbox" \
      org.opencontainers.image.source="https://github.com/eksisto/latexcellence.git" \
      org.opencontainers.image.description="科研绘图沙箱：numpy/pandas/scipy/matplotlib/seaborn 科学计算栈 + 中文字体"

ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

# ── 第 0 层：可选 apt 换源 ────────────────────────────────────
# DEBIAN_MIRROR 非空时替换 /etc/apt 内全部源文件里的 deb.debian.org（含 debian-security，
# 其路径前缀同为 .../debian-security，替换规则天然兼容）。# 分隔符避免与 URL 中 / 冲突。
RUN set -eux; \
    if [ -n "$DEBIAN_MIRROR" ]; then \
        find /etc/apt -type f \( -name "*.list" -o -name "*.sources" \) -exec sed -i \
            -e "s#http://deb\\.debian\\.org/debian#${DEBIAN_MIRROR}#g" \
            -e "s#https://deb\\.debian\\.org/debian#${DEBIAN_MIRROR}#g" \
            {} +; \
    fi

# ── 第 1 层：字体 + 科学计算栈 ────────────────────────────────
# fonts-noto-cjk / fonts-dejavu-core：中文与西文图表渲染，避免缺字方框；
# pip 包为仓库 requirements.txt 的对齐子集（版本下限保持一致，
# 升级依赖时须同步修改 requirements.txt 与此处）。
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        fonts-noto-cjk fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*; \
    pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
        "numpy>=2.0.0" \
        "pandas>=2.0.0" \
        "scipy>=1.14.0" \
        "matplotlib>=3.9.0" \
        "seaborn>=0.13.0" \
        "colorcet>=3.1.0" \
        "openpyxl>=3.1.0" \
        "xlrd>=2.0.1"

# 预建 matplotlib 配置目录（MPLCONFIGDIR 指向运行期 tmpfs），避免任意 uid 运行时初始化失败
RUN mkdir -p /workspace /tmp/mpl && chmod 1777 /workspace /tmp/mpl
WORKDIR /workspace

# 运行身份由后端 runner 以 --user <uid>:<gid> 注入（与宿主后端账号一致），
# 镜像内不绑定固定用户；绘图脚本由 runner 写入挂载的会话目录后在此执行。
