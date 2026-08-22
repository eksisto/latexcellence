#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LateXcellence 沙箱镜像统一构建脚本
#
# 首次使用先赋可执行权限：chmod +x docker/build.sh
#
# 用法：
#   ./docker/build.sh                          # 构建 backend/tex/convert/plot 全部镜像
#   ./docker/build.sh tex plot                 # 只构建指定目标（可多个）
#   BUILDX_MULTI_ARCH=1 ./build.sh             # buildx 双架构构建（linux/amd64,linux/arm64）
#   BUILDX_MULTI_ARCH=1 ./build.sh --push      # buildx 双架构构建并推送 manifest
#   BUILDX_MULTI_ARCH=1 ./build.sh --load      # buildx 单架构构建并加载到本地 daemon
#
# 环境变量：
#   BUILDX_MULTI_ARCH=1     启用 buildx 多架构路径（默认 0，走普通 docker build）
#   BUILDX_PLATFORMS=<list> 覆盖平台列表（默认 linux/amd64,linux/arm64）
#   PIP_INDEX_URL=<url>     自动透传为 backend / plot 镜像的同名 build-arg（pip 镜像源）
#
# tag 规则：latexcellence-{backend,tex,convert,plot}:YYYY.MM 与 :latest 双标签；
#          config.json 的 sandbox.image_* 引用该名称。
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_TAG="$(date +%Y.%m)"
ALL_TARGETS=(backend tex convert plot)
PLATFORMS="${BUILDX_PLATFORMS:-linux/amd64,linux/arm64}"
BUILDX_MULTI_ARCH="${BUILDX_MULTI_ARCH:-0}"

usage() {
    sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

targets=()
output_flag=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --push)
            output_flag="--push"
            ;;
        --load)
            output_flag="--load"
            ;;
        -*)
            echo "错误：未知选项 $1（可用目标：${ALL_TARGETS[*]}）" >&2
            exit 2
            ;;
        *)
            targets+=("$1")
            ;;
    esac
    shift
done

# 无参数 → 构建全部；有参数 → 校验目标合法性
if [ "${#targets[@]}" -eq 0 ]; then
    targets=("${ALL_TARGETS[@]}")
fi
for t in "${targets[@]}"; do
    valid=0
    for a in "${ALL_TARGETS[@]}"; do
        [ "$t" = "$a" ] && valid=1
    done
    if [ "$valid" -ne 1 ] || [ ! -f "$SCRIPT_DIR/$t/Dockerfile" ]; then
        echo "错误：未知构建目标 '$t'（可用目标：${ALL_TARGETS[*]}）" >&2
        exit 2
    fi
done

command -v docker >/dev/null 2>&1 || {
    echo "错误：未检测到 docker CLI，请先安装 Docker" >&2
    exit 1
}

# --push / --load 只在 buildx 路径下有意义：显式给出时自动切换到多架构模式
if [ -n "$output_flag" ]; then
    BUILDX_MULTI_ARCH=1
fi

if [ "$BUILDX_MULTI_ARCH" = "1" ]; then
    docker buildx version >/dev/null 2>&1 || {
        echo "错误：BUILDX_MULTI_ARCH=1 需要 docker buildx 插件" >&2
        exit 1
    }
    if [ "$output_flag" = "--load" ] && [[ "$PLATFORMS" == *,* ]]; then
        echo "错误：--load 不支持多平台 manifest；请改用单平台，例如：" >&2
        echo "  BUILDX_MULTI_ARCH=1 BUILDX_PLATFORMS=linux/arm64 ./build.sh --load" >&2
        exit 2
    fi
fi

declare -a summary=()
failed=0

build_one() {
    local target="$1"
    local dir="$SCRIPT_DIR/$target"
    shift
    local -a extra_args=()
    # backend / plot 支持 PIP_INDEX_URL 构建参数透传
    case "$target" in
        backend|plot)
            [ -n "${PIP_INDEX_URL:-}" ] && extra_args+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
            ;;
    esac
    local -a cmd=(docker)
    if [ "$BUILDX_MULTI_ARCH" = "1" ]; then
        cmd+=(buildx build --platform "$PLATFORMS")
        [ -n "$output_flag" ] || echo "提示：buildx 多平台结果不会自动加载到本地 daemon，建议追加 --push 或 --load"
        cmd+=(${output_flag:+"$output_flag"})
    else
        cmd+=(build)
    fi
    "${cmd[@]}" \
        -t "latexcellence-$target:$VERSION_TAG" \
        -t "latexcellence-$target:latest" \
        ${extra_args[@]+"${extra_args[@]}"} \
        "$dir"
}

for t in "${targets[@]}"; do
    echo ""
    echo "==> 构建 latexcellence-$t ($VERSION_TAG) ..."
    if build_one "$t"; then
        summary+=("OK    latexcellence-$t:$VERSION_TAG  latexcellence-$t:latest")
    else
        summary+=("FAIL  latexcellence-$t:$VERSION_TAG  （见上方日志）")
        failed=1
    fi
done

echo ""
echo "══════════════ 构建结果摘要 ══════════════"
printf '  %s\n' "${summary[@]}"
echo "══════════════════════════════════════════"

if [ "$failed" -ne 0 ]; then
    echo "存在失败的镜像构建，请检查上方日志。" >&2
    exit 1
fi
echo "全部完成。config.json 的 sandbox.image_* 指向上述镜像名即可启用。"
