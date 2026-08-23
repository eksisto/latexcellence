#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# LateXcellence 多实例 Compose 冒烟（多用户 v2 P7）
#
# 流程：起 replicas=2 + Caddy 反代 → /api/health → 注册 → 管理员审批 →
# 登录 → 分片上传（各分片经反代轮询分发，可命中不同副本）→ 打开项目 →
# apply-change → undo → 下载校验内容回滚。
#
# 用法：
#   ./tools/compose-smoke.sh                 # 缺省：跳过 TeX 编译步骤
#   ./tools/compose-smoke.sh --with-tex      # 附加 TeX 编译冒烟
#                                            # （需已构建 latexcellence-tex 镜像，
#                                            #  见 docker/build.sh；大镜像构建耗时较长）
#
# 说明：
#   - 全程使用仓库根 docker-compose.yml，数据/配置经 LATEXCELLENCE_DATA_DIR /
#     LATEXCELLENCE_CONFIG_PATH 指到临时目录，**不触碰**宿主 ./data 与 config.json；
#   - 幂等：启动前清理同名 compose 项目残留，退出（含失败）时 down 并删除临时目录；
#   - 镜像：app 镜像就地构建（docker compose build app）；tex/convert/plot
#     沙箱镜像非本脚本职责（--with-tex 只检查镜像是否已存在）。
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WITH_TEX=0
for arg in "$@"; do
    case "$arg" in
        --with-tex) WITH_TEX=1 ;;
        -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 2 ;;
    esac
done

COMPOSE_PROJECT="latexcellence-smoke"
PROXY_PORT="${SMOKE_PORT:-8080}"
ADMIN_USER="smoke-admin"
ADMIN_PASS="SmokeAdmin!2026"
SMOKE_USER="smoke-user"
SMOKE_PASS="SmokeUser!2026"
WORKSPACE_ID="smoke-ws-0001"
PROJECT_NAME="smoke-project"

command -v docker >/dev/null 2>&1 || { echo "错误：未检测到 docker CLI"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "错误：未检测到 docker compose 插件"; exit 1; }
docker info >/dev/null 2>&1 || { echo "错误：Docker daemon 不可达"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "错误：需要宿主 python3 构造上传载荷/解析响应"; exit 1; }

# 临时工作目录：默认放仓库内（bind mount 源路径必须对 Docker daemon 可见；
# 平台临时目录在沙箱/部分环境下 daemon 不可见，会导致 config.json 被建成目录）。
# 可用 SMOKE_TMPDIR 覆盖。
SMOKE_BASE="${SMOKE_TMPDIR:-$REPO_ROOT/.compose-smoke-tmp}"
mkdir -p "$SMOKE_BASE"
TMPDIR_SMOKE="$(mktemp -d "$SMOKE_BASE/run.XXXXXX")"
DATA_DIR="$TMPDIR_SMOKE/data"
CONFIG_PATH="$TMPDIR_SMOKE/config.json"
JAR_ADMIN="$TMPDIR_SMOKE/admin.jar"
JAR_USER="$TMPDIR_SMOKE/user.jar"
BASE_URL="http://127.0.0.1:${PROXY_PORT}"

cleanup() {
    local code=$?
    docker compose -p "$COMPOSE_PROJECT" --profile proxy down \
        --remove-orphans --volumes >/dev/null 2>&1 || true
    rm -rf "$TMPDIR_SMOKE"
    exit $code
}
trap cleanup EXIT INT TERM

step() { printf '\n══ %s ══\n' "$*"; }
fail() { echo "✗ $*" >&2; exit 1; }

