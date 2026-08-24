"""注册自动审批策略（多用户模式）：基于注册 IP 的恶意性判定与自动通过。

目标（对应管理员诉求）：
    注册请求携带真实客户端 IP；IP「无恶意」时自动通过审批（status=active），
    否则回落人工待审批（status=pending）。恶意性判定全部为**正向白名单**
    语义，规则可叠加：

    1. 同 IP 账号数上限：同一 registered_ip 下历史所有未被删除的账号
       （含 pending/disabled）达到上限即不自动通过——防批量小号；
       默认上限 1。共享出口 IP 的机构部署请调大该值或改用地理规则。
    2. IP/CIDR 白名单：命中 auto_approve_allowed_cidrs 即视为可信
       （如校园网出口网段），跳过地理校验直接通过；
    3. 地理白名单（可选）：离线 MaxMind GeoLite2 数据库（.mmdb，免费，
       需自行下载）把 IP 解析为国家/城市，命中国家码或城市名白名单
       （两者同时配置时需同时命中）即视为可信。

    未配置任何白名单且开关打开 = 「全部自动通过（仍受同 IP 上限约束）」
    的最简模式。任何检查不通过都只是**降级为人工审批**，绝不直接拒绝，
    避免规则误伤真实用户。

约定：
    - 本模块为同步函数层（SQLite / mmap 读库均不可阻塞事件循环），
      FastAPI 层经 asyncio.to_thread 调用；
    - 方法内实时 get_config() 的口径由调用方保持：evaluate 接收已取出的
      multi_user 配置对象，便于单测注入；
    - 地理库缺失 / 依赖未安装 / 查询失败一律 fail-closed 为「不自动通过」，
      只告警不抛错——注册主流程永不因策略基础设施故障而 5xx。
"""

import ipaddress
import threading
from dataclasses import dataclass
from pathlib import Path

from backend.core.config import MultiUserConfig
from backend.core.logger import get_logger

logger = get_logger("multiuser.regpolicy")

# 决策原因（审计日志与测试断言用；不进 API 契约）
REASON_DISABLED = "disabled"  # 自动审批总开关关闭
REASON_NO_IP = "no_ip"  # 无法确定客户端 IP（防御分支）
REASON_IP_LIMIT = "ip_limit"  # 同 IP 账号数达上限
REASON_NO_RESTRICTIONS = "no_restrictions"  # 开关开启且未配置任何白名单
REASON_CIDR_ALLOWLISTED = "cidr_allowlisted"  # 命中 IP/CIDR 白名单
REASON_GEO_MATCHED = "geo_matched"  # 命中地理白名单
REASON_GEO_UNAVAILABLE = "geo_unavailable"  # 配置了地理规则但无法解析（fail-closed）
REASON_NOT_ALLOWLISTED = "not_allowlisted"  # 配置了白名单但未命中


@dataclass(frozen=True)
class ApprovalDecision:
    """一次注册的审批决策：approved=True 时注册直接进入 active 状态。"""

    approved: bool
    reason: str


# ── 同 IP 账号计数 ──────────────────────────────────────────────
def count_registered_accounts(ip: str) -> int:
    """统计某注册 IP 名下现存账号数（全状态，不含已删除——users 行为硬删除）。"""
    from .db import db_count

    if not ip:
        return 0
    return db_count("SELECT COUNT(*) FROM users WHERE registered_ip = ?", (ip,))


# ── GeoLite2 离线地理查询 ───────────────────────────────────────
try:  # 可选依赖：requirements 已收录，环境缺 wheel 时优雅降级
    import maxminddb
except ImportError:  # pragma: no cover - 仅未装依赖的环境触发
    maxminddb = None

_reader_lock = threading.Lock()
_readers: dict[str, tuple[int, object]] = {}  # path -> (mtime_ns, Reader)
_warned_paths: set[str] = set()


