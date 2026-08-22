"""CORS 白名单 + 共享访问令牌 / 会话认证中间件。

设计说明：
- 每次请求实时读取 get_config()，支持配置热重载，不在 __init__ 中缓存配置
- CORS 白名单来自 server.allowed_origins；与请求 Host 同源的 Origin 自动放行
  （FastAPI 直接托管 static/ 的同源生产模式），其余站点一律拒绝
- 空白名单表示拒绝所有跨域来源；配置未加载时拒绝请求（fail-closed）

单用户模式（multi_user.enabled=false，默认）：
- 当 security.access_token 非空时，所有 /api/* 请求（/api/health 除外）
  必须携带 X-Latexcellence-Token 请求头或 ?token= 查询参数
  （查询参数供 <img>/<iframe>/<a download> 等无法自定义请求头的场景使用）

多用户模式（multi_user.enabled=true）：
- 跳过共享访问令牌逻辑，改为会话认证：所有 /api/* 请求（公开端点除外）
  必须携带有效的 HttpOnly 会话 Cookie（账号被禁用时即失效）
- 复用登录接口会设置该 Cookie，会话校验走 backend.core.multiuser.auth
- 变更类请求（POST/PUT/PATCH/DELETE）必须携带自定义请求头
  X-Latexcellence-CSRF，防止跨站请求伪造（配合 SameSite=Lax）
"""

import asyncio
import hmac
import json
from urllib.parse import parse_qs, urlparse

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import auth as multiuser_auth
from backend.core.multiuser import userspace

TOKEN_HEADER = "X-Latexcellence-Token"
TOKEN_QUERY = "token"
PUBLIC_PATHS = {"/api/health"}

# 多用户模式的公开端点（登录页判断模式 + 注册/登录）
MULTIUSER_PUBLIC_PATHS = frozenset(
    {"/api/health", "/api/config/status", "/api/auth/login", "/api/auth/register"}
)
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

logger = get_logger(__name__)


def _load_config():
    """实时读取配置；未加载配置时返回 None（中间件拒绝所有请求）。"""
    try:
        return get_config()
    except RuntimeError:
        return None


def _get_header(headers, name: bytes):
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _get_cookie(headers, name: str) -> str:
    """从 Cookie 头中提取指定 cookie 的值。"""
    raw = _get_header(headers, b"cookie")
    if not raw:
        return ""
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        if key == name:
            return value
    return ""


def _is_same_origin(origin: str, scope: dict) -> bool:
    """Origin 与请求 Host 同源时视为放行。

    浏览器对同源 POST/fetch 也会携带 Origin 头，FastAPI 直接托管 static/
    的生产模式下页面源就是本服务自身，必须允许，否则所有写操作会被 403。
    """
    host = _get_header(scope.get("headers") or [], b"host").strip().lower()
    if not host:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.netloc.lower() == host


def _origin_allowed(origin: str, allowed_origins: list[str], scope: dict) -> bool:
    return origin in allowed_origins or _is_same_origin(origin, scope)


def _cors_headers(origin: str, allowed: bool, credentials: bool = True):
    headers = []
    if origin and allowed:
        headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
        headers.append((b"vary", b"Origin"))
        # 前端所有请求统一携带凭据（fetch credentials: "include"，多用户
        # 模式依赖 Cookie 会话）。已回显具体 origin（非 `*`），配合凭据头
        # 是安全组合；不在白名单的来源本就被拒绝。
        if credentials:
            headers.append((b"access-control-allow-credentials", b"true"))
    return headers


async def _send_json(send, status: int, payload: dict, headers: list):
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _extract_token(headers, query_string: bytes) -> str:
    header_token = _get_header(headers, TOKEN_HEADER.lower().encode("ascii"))
    if header_token:
        return header_token.strip()
    query = parse_qs(query_string.decode("latin-1"))
    tokens = query.get(TOKEN_QUERY, [])
    return tokens[0].strip() if tokens else ""


