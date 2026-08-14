"""CORS 白名单 + 共享访问令牌校验中间件。

设计说明：
- 每次请求实时读取 get_config()，支持配置热重载，不在 __init__ 中缓存配置
- CORS 白名单来自 server.allowed_origins；与请求 Host 同源的 Origin 自动放行
  （FastAPI 直接托管 static/ 的同源生产模式），其余站点一律拒绝
- 空白名单表示拒绝所有跨域来源；配置未加载时拒绝请求（fail-closed）
- 当 security.access_token 非空时，所有 /api/* 请求（/api/health 除外）
  必须携带 X-Latexcellence-Token 请求头或 ?token= 查询参数
  （查询参数供 <img>/<iframe>/<a download> 等无法自定义请求头的场景使用）
- 不使用 Cookie/凭据，避免 CORS 通配与凭据的兼容性问题
"""

import hmac
import json
from urllib.parse import parse_qs, urlparse

from backend.core.config import get_config
from backend.core.logger import get_logger

TOKEN_HEADER = "X-Latexcellence-Token"
TOKEN_QUERY = "token"
PUBLIC_PATHS = {"/api/health"}

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


def _cors_headers(origin: str, allowed: bool):
    headers = []
    if origin and allowed:
        headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
        headers.append((b"vary", b"Origin"))
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
    """统一处理 CORS 预检、来源白名单和访问令牌校验。"""

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

        allowed_origins = list(cfg.server.allowed_origins)
        headers = scope.get("headers") or []
        origin = _get_header(headers, b"origin")
        method = scope.get("method", "GET").upper()
        origin_allowed = bool(origin) and _origin_allowed(
            origin, allowed_origins, scope
        )

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        *_cors_headers(origin, origin_allowed),
                    ],
                }
            await send(message)

        # 预检请求：浏览器在携带自定义请求头（访问令牌）前发出 OPTIONS
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
                        *_cors_headers(origin, origin_allowed),
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

        # 访问令牌校验（/api/health 用于连接测试，保持公开）
        path = scope.get("path", "")
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
                    _cors_headers(origin, origin_allowed),
                )
                return

        await self.app(scope, receive, send_with_cors)
