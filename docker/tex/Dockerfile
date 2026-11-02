# ─────────────────────────────────────────────────────────────
# LaTeXcellence TeX 编译沙箱镜像（tex）
#
# 基座选择 debian:bookworm-slim（而非 ubuntu:24.04）的理由：
#   1. install-tl 自带全套二进制与依赖库，TeX Live 与宿主发行版基本解耦，
#      发行版新旧收益极小；bookworm 的 perl 版本完全满足 install-tl/tlmgr 要求；
#   2. 与 convert 镜像共用 debian 基座，宿主机只需缓存一种基础层；
#   3. slim 变体体积小、glibc 稳定。
# 基础镜像不写死架构：amd64 / arm64 均可原生构建
# （install-tl 会自动选择 x86_64-linux / aarch64-linux 二进制集）。
#
# 构建示例：
#   docker build -t latexcellence-tex:latest docker/tex
#   # 换 CTAN 国内镜像源：
#   docker build --build-arg CTAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/texlive/tlnet \
#                -t latexcellence-tex:latest docker/tex
#
# ── 版本锁定说明（重要）────────────────────────────────────────
# tlnet 源只提供"当前年度"的网络安装器（随 TeX Live 年度发布滚动更新，
# 实测 2026-08 已发放 TL2026），故 TEXLIVE_VERSION 默认值必须跟随当年版本，
# 否则仅影响 TEXDIR 安装目录名，造成目录名与实际安装版本不一致。
# 如确需锁定旧版本（复现环境）：tlnet 不再提供旧版安装器，
# 须把 CTAN_MIRROR 一并指向该年的冻结归档（各年归档索引见 https://tug.org/historic/，
# 常用路径形如 <mirror>/historic/systems/texlive/<year>/tlnet-final）：
#   docker build --build-arg TEXLIVE_VERSION=2025 \
#                --build-arg CTAN_MIRROR=https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2025/tlnet-final \
#                -t latexcellence-tex:2025 docker/tex
#
# ── 无网络使用原则 ────────────────────────────────────────────
# 沙箱容器运行期由后端 runner 强制以 --network none 启动，镜像内不预置任何联网能力；
# 缺宏包时禁止运行时 tlmgr 安装，应由部署者修改 EXTRA_TL_PACKAGES 后重建本镜像
# （排查步骤见 docker/README.md）。
# ─────────────────────────────────────────────────────────────
# 基础镜像按 digest 固定（与 convert 镜像同一快照，tag: debian:bookworm-slim）
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

# ── 版本锁定参数（均可被 --build-arg 覆盖）────────────────────
# TEXLIVE_VERSION 默认跟随 tlnet 当前年度（见头部"版本锁定说明"）：
# 只决定 TEXDIR 安装目录名；实际安装的版本由 CTAN_MIRROR 提供的安装器决定。
ARG TEXLIVE_VERSION=2026
ARG CTAN_MIRROR=https://mirror.ctan.org/systems/texlive/tlnet
# 可选 Debian apt 国内镜像源。注意：bookworm-slim 不预装 ca-certificates，
# 换 HTTPS 源会陷入"装证书需先经网拉取"死锁——请使用 HTTP 镜像
# （如 --build-arg DEBIAN_MIRROR=http://mirrors.ustc.edu.cn/debian，
#   包完整性由 apt GPG 签名兜底）；plot/convert 基础镜像自带证书不受限。
ARG DEBIAN_MIRROR=""

