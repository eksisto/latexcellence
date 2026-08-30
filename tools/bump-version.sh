#!/bin/bash
# LaTeXcellence 版本号一键升级
# 同步修改 main.py（FastAPI app + /api/health 两处）与前端三语 i18n（欢迎页展示的版本），
# 默认以 chore(release): 升级版本为 vX.Y.Z 自动提交（--no-commit 跳过提交）。
# 说明：frontend/package.json 的 "version": "0.1.0" 是 npm 包占位，不随发布升级。
# 用法：tools/bump-version.sh <X.Y.Z | vX.Y.Z | major | minor | patch> [--no-commit]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN_PY="$ROOT/main.py"
I18N_DIR="$ROOT/frontend/src/i18n/locales"
LOCALES=(zh zh-TW en)
COMMIT_MSG_PREFIX="chore(release): 升级版本为"

info() { echo "[..] $*"; }
ok()   { echo "[OK] $*"; }
warn() { echo "[!!] $*"; }
die()  { warn "$*"; exit 1; }
step() { echo ""; echo "======== $* ========"; }

usage() {
    cat <<'EOF'
用法：tools/bump-version.sh <新版本> [--no-commit]

  <新版本>     完整版本号（2.2.0 或 v2.2.0），或相对升级关键字 major / minor / patch
  --no-commit  只修改文件，不自动提交（默认会以 chore(release): 升级版本为 vX.Y.Z 提交）

示例：
  tools/bump-version.sh patch     # 2.1.0 -> 2.1.1
  tools/bump-version.sh minor     # 2.1.0 -> 2.2.0
  tools/bump-version.sh 3.0.0     # 显式指定版本号
EOF
}

# sed 模式中的点号转义
esc() { printf '%s' "$1" | sed 's/\./\\./g'; }

# 从 main.py 读取当前版本（FastAPI 参数式与 /api/health 的 JSON 式两种写法都要覆盖），多处出现时必须一致
read_current() {
    local vers n
    vers="$(grep -hoE 'version="[0-9]+\.[0-9]+\.[0-9]+"|"version": "[0-9]+\.[0-9]+\.[0-9]+"' "$MAIN_PY" \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | sort -u)" \
        || die "main.py 中未找到版本号（version=\"X.Y.Z\" / \"version\": \"X.Y.Z\"）"
    [ -n "$vers" ] || die "main.py 中未找到版本号（version=\"X.Y.Z\" / \"version\": \"X.Y.Z\"）"
    n="$(printf '%s\n' "$vers" | wc -l)"
    [ "$n" -eq 1 ] || die "main.py 中存在多个不同版本号，请先手动对齐：$vers"
    printf '%s' "$vers"
}

# 按关键字相对升级：major/minor/patch
bump() {
    local major="$1" minor="$2" patch="$3" mode="$4"
    case "$mode" in
        major) major=$((major + 1)); minor=0; patch=0 ;;
        minor) minor=$((minor + 1)); patch=0 ;;
        patch) patch=$((patch + 1)) ;;
    esac
    printf '%s.%s.%s' "$major" "$minor" "$patch"
}

NO_COMMIT=false
TARGET=""
for arg in "$@"; do
    case "$arg" in
        --no-commit) NO_COMMIT=true ;;
        -h | --help) usage; exit 0 ;;
        -*) die "未知参数：$arg（--help 查看用法）" ;;
        *) TARGET="$arg" ;;
    esac
done
[ -n "$TARGET" ] || { usage; exit 1; }

step "解析目标版本"
CURRENT="$(read_current)"
case "$TARGET" in
    major | minor | patch)
        IFS='.' read -r MA MIN PA <<< "$CURRENT"
        NEW="$(bump "$MA" "$MIN" "$PA" "$TARGET")"
        ;;
    v*)
        NEW="${TARGET#v}"
        ;;
    *)
        NEW="$TARGET"
        ;;
esac
[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "版本号格式非法：$TARGET（应为 X.Y.Z / vX.Y.Z / major / minor / patch）"
[ "$NEW" != "$CURRENT" ] || die "新版本与当前版本相同（$CURRENT），无事可做"

CURRENT_ESC="$(esc "$CURRENT")"
NEW_ESC="$(esc "$NEW")"

FILES=(main.py frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/zh-TW.json frontend/src/i18n/locales/en.json)

# 预检（全部通过后才动手改文件）：文件齐全；提交模式下要求 4 个版本文件本身无未提交改动
[ -f "$MAIN_PY" ] || die "未找到 $MAIN_PY"
for locale in "${LOCALES[@]}"; do
    [ -f "$I18N_DIR/$locale.json" ] || die "未找到 $I18N_DIR/$locale.json"
done
if ! $NO_COMMIT; then
    [ -z "$(git -C "$ROOT" status --porcelain -- "${FILES[@]}")" ] \
        || die "版本文件（main.py / i18n）已有未提交改动，拒绝在其上覆盖；请先提交，或改用 --no-commit"
fi

RESTORE_HINT="可用 git checkout -- main.py frontend/src/i18n/locales/ 还原"
step "升级 $CURRENT -> $NEW"

sed -i -e "s/version=\"${CURRENT_ESC}\"/version=\"${NEW}\"/g" \
    -e "s/\"version\": \"${CURRENT_ESC}\"/\"version\": \"${NEW}\"/g" "$MAIN_PY"
n_new="$(grep -Ec "version=\"${NEW_ESC}\"|\"version\": \"${NEW_ESC}\"" "$MAIN_PY" || true)"
n_old="$(grep -Ec "version=\"${CURRENT_ESC}\"|\"version\": \"${CURRENT_ESC}\"" "$MAIN_PY" || true)"
[ "$n_new" -eq 2 ] && [ "$n_old" -eq 0 ] \
    || die "main.py 替换后校验失败（新版本出现 $n_new 次、旧版本残留 $n_old 次，应为 2 和 0）；$RESTORE_HINT"
ok "main.py（app + /api/health 两处）"

for locale in "${LOCALES[@]}"; do
    file="$I18N_DIR/$locale.json"
    sed -i "s/\"version\": \"v${CURRENT_ESC}\"/\"version\": \"v${NEW}\"/" "$file"
    n_new="$(grep -c "\"version\": \"v${NEW}\"" "$file" || true)"
    n_old="$(grep -c "\"version\": \"v${CURRENT_ESC}\"" "$file" || true)"
    [ "$n_new" -eq 1 ] && [ "$n_old" -eq 0 ] \
        || die "$locale.json 替换后校验失败（新版本出现 $n_new 次、旧版本残留 $n_old 次，应为 1 和 0）；$RESTORE_HINT"
    ok "i18n/$locale.json"
done

if $NO_COMMIT; then
    step "完成（未提交）"
    echo "已修改以下文件，提交信息建议：${COMMIT_MSG_PREFIX} v${NEW}"
    printf '  %s\n' "${FILES[@]}"
    exit 0
fi

step "提交"
git -C "$ROOT" add -- "${FILES[@]}"
git -C "$ROOT" commit -m "${COMMIT_MSG_PREFIX} v${NEW}" \
    || die "git commit 失败（pre-commit 拦截？），改动已保留在工作区"
ok "$(git -C "$ROOT" log -1 --oneline)"

step "完成"
echo "版本号已升级：${CURRENT} -> ${NEW}"
echo "别忘了按 maintenance/02-发布一次更新.md 走发布流程。"
