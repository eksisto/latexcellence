"""真实客户端 IP 解析（多用户注册自动审批的 IP 前置）。

问题背景：
    直接暴露部署时 request.client.host 即访客真实 IP；但生产推荐
    HTTPS 反向代理（Caddy/Nginx）部署下，request.client.host 是代理
    地址，真实访客 IP 在 X-Forwarded-For 头里。

解析规则（与业界标准一致，防伪造）：
    - 请求来源地址不在 trusted_proxies 白名单内 → 它本身就是真实客户端
      IP，忽略任何 X-Forwarded-For（防止直连客户端伪造头绕过策略）；
    - 来源地址是可信代理 → 从右往左遍历 X-Forwarded-For 链：最右侧是
      最近一跳追加的地址。逐个跳过仍属于可信代理的条目，第一个不可信
      地址即客户端真实 IP（代理每跳都会把上一跳地址追加到链尾，攻击者
      自带的伪造头只会落在链首，必然先于可信代理条目被处理）；
    - 链为空或全部为可信代理 → 回退到请求来源地址。

约定：
    - 本模块全部为纯同步函数，不触库、不读配置——trusted_proxies 由调用方
      实时从 get_config() 取出传入，便于单测与复用；
    - 所有输出经 ipaddress 归一化（去环回映射、压缩表示），无法解析的
      条目直接跳过而非抛错（恶意构造的头不应导致注册 500）。
"""

import ipaddress

# IPv6 环回/IPv4-mapped 归一化由 ipaddress.ip_address 完成；此处只定义
# 「空 IP」统一出口口径（无 client 信息或全部条目不可信时返回空串）
EMPTY_IP = ""


def _parse_networks(entries: list[str]) -> list[ipaddress._BaseNetwork]:
    """把配置条目解析为网络对象列表；非法条目跳过（校验已在 Pydantic 层兜底）。"""
    networks = []
    for entry in entries or []:
        text = (entry or "").strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return networks


def _in_networks(address: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    """地址是否命中任一网络；无效地址一律视为未命中。"""
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(addr in network for network in networks)


def _normalize(address: str) -> str:
    """归一化为规范文本形式（压缩 IPv6、剥离 IPv4-mapped 前缀）；失败返回空串。"""
    text = (address or "").strip()
    if not text:
        return EMPTY_IP
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        # 可能带端口（如 "1.2.3.4:5678" 的非标 XFF 条目）：尝试剥端口再解析
        host, _, _port = text.rpartition(":")
        if host:
            try:
                return ipaddress.ip_address(host).compressed
            except ValueError:
                pass
        return EMPTY_IP


def resolve_client_ip(
    client_host: str, x_forwarded_for: str, trusted_proxies: list[str]
) -> str:
    """按可信代理解析真实客户端 IP，返回归一化的 IP 文本（未知返回空串）。

    client_host：request.client.host（TCP 对端地址）；
    x_forwarded_for：原始 X-Forwarded-For 头值（逗号分隔，可为空）；
    trusted_proxies：受信任代理的 IP/CIDR 列表（空列表 = 不信任任何头，
    恒用 TCP 对端地址，直连部署的安全默认）。
    """
    remote = (client_host or "").strip()
    if not remote:
        return EMPTY_IP
    networks = _parse_networks(trusted_proxies)
    # 对端不是可信代理：它就是真实客户端，任何 XFF 头都不可信
    if not networks or not _in_networks(remote, networks):
        return _normalize(remote)

    entries = [part.strip() for part in (x_forwarded_for or "").split(",")]
    # 从右往左找第一个不属于可信代理的地址（最近一跳由本站代理追加，最可信）
    for entry in reversed(entries):
        if not entry:
            continue
        if _in_networks(entry, networks):
            continue  # 上游仍是可信代理，继续向左追溯
        normalized = _normalize(entry)
        return normalized
    # 链上全部是可信代理（例如内网多级代理直连）：回退对端地址
    return _normalize(remote)