json_get() { python3 -c "
import json, sys
data = json.load(sys.stdin)
cur = data
for key in '$1'.split('.'):
    if not key: continue
    cur = cur[key]
print(cur if not isinstance(cur, (dict, list)) else json.dumps(cur))
"; }

step "准备隔离环境（$TMPDIR_SMOKE）"
mkdir -p "$DATA_DIR"
cat > "$CONFIG_PATH" <<EOF
{
  "server": { "host": "0.0.0.0", "port": 8000, "dev_mode": false },
  "llm": { "api_key": "", "base_url": "https://smoke.invalid/v1", "model": "smoke" },
  "logging": { "level": "INFO", "dir": "logs/" },
  "multi_user": {
    "enabled": true,
    "db_path": "/app/data/latexcellence.db",
    "users_root": "/app/data/users/",
    "staging_root": "/app/data/staging/",
    "registration_enabled": true,
    "chunk_part_max_mb": 1,
    "max_upload_archive_mb": 50
  },
  "smtp": { "enabled": false, "host": "" },
  "sandbox": { "mode": "auto" }
}
EOF

export LATEXCELLENCE_DATA_DIR="$DATA_DIR"
export LATEXCELLENCE_CONFIG_PATH="$CONFIG_PATH"
export LATEXCELLENCE_ADMIN_USERNAME="$ADMIN_USER"
export LATEXCELLENCE_ADMIN_PASSWORD="$ADMIN_PASS"
export LATEXCELLENCE_PROXY_PORT="$PROXY_PORT"

step "构造分片上传载荷（zip ≈1.6MB，随机内容保证 2 个分片）"
python3 - "$TMPDIR_SMOKE" <<'PYEOF'
import io, os, sys, zipfile

out = sys.argv[1]
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("main.tex",
                "\\documentclass{article}\n\\begin{document}\n"
                "Hello compose smoke.\n\\end{document}\n")
    zf.writestr("payload.bin", os.urandom(1_600_000))  # 不可压缩，撑出第二个分片
data = buf.getvalue()
open(os.path.join(out, "upload.zip"), "wb").write(data)
import hashlib
print(len(data))
print(hashlib.md5(data).hexdigest())
open(os.path.join(out, "upload.zip.md5"), "w").write(hashlib.md5(data).hexdigest() + "\n")
PYEOF
ZIP_PATH="$TMPDIR_SMOKE/upload.zip"
ZIP_MD5="$(cat "$TMPDIR_SMOKE/upload.zip.md5")"
ZIP_SIZE="$(stat -c%s "$ZIP_PATH")"
CHUNK=1048576  # chunk_part_max_mb=1
N_CHUNKS=$(( (ZIP_SIZE + CHUNK - 1) / CHUNK ))
echo "upload.zip: ${ZIP_SIZE}B, md5=${ZIP_MD5}, chunks=${N_CHUNKS}"

step "清理残留 compose 项目（幂等）"
docker compose -p "$COMPOSE_PROJECT" --profile proxy down \
    --remove-orphans --volumes >/dev/null 2>&1 || true

step "构建 app 镜像"
docker compose -p "$COMPOSE_PROJECT" build app

if [ "$WITH_TEX" -eq 1 ]; then
    step "--with-tex：检查 TeX 沙箱镜像"
    if ! docker image inspect latexcellence-tex:latest >/dev/null 2>&1; then
        fail "未找到 latexcellence-tex:latest 镜像——请先执行 ./docker/build.sh tex（TeX 大镜像构建耗时较长）"
    fi
    echo "latexcellence-tex:latest 已就绪"
fi

step "启动 replicas=2 + Caddy 反代"
# 不指定服务名，让 proxy profile 的 caddy 一并启动（--wait 等待健康）
docker compose -p "$COMPOSE_PROJECT" --profile proxy up -d --scale app=2 --wait || \
    fail "compose up 失败"
replicas="$(docker compose -p "$COMPOSE_PROJECT" --profile proxy ps --status running app | tail -n +2 | wc -l)"
[ "$replicas" -ge 2 ] || fail "运行中的 app 副本数 = $replicas（期望 ≥2）"
echo "app 运行副本数：$replicas"

step "等待 /api/health（经 Caddy 负载均衡打到任一副本）"
# 优先走宿主发布端口；部分环境（沙箱/防火墙）loopback 发布端口不可达，
# 自动回落 Caddy 容器 IP 直连——同样经反代与 DNS 轮询分发到各副本。
CADDY_CID="$(docker compose -p "$COMPOSE_PROJECT" --profile proxy ps -q caddy | head -1)"
BASE_URL="http://127.0.0.1:${PROXY_PORT}"
if [ -n "$CADDY_CID" ]; then
    CADDY_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CADDY_CID")"
fi
health_ok=""
for i in $(seq 1 60); do
    body="$(curl -sf "$BASE_URL/api/health" || true)"
    if [ -n "$body" ]; then break; fi
    if [ -n "${CADDY_IP:-}" ] && [ "$i" = "5" ]; then
        echo "宿主发布端口不可达，回落 Caddy 容器 IP：$CADDY_IP"
        BASE_URL="http://${CADDY_IP}"
    fi
    sleep 1
done
body="$(curl -sf "$BASE_URL/api/health" || true)"
[ -n "$body" ] || {
    echo "caddy 日志：" >&2
    docker compose -p "$COMPOSE_PROJECT" --profile proxy logs caddy >&2 || true
    fail "健康检查超时（BASE_URL=$BASE_URL）"
}
health_ok="$body"
echo "health: $health_ok (via $BASE_URL)"

csrf_header() {
    # 双提交 CSRF：请求头取 Cookie jar 中 latexcellence_csrf 的值。
    # 注意调用处不加引号——无 token 时输出为空，需整体从参数中消失
    # （加引号会产生一个空字符串参数，curl 报 blank argument）。
    local jar="$1"
    [ -f "$jar" ] || return 0
    local token
    token="$(awk '$6 == "latexcellence_csrf" { print $7 }' "$jar" | tail -1)"
    if [ -n "$token" ]; then
        printf -- "-H X-Latexcellence-CSRF:%s" "$token"
    fi
}

api() {
    # api <jar> <method> <path> [curl extra args...] —— HTTP ≥400 即视为失败。
    # -g 关闭 URL globbing：请求体中的 `{}` 会被 curl 误认为 URL 序列语法
    local jar="$1" method="$2" path="$3"; shift 3
    curl -gsSf -b "$jar" -c "$jar" -X "$method" $(csrf_header "$jar") \
        -H 'Content-Type: application/json' "$@" "$BASE_URL$path"
}

step "注册普通用户 → 管理员登录并审批 → 用户登录"
api "$JAR_USER" POST /api/auth/register \
    -d "{\"username\":\"${SMOKE_USER}\",\"password\":\"${SMOKE_PASS}\"}" >/dev/null \
    || fail "注册失败"
api "$JAR_ADMIN" POST /api/auth/login \
    -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASS}\"}" >/dev/null \
    || fail "管理员登录失败"
