#!/usr/bin/env bash
# 后端校验：ruff + black --check + AGENTS.md 约束扫描 + pytest。
#
# 本地与 CI 共用同一套命令（CI 的 python-check job 只额外传 --install / --skip-docker），
# 保证「本地跑过」与「CI 通过」是同一件事，不靠人工维护两份命令。
#
# 用法：bash scripts/check_backend.sh [--install] [--skip-docker]
#   --install      先安装 requirements.txt + requirements-dev.txt（CI / 首次搭建用）
#   --skip-docker  跳过 marker=docker 的真容器冒烟（pytest -m "not docker"）。
#                  CI job 无 Docker daemon；本机 daemon 与宿主机不共享 /tmp 时
#                  这些用例必然失败，属环境问题而非代码问题，见 docs/testing.md。
set -euo pipefail
cd "$(dirname "$0")/.."

INSTALL=0
SKIP_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    --skip-docker) SKIP_DOCKER=1 ;;
    *) echo "未知参数: $arg（可用：--install / --skip-docker）" >&2; exit 2 ;;
  esac
done

# 优先使用项目 .venv（ruff / black / pytest 所在）
if [ -x .venv/bin/python ]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi
PY=python
command -v python >/dev/null 2>&1 || PY=python3

if [ "$INSTALL" = "1" ]; then
  echo "── 安装依赖（requirements.txt + requirements-dev.txt）──"
  "$PY" -V
  "$PY" -m pip install -r requirements.txt -r requirements-dev.txt
fi

echo "── ruff ──────────────────────────────"
"$PY" -m ruff check backend/ main.py tests/ conftest.py scripts/

echo "── black --check ─────────────────────"
"$PY" -m black --check backend/ main.py tests/ conftest.py scripts/

echo "── AGENTS.md 约束扫描 ────────────────"
"$PY" scripts/check_conventions.py

PYTEST_ARGS=(tests/ -q)
if [ "$SKIP_DOCKER" = "1" ]; then
  PYTEST_ARGS+=(-m "not docker")
fi

echo "── pytest ────────────────────────────"
"$PY" -m pytest "${PYTEST_ARGS[@]}"
