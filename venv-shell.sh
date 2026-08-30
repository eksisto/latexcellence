#!/bin/bash
# LaTeXcellence — 打开一个已激活 .venv 虚拟环境的交互式 bash
#
# 用法：
#     ./venv-shell.sh
#
# 运行后进入一个新的 bash，此时 .venv 已激活（python/pip 指向虚拟环境，
# 提示符前缀显示 (venv)）。退出该 shell（exit 或 Ctrl+D）后，
# 原终端的虚拟环境状态不受影响。

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"

# 1. 检查并激活虚拟环境
if [ ! -f "$VENV/bin/activate" ]; then
    echo "[FAIL] 未找到虚拟环境: .venv" >&2
    echo "       请先创建并安装依赖：" >&2
    echo "       python3 -m venv .venv" >&2
    echo "       .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"
echo "[OK] 已激活虚拟环境: .venv"
echo "     Python: $(python --version 2>&1)"
echo "     输入 exit 或按 Ctrl+D 退出虚拟环境 shell。"
echo ""

# 2. 生成临时 rcfile：
#    - 先加载用户 ~/.bashrc，保留原有别名/函数/提示符
#    - 再强制在提示符前加 (venv) 前缀，标记当前处于虚拟环境
#    - 退出新 shell 时自动删除该临时文件
RCFILE="$(mktemp)"
cat > "$RCFILE" <<EOF
[ -f "\$HOME/.bashrc" ] && source "\$HOME/.bashrc"
PS1="(venv) \${PS1:-\\u@\\h:\\w\\\$ }"
trap 'rm -f "$RCFILE"' EXIT
EOF

# 3. 打开新的交互式 bash。venv 的环境变量（VIRTUAL_ENV / PATH）通过
#    进程环境继承给子 shell；exec 使其退出后直接回到原终端，无残留进程。
exec bash --rcfile "$RCFILE" -i