# 额外宏包清单：scheme-medium 不含 collection-langchinese，中文学术写作所需宏包在此显式补齐。
# 注意 tlmgr 包名与 .sty 文件名的差异：
#   subcaption.sty 由 caption 包提供（caption 打包了整个 caption bundle）；
#   amssymb.sty 由 amsfonts 包提供；故清单写 caption / amsfonts，
#   构建末尾的 kpsewhich 自检会直接验证这两个 .sty 已就位。
#   l3backend 无独立 tlmgr 包（随 latex 基础发行，scheme-medium 已内置），
#   写进清单会让 tlmgr 以"package not present"非零退出、拖垮整次构建。
# 清单按用途分组，部署者可整体覆盖 EXTRA_TL_PACKAGES 或只改某一组。
ARG TL_PKGS_CHINESE="ctex xecjk zhnumber"
ARG TL_PKGS_BIB="biber biblatex biblatex-gb7714-2015"
ARG TL_PKGS_MATH="amsmath amsfonts mathtools unicode-math pdfx siunitx cleveref"
ARG TL_PKGS_LAYOUT="xcolor framed enumitem titlesec fancyhdr tocloft natbib geometry setspace microtype etoolbox environ trimspaces hyperref"
ARG TL_PKGS_TABLES_CODE="pgf booktabs multirow threeparttable caption algorithm2e algorithms listings minted fvextra tcolorbox"
# 科研绘图（plot_gen）：TikZ 片段包装编译依赖 standalone 文档类，
# PGFPlots 图表依赖 pgfplots（scheme-medium 均不含，缺任一项都会让
# compile_tikz 报 `File ... not found` 并浪费 Agent 重试轮次）。
ARG TL_PKGS_PLOT="standalone pgfplots"
# 西文字体白名单：TeX Gyre Termes 是 Times New Roman 的度量兼容自由替代
# （Times New Roman/SimSun 本体为商业授权，不可打进镜像；CJK 端由
# fonts-noto-cjk 与 scheme-medium 自带的 Fandol 覆盖）。template_gen 的
# prompt 按同一份"字体白名单"把用户需求映射到这些字体，两侧须同步维护。
ARG TL_PKGS_FONTS="tex-gyre"
ARG TL_PKGS_TOOLS="latexmk texfot latexdiff"
ARG EXTRA_TL_PACKAGES="${TL_PKGS_CHINESE} ${TL_PKGS_BIB} ${TL_PKGS_MATH} ${TL_PKGS_LAYOUT} ${TL_PKGS_TABLES_CODE} ${TL_PKGS_PLOT} ${TL_PKGS_FONTS} ${TL_PKGS_TOOLS}"

LABEL org.opencontainers.image.title="LaTeXcellence TeX Sandbox" \
      org.opencontainers.image.source="https://github.com/eksisto/latexcellence.git" \
      org.opencontainers.image.description="LaTeX 编译沙箱：TeX Live ${TEXLIVE_VERSION} scheme-medium + 中文宏包 + latexmk/texfot/latexdiff/latexindent/git"

# ── 第 1 层：系统依赖 ─────────────────────────────────────────
# 可选 apt 换源（DEBIAN_MIRROR 非空时替换 deb.debian.org，# 分隔符避免与 URL 中 / 冲突）
RUN set -eux; \
    if [ -n "$DEBIAN_MIRROR" ]; then \
        find /etc/apt -type f \( -name "*.list" -o -name "*.sources" \) -exec sed -i \
            -e "s#http://deb\\.debian\\.org/debian#${DEBIAN_MIRROR}#g" \
            -e "s#https://deb\\.debian\\.org/debian#${DEBIAN_MIRROR}#g" \
            {} +; \
    fi
# perl        ：install-tl / tlmgr / latexindent 运行时
# python3     ：latexmk 钩子；python3-pygments 提供 pygmentize（minted 需要）
# fontconfig +
# fonts-noto-cjk：XeLaTeX/LuaLaTeX 经 fontconfig 查找系统中文字体
# libyaml-tiny-perl +
# libfile-homedir-perl：latexindent.pl 的 Perl 模块依赖
# git         ：git_diff 模块的 git 子命令与 latexdiff 底层调用
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates wget perl \
        python3-minimal python3-pygments \
        fontconfig fonts-noto-cjk \
        libyaml-tiny-perl libfile-homedir-perl \
        git; \
    rm -rf /var/lib/apt/lists/*

# ── 第 2 层：profile 方式 unattended 安装 TeX Live scheme-medium ──
# 不装 doc/src 文件以控制镜像体积；安装完成后 tlmgr path add 将全部可执行文件
# 符号链接到 /usr/local/bin（bin 目录名随架构变化，用 glob 定位，不写死）。
RUN set -eux; \
    wget -qO /tmp/install-tl-unx.tar.gz "$CTAN_MIRROR/install-tl-unx.tar.gz"; \
    mkdir /tmp/install-tl; \
    tar -xzf /tmp/install-tl-unx.tar.gz -C /tmp/install-tl --strip-components=1; \
    printf '%s\n' \
        "selected_scheme scheme-medium" \
        "TEXDIR /usr/local/texlive/$TEXLIVE_VERSION" \
        "TEXMFLOCAL /usr/local/texlive/texmf-local" \
        "TEXMFSYSCONFIG /usr/local/texlive/$TEXLIVE_VERSION/texmf-config" \
        "TEXMFSYSVAR /usr/local/texlive/$TEXLIVE_VERSION/texmf-var" \
        "instopt_adjustpath 0" \
        "instopt_adjustrepo 1" \
        "tlpdbopt_autobackup 0" \
        "tlpdbopt_install_docfiles 0" \
        "tlpdbopt_install_srcfiles 0" \
        > /tmp/profile.txt; \
    /tmp/install-tl/install-tl --profile=/tmp/profile.txt --repository="$CTAN_MIRROR"; \
    "$(ls /usr/local/texlive/"$TEXLIVE_VERSION"/bin/*/tlmgr)" path add; \
    rm -rf /tmp/install-tl /tmp/install-tl-unx.tar.gz /tmp/profile.txt

