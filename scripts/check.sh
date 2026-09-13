#!/usr/bin/env bash
# 统一验证入口：改完代码跑这一条即可覆盖全部机器检查。
#
# 后端 / 前端分别由 check_backend.sh、check_frontend.sh 实现，CI 的
# python-check / web-check job 也直接调用这两个脚本。
#
# 用法：bash scripts/check.sh [--install] [--build] [--skip-docker]
#   --install      先安装依赖（后端 pip + 前端 npm ci），CI / 首次搭建用
#   --build        追加前端生产构建（CI 默认开启；本地按需）
#   --skip-docker  跳过 marker=docker 的真容器冒烟（CI / daemon 与宿主机不共享 /tmp 时用）
# 未装依赖时默认直接复用已有 .venv / frontend/node_modules。
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_ARGS=()
FRONTEND_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --install)
      BACKEND_ARGS+=(--install)
      FRONTEND_ARGS+=(--install)
      ;;
    --build) FRONTEND_ARGS+=(--build) ;;
    --skip-docker) BACKEND_ARGS+=(--skip-docker) ;;
    *) echo "未知参数: $arg（可用：--install / --build / --skip-docker）" >&2; exit 2 ;;
  esac
done

# ${arr[@]+"${arr[@]}"} 是空数组在 set -u 下对老版本 bash（含 macOS 自带 3.2）的安全展开
bash scripts/check_backend.sh ${BACKEND_ARGS[@]+"${BACKEND_ARGS[@]}"}
bash scripts/check_frontend.sh ${FRONTEND_ARGS[@]+"${FRONTEND_ARGS[@]}"}

echo "✓ 全部检查通过"
