#!/usr/bin/env bash
# 前端校验：单测（node --test）+ 可选生产构建。
#
# 本地与 CI 共用同一套命令（CI 的 web-check job 传 --install --build），
# 保证 CI 覆盖的不止是单测：断掉的 import / JSX 语法错误只有生产构建才抓得到。
#
# 用法：bash scripts/check_frontend.sh [--install] [--build]
#   --install  node_modules 缺失或 CI 场景执行 npm ci（按 package-lock.json 精确安装）
#   --build    追加 npm run build（CI 默认开启；本地默认跳过以控制耗时）
set -euo pipefail
cd "$(dirname "$0")/../frontend"

INSTALL=0
BUILD=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    --build) BUILD=1 ;;
    *) echo "未知参数: $arg（可用：--install / --build）" >&2; exit 2 ;;
  esac
done

if [ "$INSTALL" = "1" ]; then
  echo "── npm ci ────────────────────────────"
  npm ci
elif [ ! -d node_modules ]; then
  echo "缺少 frontend/node_modules：先执行 npm install，或改用 bash scripts/check_frontend.sh --install" >&2
  exit 2
fi

echo "── 前端单测 ──────────────────────────"
npm test

if [ "$BUILD" = "1" ]; then
  echo "── 前端构建 ──────────────────────────"
  npm run build
fi
