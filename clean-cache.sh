#!/bin/bash
# LateXcellence Cache Cleaner

ROOT="$(cd "$(dirname "$0")" && pwd)"
cleaned=0
failed=0

echo ""
echo "========================================"
echo "  LateXcellence Cache Cleaner"
echo "========================================"
echo ""

clean_dir() {
    local dir="$1"
    if [ -d "$dir" ]; then
        if rm -rf "$dir" 2>/dev/null; then
            echo "  [OK]   ${dir#$ROOT/}"
            ((cleaned++))
        else
            echo "  [FAIL] ${dir#$ROOT/}"
            ((failed++))
        fi
    fi
}

clean_pattern() {
    local desc="$1"
    local pattern="$2"
    local dir="$3"
    found=0
    while IFS= read -r -d '' f; do
        if rm -rf "$f" 2>/dev/null; then
            echo "  [OK]   ${f#$ROOT/}"
            ((cleaned++))
        else
            echo "  [FAIL] ${f#$ROOT/}"
            ((failed++))
        fi
        found=1
    done < <(find "$dir" -path "*/node_modules" -prune -o -name "$pattern" -print0 2>/dev/null)
    if [ "$found" -eq 0 ]; then
        echo "  --     ${desc} (不存在，跳过)"
    fi
}

echo "[1/5] 清理 __pycache__ ..."
clean_pattern "__pycache__" "__pycache__" "$ROOT"

echo "[2/5] 清理 *.pyc ..."
clean_pattern "*.pyc" "*.pyc" "$ROOT"

echo "[3/5] 清理 node_modules/.cache ..."
cache="$ROOT/frontend/node_modules/.cache"
if [ -d "$cache" ]; then
    if rm -rf "$cache" 2>/dev/null; then
        echo "  [OK]   frontend/node_modules/.cache"
        ((cleaned++))
    else
        echo "  [FAIL] frontend/node_modules/.cache"
        ((failed++))
    fi
else
    echo "  --     node_modules/.cache (不存在，跳过)"
fi

echo "[4/5] 清理 .pytest_cache ..."
clean_pattern ".pytest_cache" ".pytest_cache" "$ROOT"

echo "[5/5] 清理 .pytest_tmp ..."
# pytest 的 basetemp（pytest.ini 里 --basetemp=.pytest_tmp），会话异常中断后
# 可能残留为普通权限无法删除的目录（Windows ACL 损坏），导致后续 pytest 每次
# 会话结束清理报 PermissionError。这里补清理；删除失败时提示改用 clean-cache.ps1
# （其会自动提权）。Linux 下该目录为用户所有，普通 rm -rf 即可。
pytest_tmp="$ROOT/.pytest_tmp"
if [ -d "$pytest_tmp" ]; then
    if rm -rf "$pytest_tmp" 2>/dev/null; then
        echo "  [OK]   .pytest_tmp"
        ((cleaned++))
    else
        echo "  [FAIL] .pytest_tmp（ACL 受限）"
        echo "        请改用 clean-cache.ps1 以管理员权限清理，或手动执行："
        echo "        powershell -NoProfile -Command \"Remove-Item -LiteralPath '$pytest_tmp' -Recurse -Force\""
        ((failed++))
    fi
else
    echo "  --     .pytest_tmp (不存在，跳过)"
fi

echo ""
echo "========================================"
echo "  清理结果"
echo "========================================"
echo ""
echo "  已清理: ${cleaned} 项"
echo "  失败:   ${failed} 项"
echo ""
read -n 1 -s -r -p "按任意键退出..."
echo ""