# ── 第 3 层：补齐参数化宏包清单 + 构建期自检 ────────────────────
# 自检失败（缺可执行文件或 .sty 未就位）即中止构建并指名缺失项，
# 把"缺包"暴露在构建期而非编译期；逐项检查以输出非空为准，兼容各版本 kpsewhich。
RUN set -eux; \
    tlmgr option repository "$CTAN_MIRROR"; \
    tlmgr install $EXTRA_TL_PACKAGES; \
    for b in xelatex lualatex pdflatex latexmk texfot latexdiff latexindent biber git pygmentize synctex; do \
        command -v "$b" >/dev/null || { echo "FATAL: 镜像缺少可执行文件 $b"; exit 1; }; \
    done; \
    for f in ctex.sty xeCJK.sty zhnumber.sty biblatex.sty gb7714-2015.bbx \
             pgf.sty pgfplots.sty standalone.cls booktabs.sty multirow.sty threeparttable.sty \
             caption.sty subcaption.sty algorithm2e.sty algorithm.sty \
             listings.sty minted.sty fvextra.sty tcolorbox.sty \
             hyperref.sty cleveref.sty siunitx.sty pdfx.sty \
             texgyretermes-regular.otf \
             amsmath.sty amssymb.sty mathtools.sty unicode-math.sty \
             xcolor.sty framed.sty enumitem.sty titlesec.sty fancyhdr.sty \
             tocloft.sty natbib.sty geometry.sty setspace.sty microtype.sty \
             etoolbox.sty environ.sty trimspaces.sty; do \
        p="$(kpsewhich "$f")" || true; \
        [ -n "$p" ] || { echo "FATAL: 宏包清单未覆盖 ${f}（请核对 EXTRA_TL_PACKAGES）"; exit 1; }; \
    done

# ── 第 4 层：fontconfig 纳管 TeX Live 自带字体 + 字体白名单自检 ───
# scheme-medium 自带 Latin Modern / TeX Gyre / Fandol 等自由字体，但其 OTF
# 位于 texmf-dist/fonts 下，默认不在 fontconfig 扫描路径内；fontspec 的
# \setmainfont 按字体名查找只认 fontconfig 可见的字体，否则报
# `The font "X" cannot be found`（run trace 6aacdd03 连 Latin Modern 都
# 找不到的根因）。通过 /etc/fonts/local.conf 纳管后，XeLaTeX/LuaLaTeX
# 可按名加载这些字体；构建期自检白名单核心成员确实可见。
RUN set -eux; \
    printf '%s\n' \
        '<?xml version="1.0"?>' \
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">' \
        '<fontconfig>' \
        '  <!-- TeX Live 自带字体（Latin Modern / TeX Gyre / Fandol 等） -->' \
        '  <dir>/usr/local/texlive/texmf-dist/fonts/opentype</dir>' \
        '  <dir>/usr/local/texlive/texmf-dist/fonts/truetype</dir>' \
        '</fontconfig>' \
        > /etc/fonts/local.conf; \
    fc-cache -f >/dev/null; \
    fc-match -f '%{family}\n' "TeX Gyre Termes" | grep -q "TeX Gyre Termes"; \
    fc-list | grep -qi "noto sans cjk"

# 工作目录：容器以后端 runner 传入的 uid:gid 运行（docker run --user <uid>:<gid>），
# 与宿主后端账号保持一致以保证产物属主正确；镜像内不绑定固定工作用户。
RUN mkdir -p /workspace && chmod 1777 /workspace
WORKDIR /workspace

# 运行身份为任意 --user 注入的 uid 时 HOME 可能不存在，统一指向可写的 /tmp
# （运行期 /tmp 为 tmpfs；fontconfig/biber 等的用户级缓存均落在此处）
ENV HOME=/tmp

# 编译命令由后端 sandbox runner 传入（如 latexmk -xelatex ...），故不设默认 CMD。
