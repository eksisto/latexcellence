"""搜索结果 URL 的安全校验、规范化与来源分类。"""

import asyncio
import ipaddress
import socket
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "spm",
}
_TRACKING_PREFIXES = ("utm_",)


def _parse_obscured_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """识别浏览器可接受的整数、八进制或十六进制 IPv4 写法。"""

    def parse_part(part: str) -> int:
        if part.casefold().startswith("0x"):
            return int(part, 16)
        if len(part) > 1 and part.startswith("0"):
            return int(part, 8)
        return int(part, 10)

    try:
        if "." not in host:
            return ipaddress.IPv4Address(parse_part(host))
        parts = host.split(".")
        if not 1 <= len(parts) <= 4:
            return None
        values = [parse_part(part) for part in parts]
        if any(value < 0 for value in values):
            return None
        address = 0
        for index, value in enumerate(values):
            remaining = len(values) - index - 1
            width = 8 if remaining else 8 * (4 - index)
            if value >= 1 << width:
                return None
            address = (address << width) | value
        return ipaddress.IPv4Address(address)
    except ValueError:
        return None


def canonicalize_url(url: str) -> str | None:
    """返回可去重的公开 HTTP(S) URL；无效或明显私网 URL 返回 ``None``。"""
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None

        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return None
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            address = _parse_obscured_ipv4(host)
        if address is not None and not address.is_global:
            return None

        try:
            ascii_host = host.encode("idna").decode("ascii")
            port = parsed.port
        except (UnicodeError, ValueError):
            return None
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        )
        netloc = ascii_host if port is None or default_port else f"{ascii_host}:{port}"
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if path != "/":
            path = path.rstrip("/")
        query_items = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_PARAMS
            and not key.casefold().startswith(_TRACKING_PREFIXES)
        ]
        query_items.sort(key=lambda item: (item[0].casefold(), item[1]))
        return urlunsplit(
            (
                parsed.scheme.lower(),
                netloc,
                path,
                urlencode(query_items, doseq=True),
                "",
            )
        )
    except (TypeError, ValueError):
        return None


def url_domain(url: str) -> str:
    """提取规范化主机名；无效 URL 返回空字符串。"""
    canonical = canonicalize_url(url)
    return urlsplit(canonical).hostname or "" if canonical else ""


def classify_source(url: str) -> str:
    """用稳定、可解释的规则粗分搜索来源类型。"""
    domain = url_domain(url)
    path = urlsplit(url).path.casefold() if domain else ""
    if domain.endswith(".gov") or ".gov." in domain:
        return "government"
    if domain.endswith(".edu") or ".edu." in domain:
        return "academic"
    if domain in {"arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov"}:
        return "academic"
    if domain.startswith("docs.") or "/docs" in path or "/documentation" in path:
        return "official_docs"
    if domain:
        return "web"
    return "unknown"


def ip_is_blocked(ip_text: str) -> bool:
    """判断 IP 是否属于不应抓取的私网/环回/保留地址。"""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True  # 非 IP 文本由调用方按 hostname 解析处理
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip_is_blocked(str(ip.ipv4_mapped))
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def host_is_blocked(host: str) -> bool:
    """
    判断 host 是否指向内网/环回/保留地址。

    hostname 会做 DNS 解析并逐个检查解析结果，任一地址落在私网段即拦截；
    解析失败按不安全处理（fail closed）。
    """
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return ip_is_blocked(host)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except Exception:
        return True
    return any(ip_is_blocked(info[4][0]) for info in infos)


async def url_is_safe(url: str) -> bool:
    """只允许公网 HTTPS URL，含 DNS 验证，防 SSRF。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            return False
        host = parsed.hostname or ""
        return bool(host) and not await host_is_blocked(host)
    except Exception:
        return False