pending_user_id="$(api "$JAR_ADMIN" GET /api/admin/users | json_get users | python3 -c "
import json, sys
users = json.load(sys.stdin)
print(next(u['id'] for u in users if u['username'] == '${SMOKE_USER}'))
")" || fail "待审批列表中未找到新用户"
api "$JAR_ADMIN" POST "/api/admin/users/${pending_user_id}/approve" -d '{}' >/dev/null \
    || fail "审批失败"
api "$JAR_USER" POST /api/auth/login \
    -d "{\"username\":\"${SMOKE_USER}\",\"password\":\"${SMOKE_PASS}\"}" >/dev/null \
    || fail "用户登录失败"
echo "注册 → 审批 → 登录链路 OK"

step "分片上传（init → parts → complete）"
session_json="$(api "$JAR_USER" POST /api/projects/upload-sessions -d @- <<EOF
{
  "name": "${PROJECT_NAME}",
  "kind": "zip",
  "total_bytes": ${ZIP_SIZE},
  "files": [
    {"path": "upload.zip", "size": ${ZIP_SIZE}, "md5": "${ZIP_MD5}", "chunks": ${N_CHUNKS}}
  ]
}
EOF
)" || fail "创建上传会话失败"
SID="$(echo "$session_json" | json_get session_id)"
[ -n "$SID" ] && [ "$SID" != "null" ] || fail "未取得会话 id：$session_json"

part_bytes_start=0
for c in $(seq 0 $((N_CHUNKS - 1))); do
    end=$(( part_bytes_start + CHUNK ))
    [ "$end" -gt "$ZIP_SIZE" ] && end=$ZIP_SIZE
    dd if="$ZIP_PATH" bs=1 skip="$part_bytes_start" count="$((end - part_bytes_start))" \
        of="$TMPDIR_SMOKE/part.bin" status=none
    pmd5="$(md5sum "$TMPDIR_SMOKE/part.bin" | cut -d' ' -f1)"
    api "$JAR_USER" PUT "/api/projects/upload-sessions/${SID}/parts/0/${c}" \
        -H 'Content-Type: application/octet-stream' \
        -H "X-Part-MD5: ${pmd5}" \
        --data-binary "@$TMPDIR_SMOKE/part.bin" >/dev/null \
        || fail "分片 $c 上传失败"
    echo "  分片 $c/$((N_CHUNKS - 1)) OK（经反代分发，可能命中任一副本）"
    part_bytes_start=$end
done
project_json="$(api "$JAR_USER" POST "/api/projects/upload-sessions/${SID}/complete" -d '{}')" \
    || fail "complete 失败"
PROJECT_ID="$(echo "$project_json" | json_get project.project_id)"
[ -n "$PROJECT_ID" ] && [ "$PROJECT_ID" != "null" ] || fail "未取得 project_id：$project_json"
echo "项目已创建：$PROJECT_ID"

step "打开项目 → apply-change → undo → 下载校验"
api "$JAR_USER" POST "/api/project/open?workspace_id=${WORKSPACE_ID}" \
    -d "{\"project_id\": \"${PROJECT_ID}\"}" >/dev/null || fail "打开项目失败"

api "$JAR_USER" POST "/api/project/apply-change?workspace_id=${WORKSPACE_ID}" \
    -d '{"file_path": "main.tex", "paragraph_index": 0, "new_text": "Edited by smoke."}' \
    >/dev/null || fail "apply-change 失败"
echo "apply-change OK"

api "$JAR_USER" POST "/api/project/undo?workspace_id=${WORKSPACE_ID}" \
    -d '{"file_path": "main.tex"}' >/dev/null \
    || fail "undo 失败"
echo "undo OK"

download_md5_check="$(curl -sSf -b "$JAR_USER" "$BASE_URL/api/projects/${PROJECT_ID}/download" \
    | python3 -c "
import io, sys, zipfile
zf = zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))
text = zf.read('main.tex').decode('utf-8')
assert 'Hello compose smoke.' in text, 'undo 未生效：' + text
print('rollback-verified')
")" || fail "下载校验失败"
[ "$download_md5_check" = "rollback-verified" ] || fail "内容回滚断言失败"
echo "下载并校验：undo 后 main.tex 已恢复原文"

if [ "$WITH_TEX" -eq 1 ]; then
    step "TeX 编译冒烟（--with-tex）"
    compile_out="$(api "$JAR_USER" POST "/api/compile/run?workspace_id=${WORKSPACE_ID}" \
        -d '{"engine": "auto"}')" || fail "编译请求失败"
    echo "$compile_out" | head -c 300; echo
fi

step "✔ 冒烟通过：replicas=2 多实例链路全绿（health/审批/分片上传/undo/下载）"
