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
    3. 地理白名单（可选）：离线地理数据库把 IP 解析为国家/城市，命中
       国家码或城市名白名单（两者同时配置时需同时命中）即视为可信。
       双库解析链（resolve_geo）：
       a) 优先查询 ip2region（.xdb，全球覆盖，国内城市精度显著优于
          GeoLite2；主路径 + v6 补充路径各指一份库文件，地址族由文件头
          自动识别并按注册 IP 路由）：结果国家 == 中国 → 直接采用该结果；
       b) 非 / 未知中国（含未配置 ip2region 或对应地址族缺库）→
          回落 MaxMind GeoLite2（.mmdb，国际城市数据更细）。

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
import re
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


# ── ip2region 离线地理查询（中国优先源）────────────────────────
IP2REGION_CHINA_NAME = "中国"  # 官方数据约定：中国区域信息全中文
_ISO2_RE = re.compile(r"[A-Za-z]{2}")

_xdb_lock = threading.Lock()
_xdb_searchers: dict[str, tuple[int, object]] = {}  # path -> (mtime_ns, Searcher)
_xdb_warned_paths: set[str] = set()


def _open_xdb_searcher(db_path: Path):
    """按路径+mtime 缓存 ip2region Searcher（content 策略整库载入内存）。

    与 _open_reader 同一套双检锁缓存语义：文件更新（mtime 变化）时重建，
    文件缺失/依赖缺失/打开失败返回 None 并只告警一次。Searcher 以 content
    缓存策略创建（查询零 I/O、不持共享文件句柄），线程安全。
    """
    from .ip2region_xdb import open_content_searcher

    try:
        mtime = db_path.stat().st_mtime_ns
    except OSError:
        if str(db_path) not in _xdb_warned_paths:
            logger.warning(
                "ip2region database not found at %s; China lookups will fall "
                "back to GeoLite2 until the file exists",
                db_path,
            )
            _xdb_warned_paths.add(str(db_path))
        return None
    cached = _xdb_searchers.get(str(db_path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with _xdb_lock:
        cached = _xdb_searchers.get(str(db_path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            searcher_obj = open_content_searcher(str(db_path))
        except Exception as e:  # noqa: BLE001 - 损坏的库文件不应炸注册流程
            logger.error("Failed to open ip2region database %s: %s", db_path, e)
            return None
        _xdb_searchers[str(db_path)] = (mtime, searcher_obj)
        _xdb_warned_paths.discard(str(db_path))
        logger.info("ip2region database loaded: %s", db_path)
        return searcher_obj


def parse_ip2region_region(region: str) -> dict | None:
    """解析 ip2region 区域字符串；仅中国归属产出结果，其余返回 None。

    官方数据格式随版本演进：
    - 新版（当前 maker）：``Country|Province|City|ISP|iso-alpha2``，
      中国条目全中文（如 ``中国|天津市|天津市|中国教育网|CN``）；
    - 旧版（v2.x）：``国家|大区|省份|城市|ISP``，无国家码，未知字段填 0。
    判定口径：末位两位字母视为 ISO 码（新版），否则按旧版取位并以国家名
    「中国」识别。中国 → {"country": "CN", "city_names": [省, 市]}；
    非 / 未知中国 → None（调用方回落 GeoLite2）。城市候选附带去除末尾
    「市/省」的变体，兼容 GeoLite2 风格（「天津」）与行政区划全称
    （「天津市」）两种白名单写法。
    """
    parts = [p.strip() for p in (region or "").split("|")]
    if not parts or not parts[0]:
        return None
    country = parts[0]
    if len(parts) >= 2 and _ISO2_RE.fullmatch(parts[-1]):
        iso = parts[-1].upper()
        province_city = parts[1:3]  # 新版：省、市
    else:
        iso = ""
        province_city = parts[2:4]  # 旧版跳过大区位，取省、市
    if iso != "CN" and country != IP2REGION_CHINA_NAME:
        return None
    names: list[str] = []
    for name in province_city:
        if not name or name == "0":
            continue
        names.append(name)
        for suffix in ("市", "省"):
            stripped = name[: -len(suffix)] if name.endswith(suffix) else ""
            if stripped:
                names.append(stripped)
    return {"country": "CN", "city_names": list(dict.fromkeys(names))}


def ip2region_lookup(db_path: str, ip: str) -> dict | None:
    """查询单份 ip2region 库；仅采纳中国归属的结果，其余一律返回 None。

    返回与 geo_lookup 同构的 {"country": "CN", "city_names": [...]}；
    非中国 / 未知 / 私有地址 / 地址族不匹配（库文件只覆盖一个地址族，
    由文件头探测）/ 库缺失 / 查询失败返回 None，由调用方按解析链回落
    GeoLite2。
    """
    searcher_obj = _open_xdb_searcher(Path(db_path))
    if searcher_obj is None:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if searcher_obj.get_ip_version().byte_num != len(addr.packed):
        # 地址族与该库不符：属正常路由（另一族走补充库或回落），静默降级
        return None
    try:
        region = searcher_obj.search(addr.packed)
    except Exception as e:  # noqa: BLE001 - 查询异常同样降级
        logger.warning("ip2region lookup failed for %s: %s", ip, e)
        return None
    if not isinstance(region, str):
        return None
    return parse_ip2region_region(region)


def resolve_geo(ip: str, cfg: MultiUserConfig) -> dict | None:
    """地理白名单的统一解析链（决策规则 3 的数据来源）：

    1. 配置了 ip2region 库 → 按注册 IP 的地址族路由查询（主路径与 v6
       补充路径各指一份 xdb，地址族由文件头自动识别）；命中中国归属即
       直接采用（国内精度碾压 GeoLite2）；
    2. 非 / 未知中国、对应地址族未配置或库不可用 → 回落 GeoLite2；
    3. 两级都无结果 → None（调用方 fail-closed 转人工审批）。
    """
    for xdb_path in (
        cfg.auto_approve_ip2region_db_path,
        cfg.auto_approve_ip2region_db_path_v6,
    ):
        if not xdb_path:
            continue
        geo = ip2region_lookup(xdb_path, ip)
        if geo is not None:
            logger.debug("Geo source for registration policy: ip2region")
            return geo
    if cfg.auto_approve_geo_db_path:
        geo = geo_lookup(cfg.auto_approve_geo_db_path, ip)
        if geo is not None:
            logger.debug("Geo source for registration policy: geolite2")
            return geo
    return None


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
        (
            cfg.auto_approve_geo_db_path
            or cfg.auto_approve_ip2region_db_path
            or cfg.auto_approve_ip2region_db_path_v6
        )
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

    # 规则 3：地理白名单（ip2region 中国优先 + GeoLite2 回落；
    # 两级都解析失败 fail-closed 为不自动通过）
    if geo_rules_active:
        geo = resolve_geo(ip, cfg)
        if geo is None:
            return ApprovalDecision(False, REASON_GEO_UNAVAILABLE)
        if _match_geo(
            geo, cfg.auto_approve_allowed_countries, cfg.auto_approve_allowed_cities
        ):
            return ApprovalDecision(True, REASON_GEO_MATCHED)

    return ApprovalDecision(False, REASON_NOT_ALLOWLISTED)


def close_geo_cache() -> None:
    """关闭并清空 Reader / Searcher 缓存（进程退出 / 测试隔离用）。"""
    with _reader_lock:
        for _mtime, reader in _readers.values():
            try:
                reader.close()
            except Exception:  # noqa: BLE001 - 关闭路径尽力而为
                pass
        _readers.clear()
    with _xdb_lock:
        for _mtime, searcher_obj in _xdb_searchers.values():
            try:
                searcher_obj.close()
            except Exception:  # noqa: BLE001 - 关闭路径尽力而为
                pass
        _xdb_searchers.clear()