class AccessSecurityMiddleware:
    """统一处理 CORS 预检、来源白名单、访问令牌与会话/CSRF 校验。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cfg = _load_config()
        if cfg is None:
            logger.error(
                "Config not loaded; rejecting request: %s", scope.get("path", "")
            )
            await _send_json(
                send,
                503,
                {"detail": "Server configuration not loaded"},
                [],
            )
            return

        multi_user = bool(cfg.multi_user.enabled)
        allowed_origins = list(cfg.server.allowed_origins)
        headers = scope.get("headers") or []
        origin = _get_header(headers, b"origin")
        method = scope.get("method", "GET").upper()
        origin_allowed = bool(origin) and _origin_allowed(
            origin, allowed_origins, scope
        )
        # 多用户模式使用 Cookie 会话：允许的跨源响应需携带凭据头（dev：Vite 5173）
        credentials = True

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        *_cors_headers(origin, origin_allowed, credentials),
                    ],
                }
            await send(message)

        # 预检请求：浏览器在携带自定义请求头（访问令牌 / CSRF 头）前发出 OPTIONS
        if method == "OPTIONS" and origin:
            if not origin_allowed:
                await _send_json(
                    send, 403, {"detail": "Origin not allowed by CORS policy"}, []
                )
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        *_cors_headers(origin, origin_allowed, credentials),
                        (b"access-control-allow-methods", b"*"),
                        (b"access-control-allow-headers", b"*"),
                        (b"access-control-max-age", b"86400"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        # 普通请求：来源不在白名单且非同源时拒绝（阻止任意网页访问用户本地后端）
        if origin and not origin_allowed:
            logger.warning("Rejected request from disallowed origin: %s", origin)
            await _send_json(
                send, 403, {"detail": "Origin not allowed by CORS policy"}, []
            )
            return

        path = scope.get("path", "")

        if multi_user:
            await self._check_multiuser_auth(
                scope,
                receive,
                headers,
                path,
                method,
                origin,
                origin_allowed,
                credentials,
                send,
                send_with_cors,
            )
            return

        # ── 单用户模式：共享访问令牌校验 ──
        # （/api/health 用于连接测试，保持公开）
        if (
            cfg
            and cfg.security.access_token
            and path.startswith("/api/")
            and path not in PUBLIC_PATHS
        ):
            token = _extract_token(headers, scope.get("query_string", b""))
            if not token or not hmac.compare_digest(token, cfg.security.access_token):
                logger.warning(
                    "Rejected request with missing/invalid access token: %s", path
                )
                await _send_json(
                    send,
                    401,
                    {"detail": "Access token required or invalid"},
                    _cors_headers(origin, origin_allowed, True),
                )
                return

        await self.app(scope, receive, send_with_cors)

    async def _check_multiuser_auth(
        self,
        scope,
        receive,
        headers,
        path: str,
        method: str,
        origin: str,
        origin_allowed: bool,
        credentials: bool,
        send,
        send_with_cors,
    ) -> None:
        """多用户模式：会话认证 + 变更类请求 CSRF 校验。"""
        cors = _cors_headers(origin, origin_allowed, credentials)

        if not path.startswith("/api/") or path in MULTIUSER_PUBLIC_PATHS:
            await self.app(scope, receive, send_with_cors)
            return

        session = _get_cookie(headers, multiuser_auth.SESSION_COOKIE_NAME)
        if not session:
            logger.warning("Rejected request without session: %s", path)
            await _send_json(
                send,
                401,
                {"detail": {"error": "unauthorized", "message": "Not logged in"}},
                cors,
            )
            return

        user = await asyncio.to_thread(multiuser_auth.validate_session, session)
        if user is None:
            logger.warning("Rejected request with invalid/expired session: %s", path)
            await _send_json(
                send,
                401,
                {
                    "detail": {
                        "error": "unauthorized",
                        "message": "Session expired or invalid",
                    }
                },
                cors,
            )
            return

        # v2 P1b：请求级当前用户上下文全局注入。本中间件为纯 ASGI，此处
        # 设置的 ContextVar 与下游路由/依赖/SSE 生成器同任务上下文，对全部
        # /api 端点可见——LLM 每用户治理（llm_governance）与 P6 会话类资源
        # 归属校验因此不再依赖各路由单独声明 current_user_context 依赖。
        # 单用户模式不进入本分支，上下文保持 None（治理直通）。
        userspace.set_current_user_id(user["id"])

        # 变更类请求：校验双提交 CSRF——请求头 X-Latexcellence-CSRF 必须与
        # CSRF Cookie（latexcellence_csrf）的值一致（常量时间比较）。
        # 与 SameSite=Lax 配合，抵御跨站请求伪造（含同站子域场景）。
        if method in MUTATING_METHODS:
            header_val = _get_header(
                headers, multiuser_auth.CSRF_HEADER_NAME.lower().encode("ascii")
            )
            cookie_val = _get_cookie(headers, multiuser_auth.CSRF_COOKIE_NAME)
            # 按字节常量时间比较：str 形式的 compare_digest 遇非 ASCII
            # （请求头经 latin-1 解码）会抛 TypeError → 500
            if (
                not cookie_val
                or not header_val
                or not hmac.compare_digest(
                    header_val.strip().encode("utf-8"), cookie_val.encode("utf-8")
                )
            ):
                logger.warning("Rejected mutating request with invalid CSRF: %s", path)
                await _send_json(
                    send,
                    403,
                    {
                        "detail": {
                            "error": "invalid_csrf",
                            "message": "CSRF token missing or invalid",
                        }
                    },
                    cors,
                )
                return

        await self.app(scope, receive, send_with_cors)