def _open_reader(db_path: Path):
    """按路径+mtime 缓存 maxminddb Reader；依赖缺失/文件缺失返回 None 并只告警一次。

    文件更新（mtime 变化）时关闭并替换旧 Reader，避免 mmap/fd 随每次
    库文件覆盖而泄漏。
    """
    if maxminddb is None:
        if "module" not in _warned_paths:
            _warned_paths.add("module")
            logger.warning(
                "maxminddb is not installed; GeoIP rules for registration "
                "auto-approval are unavailable (pip install maxminddb)"
            )
        return None
    try:
        mtime = db_path.stat().st_mtime_ns
    except OSError:
        if db_path not in _warned_paths:
            logger.warning(
                "GeoLite2 database not found at %s; registrations will not be "
                "auto-approved by geographic rules until the file exists",
                db_path,
            )
            _warned_paths.add(db_path)
        return None
    cached = _readers.get(str(db_path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with _reader_lock:
        cached = _readers.get(str(db_path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            reader = maxminddb.open_database(str(db_path))
        except Exception as e:  # noqa: BLE001 - 损坏的库文件不应炸注册流程
            logger.error("Failed to open GeoIP database %s: %s", db_path, e)
            return None
        stale = _readers.pop(str(db_path), None)
        if stale is not None:
            # 旧 Reader 必须显式关闭；并发中仍持有旧 Reader 的查询由
            # geo_lookup 的异常兜底 fail-closed（转人工），不影响注册主流程。
            try:
                stale[1].close()
            except Exception:  # noqa: BLE001 - 关闭路径尽力而为
                pass
        _readers[str(db_path)] = (mtime, reader)
        logger.info("GeoIP database loaded: %s", db_path)
        return reader


def geo_lookup(db_path: str, ip: str) -> dict | None:
    """查询 IP 的地理归属，返回 {"country": ISO 码或 "", "city_names": [...]}。

    私有地址 / 库外 IP / 任何失败返回 None（调用方按 fail-closed 处理）。
    """
    reader = _open_reader(Path(db_path))
    if reader is None:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    try:
        record = reader.get(addr)
    except Exception as e:  # noqa: BLE001 - 查询异常同样降级
        logger.warning("GeoIP lookup failed for %s: %s", ip, e)
        return None
    if not isinstance(record, dict):
        return None
    country = ((record.get("country") or {}).get("iso_code") or "").upper()
    names = (record.get("city") or {}).get("names") or {}
    return {
        "country": country,
        # 收录全部语言名称（en / zh-CN / ...），城市匹配大小写不敏感
        "city_names": [v for v in names.values() if isinstance(v, str)],
    }


def _match_geo(geo: dict, countries: list[str], cities: list[str]) -> bool:
    """白名单匹配语义：国家与城市同时配置时需同时命中（AND）；各自大小写不敏感。"""
    country_ok = not countries or geo["country"].upper() in {
        c.strip().upper() for c in countries
    }
    city_ok = not cities or (
        {n.lower() for n in geo["city_names"]}
        & {c.strip().lower() for c in cities if c.strip()}
    )
    return country_ok and bool(city_ok)


def _parse_cidrs(entries: list[str]) -> list:
    networks = []
    for entry in entries or []:
        text = (entry or "").strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue  # Pydantic 校验已拦截非法条目；此处防御运行期改动
    return networks


# ── 决策入口 ────────────────────────────────────────────────────
def evaluate_registration_ip(
    ip: str, existing_accounts: int, cfg: MultiUserConfig
) -> ApprovalDecision:
    """按配置评估注册 IP，返回是否自动通过与原因。

    纯函数：同 IP 存量账号数由调用方传入（service 层先查库），本函数
    只做决策，便于穷举式单元测试。
    """
    if not cfg.auto_approve_enabled:
        return ApprovalDecision(False, REASON_DISABLED)
    if not ip:
        return ApprovalDecision(False, REASON_NO_IP)

    # 规则 1：同 IP 账号上限优先于一切白名单（白名单放行的是「地点」，不是「数量」）
    if existing_accounts >= cfg.auto_approve_max_accounts_per_ip:
        return ApprovalDecision(False, REASON_IP_LIMIT)

    cidrs_configured = bool(cfg.auto_approve_allowed_cidrs)
    geo_rules_active = bool(
        cfg.auto_approve_geo_db_path
        and (cfg.auto_approve_allowed_countries or cfg.auto_approve_allowed_cities)
    )

    # 未配置任何白名单：最简「全放行」模式（仍受同 IP 上限约束）
    if not cidrs_configured and not geo_rules_active:
        return ApprovalDecision(True, REASON_NO_RESTRICTIONS)

    # 规则 2：CIDR 白名单命中即通过（跳过地理校验，如校园网网段直连场景）
    if cidrs_configured:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            addr = None
        if addr is not None and any(
            addr in network for network in _parse_cidrs(cfg.auto_approve_allowed_cidrs)
        ):
            return ApprovalDecision(True, REASON_CIDR_ALLOWLISTED)

    # 规则 3：地理白名单（查询失败 fail-closed 为不自动通过）
    if geo_rules_active:
        geo = geo_lookup(cfg.auto_approve_geo_db_path, ip)
        if geo is None:
            return ApprovalDecision(False, REASON_GEO_UNAVAILABLE)
        if _match_geo(
            geo, cfg.auto_approve_allowed_countries, cfg.auto_approve_allowed_cities
        ):
            return ApprovalDecision(True, REASON_GEO_MATCHED)

    return ApprovalDecision(False, REASON_NOT_ALLOWLISTED)


def close_geo_cache() -> None:
    """关闭并清空 Reader 缓存（进程退出 / 测试隔离用）。"""
    with _reader_lock:
        for _mtime, reader in _readers.values():
            try:
                reader.close()
            except Exception:  # noqa: BLE001 - 关闭路径尽力而为
                pass
        _readers.clear()
