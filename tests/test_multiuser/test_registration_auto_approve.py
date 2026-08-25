"""多用户注册自动审批（IP 策略）：clientip 解析 / 策略决策 / API 集成 / 迁移。

覆盖：
    1. clientip.resolve_client_ip：可信代理解析 X-Forwarded-For、防伪造头
    2. registration_policy.evaluate_registration_ip：开关/同 IP 上限/
       CIDR 白名单/地理白名单（mock mmdb reader）的穷举决策
    3. geo_lookup：mmdb 记录解析与 fail-closed 行为（依赖缺失/坏文件）
    4. ip2region 双库解析链：区域字符串新旧格式解析、中国优先采纳、
       非/未知中国回落 GeoLite2、双库不可用 fail-closed、真实 xdb 文件
       （手工构造的最小合法 xdb）端到端查询与 mtime 缓存重建
    5. 注册 API：IP 记录、自动通过直接可登录、同 IP 第二账号转人工、
       每 IP 注册限流、管理员列表暴露 IP 审计字段
    6. v9 迁移：幂等重入 + 存量 v8 库无损升级
"""

import ipaddress
import os
import sqlite3
import struct

import pytest
from fastapi.testclient import TestClient

from backend.core.config import MultiUserConfig
from backend.core.multiuser import clientip
from backend.core.multiuser import db as db_module
from backend.core.multiuser import registration_policy as regpolicy
from tests.test_multiuser.conftest import build_mu_app, build_mu_env, csrf

PROXY = "10.0.0.2"  # 测试中的可信反代地址


@pytest.fixture(autouse=True)
def _clean_register_rate_limits():
    """清理 register_ip 限流事件，避免小时级窗口跨用例串扰。"""
    yield
    if db_module.is_initialized():
        db_module.db_execute("DELETE FROM rate_limit_events WHERE kind = 'register_ip'")


def make_client(ip: str) -> TestClient:
    """以指定来源 IP 构造测试客户端（Starlette TestClient 的 client 参数）。"""
    return TestClient(build_mu_app(), client=(ip, 40000))


# ── 1. 真实客户端 IP 解析 ────────────────────────────────────────
class TestResolveClientIp:
    def test_direct_connection_ignores_forwarded_header(self):
        """对端非可信代理时忽略 XFF——伪造头不能绕过 IP 策略。"""
        assert clientip.resolve_client_ip("203.0.113.7", "1.2.3.4", []) == "203.0.113.7"
        assert (
            clientip.resolve_client_ip("203.0.113.7", "1.2.3.4", [f"{PROXY}/32"])
            == "203.0.113.7"
        )

    def test_trusted_proxy_single_hop(self):
        ip = clientip.resolve_client_ip(PROXY, "198.51.100.9", [f"{PROXY}/32"])
        assert ip == "198.51.100.9"

    def test_trusted_proxy_multi_hop_skips_trusted_chain(self):
        """多级可信代理逐跳追加：从右往左找第一个不可信地址。"""
        xff = f"203.0.113.5, {PROXY}, 10.0.0.3"
        ip = clientip.resolve_client_ip(PROXY, xff, ["10.0.0.0/8"])
        assert ip == "203.0.113.5"

    def test_spoofed_prefix_after_real_proxy_hop(self):
        """客户端自带的伪造条目落在链首，先于可信代理条目被跳过。"""
        xff = f"6.6.6.6, 198.51.100.9"
        ip = clientip.resolve_client_ip(PROXY, xff, ["10.0.0.0/8"])
        # 链中第一个不可信地址是 198.51.100.9（真实上一跳），伪造的 6.6.6.6 被忽略
        assert ip == "198.51.100.9"

    def test_all_trusted_chain_falls_back_to_remote(self):
        ip = clientip.resolve_client_ip(PROXY, f"{PROXY}, 10.0.0.3", ["10.0.0.0/8"])
        assert ip == PROXY

    def test_garbage_entries_skipped_not_raised(self):
        assert clientip.resolve_client_ip(PROXY, "not-an-ip,, ", [f"{PROXY}/32"]) in (
            "",
            PROXY,
        )
        assert clientip.resolve_client_ip("", "", ["0.0.0.0/0"]) == ""

    def test_ipv6_normalized(self):
        ip = clientip.resolve_client_ip(
            PROXY, "2001:0DB8:0000:0000:0000:0000:0000:0001", [f"{PROXY}/32"]
        )
        assert ip == "2001:db8::1"


# ── 2. 审批策略决策 ──────────────────────────────────────────────
class TestEvaluateRegistrationIp:
    def test_disabled_always_pending(self):
        cfg = MultiUserConfig(auto_approve_enabled=False)
        d = regpolicy.evaluate_registration_ip("8.8.8.8", 0, cfg)
        assert (d.approved, d.reason) == (False, regpolicy.REASON_DISABLED)

    def test_enabled_without_rules_approves(self):
        cfg = MultiUserConfig(auto_approve_enabled=True)
        d = regpolicy.evaluate_registration_ip("8.8.8.8", 0, cfg)
        assert (d.approved, d.reason) == (True, regpolicy.REASON_NO_RESTRICTIONS)

    def test_same_ip_limit_blocks_before_allowlist(self):
        """同 IP 上限优先于一切白名单：白名单放行的是地点，不是数量。"""
        cfg = MultiUserConfig(
            auto_approve_enabled=True,
            auto_approve_max_accounts_per_ip=2,
            auto_approve_allowed_cidrs=["8.8.8.0/24"],
        )
        assert regpolicy.evaluate_registration_ip("8.8.8.8", 1, cfg).approved
        d = regpolicy.evaluate_registration_ip("8.8.8.8", 2, cfg)
        assert (d.approved, d.reason) == (False, regpolicy.REASON_IP_LIMIT)

    def test_empty_ip_never_approved(self):
        cfg = MultiUserConfig(auto_approve_enabled=True)
        assert regpolicy.evaluate_registration_ip("", 0, cfg).reason == (
            regpolicy.REASON_NO_IP
        )

    def test_cidr_hit_and_miss(self):
        cfg = MultiUserConfig(
            auto_approve_enabled=True,
            auto_approve_allowed_cidrs=["59.77.0.0/16", "192.168.1.5/32"],
        )
        hit = regpolicy.evaluate_registration_ip("59.77.1.2", 0, cfg)
        assert (hit.approved, hit.reason) == (True, regpolicy.REASON_CIDR_ALLOWLISTED)
        single = regpolicy.evaluate_registration_ip("192.168.1.5", 0, cfg)
        assert single.approved is True
        miss = regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg)
        assert (miss.approved, miss.reason) == (False, regpolicy.REASON_NOT_ALLOWLISTED)

    @pytest.fixture
    def _fake_geo(self, monkeypatch):
        """注入假 mmdb reader：城市合肥（en/zh-CN），国家 CN。"""
        record = {
            "country": {"iso_code": "CN"},
            "city": {"names": {"en": "Hefei", "zh-CN": "合肥"}},
        }

        class _FakeReader:
            def get(self, addr):
                return dict(record)

        monkeypatch.setattr(regpolicy, "_open_reader", lambda path: _FakeReader())
        return record

    def test_geo_city_match_zh_and_en(self, _fake_geo):
        for city in ("合肥", "hefei"):
            cfg = MultiUserConfig(
                auto_approve_enabled=True,
                auto_approve_geo_db_path="GeoLite2-City.mmdb",
                auto_approve_allowed_cities=[city],
            )
            d = regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg)
            assert (d.approved, d.reason) == (True, regpolicy.REASON_GEO_MATCHED)

    def test_geo_country_and_city_require_both(self, _fake_geo):
        base = {
            "auto_approve_enabled": True,
            "auto_approve_geo_db_path": "GeoLite2-City.mmdb",
        }
        both = MultiUserConfig(
            **base,
            auto_approve_allowed_countries=["CN"],
            auto_approve_allowed_cities=["合肥"],
        )
        assert both and regpolicy.evaluate_registration_ip("1.2.3.4", 0, both).approved
        wrong_city = MultiUserConfig(
            **base,
            auto_approve_allowed_countries=["CN"],
            auto_approve_allowed_cities=["上海"],
        )
        assert not regpolicy.evaluate_registration_ip("1.2.3.4", 0, wrong_city).approved
        wrong_country = MultiUserConfig(
            **base,
            auto_approve_allowed_countries=["US"],
            auto_approve_allowed_cities=["合肥"],
        )
        assert not regpolicy.evaluate_registration_ip(
            "1.2.3.4", 0, wrong_country
        ).approved

    def test_geo_lookup_fail_is_fail_closed(self, monkeypatch):
        """配置了地理规则但库缺失/查询失败 → 不自动通过（转人工）。"""
        monkeypatch.setattr(regpolicy, "_open_reader", lambda path: None)
        cfg = MultiUserConfig(
            auto_approve_enabled=True,
            auto_approve_geo_db_path="missing.mmdb",
            auto_approve_allowed_cities=["合肥"],
        )
        d = regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg)
        assert (d.approved, d.reason) == (False, regpolicy.REASON_GEO_UNAVAILABLE)

    def test_geo_db_path_without_lists_keeps_rules_inactive(self, monkeypatch):
        """只配库路径未配名单 = 未启用地理规则，不触发 geo 查询。"""
        cfg = MultiUserConfig(
            auto_approve_enabled=True, auto_approve_geo_db_path="x.mmdb"
        )
        called = []
        monkeypatch.setattr(regpolicy, "geo_lookup", lambda path, ip: called.append(ip))
        d = regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg)
        assert d.approved is True and not called


# ── 3. geo_lookup 记录解析 ───────────────────────────────────────
class TestGeoLookup:
    def test_parses_country_and_multilang_cities(self, monkeypatch):
        class _Reader:
            def get(self, addr):
                if str(addr) == "1.2.3.4":
                    return {
                        "country": {"iso_code": "cn"},
                        "city": {"names": {"en": "Hefei", "zh-CN": "合肥"}},
                    }
                return None

        monkeypatch.setattr(regpolicy, "_open_reader", lambda path: _Reader())
        geo = regpolicy.geo_lookup("x.mmdb", "1.2.3.4")
        assert geo == {"country": "CN", "city_names": ["Hefei", "合肥"]}
        assert regpolicy.geo_lookup("x.mmdb", "255.255.255.255") is None

    def test_invalid_ip_returns_none(self, monkeypatch):
        monkeypatch.setattr(regpolicy, "_open_reader", lambda path: object())
        assert regpolicy.geo_lookup("x.mmdb", "not-an-ip") is None

    def test_reader_exception_degrades_to_none(self, monkeypatch):
        class _Boom:
            def get(self, addr):
                raise RuntimeError("mmap boom")

        monkeypatch.setattr(regpolicy, "_open_reader", lambda path: _Boom())
        assert regpolicy.geo_lookup("x.mmdb", "1.2.3.4") is None

    def test_reader_replaced_on_mtime_change_closes_old(self, tmp_path, monkeypatch):
        """库文件更新（mtime 变化）后重建 Reader，并关闭旧 Reader 防 mmap/fd 泄漏。"""
        import os

        db_file = tmp_path / "geo.mmdb"
        db_file.write_bytes(b"mmdb-bytes")
        opened = []
        closed = []

        class _FakeReader:
            def get(self, addr):
                return {"country": {"iso_code": "CN"}}

            def close(self):
                closed.append(self)

        class _FakeMaxMindDB:
            def open_database(self, path):
                reader = _FakeReader()
                opened.append(reader)
                return reader

        # 注入假模块，测试不依赖真实 maxminddb 是否安装
        monkeypatch.setattr(regpolicy, "maxminddb", _FakeMaxMindDB())
        regpolicy.close_geo_cache()
        try:
            first = regpolicy.geo_lookup(str(db_file), "1.2.3.4")
            assert first == {"country": "CN", "city_names": []}
            assert len(opened) == 1
            # mtime 前移模拟 GeoLite2 库文件更新
            st = db_file.stat()
            os.utime(db_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            second = regpolicy.geo_lookup(str(db_file), "1.2.3.4")
            assert second == first
            # 断言须在 close_geo_cache 收尾前：仅旧 Reader 因替换被关闭
            assert len(opened) == 2
            assert closed == [opened[0]]
        finally:
            regpolicy.close_geo_cache()


# ── 3b. ip2region 双库解析链（中国优先 + GeoLite2 回落）──────────
class TestParseIp2RegionRegion:
    def test_new_format_china_adopts_iso_and_city(self):
        """新版格式 Country|Province|City|ISP|ISO：中国条目全中文。"""
        geo = regpolicy.parse_ip2region_region("中国|天津市|天津市|中国教育网|CN")
        assert geo == {"country": "CN", "city_names": ["天津市", "天津"]}

    def test_city_suffix_variants_for_whitelist_compat(self):
        """省/市后缀剥离变体：兼容 GeoLite2 风格（合肥）与全称（合肥市）。"""
        geo = regpolicy.parse_ip2region_region("中国|安徽省|合肥市|中国电信|CN")
        assert geo["city_names"] == ["安徽省", "安徽", "合肥市", "合肥"]

    def test_old_format_china_skips_region_field(self):
        """旧版格式 国家|大区|省份|城市|ISP：跳过大区位取省市，按国名识别中国。"""
        geo = regpolicy.parse_ip2region_region("中国|华东|福建省|厦门市|电信")
        assert geo == {
            "country": "CN",
            "city_names": ["福建省", "福建", "厦门市", "厦门"],
        }

    def test_non_china_returns_none(self):
        """非中国（新旧格式皆然）不产出结果——由调用方回落 GeoLite2。"""
        assert (
            regpolicy.parse_ip2region_region("United States|California|0|Google LLC|US")
            is None
        )
        assert regpolicy.parse_ip2region_region("美国|0|0|0|0") is None

    def test_placeholders_filtered_but_china_kept(self):
        geo = regpolicy.parse_ip2region_region("中国|0|0|0|0")
        assert geo == {"country": "CN", "city_names": []}

    def test_empty_or_garbage_returns_none(self):
        assert regpolicy.parse_ip2region_region("") is None
        assert regpolicy.parse_ip2region_region(None) is None


def _build_mini_xdb(path, segments, *, ipv6: bool = False):
    """手工构造最小合法 xdb，供 vendored searcher 端到端查询。

    segments: [(start, end, region_str)]。IPv4（结构 v2）时 start/end 为
    整数，索引内 IP 按官方 maker 以小端存储；IPv6（结构 v3）时 start/end
    为 16 字节 packed（大端原样存储），索引条目 38B/条。
    布局：header(256B) + vector index(512KB) + 区域数据块 + 索引块。
    """
    byte_num, entry_fmt = (16, "<16s16sHI") if ipv6 else (4, "<4s4sHI")
    index_size = 38 if ipv6 else 14
    version, ip_version_id = (3, 6) if ipv6 else (2, 4)
    header_len, vector_len = 256, 524288
    data_start = header_len + vector_len

    def _store(value):
        return value if ipv6 else value.to_bytes(byte_num, "little")

    def _bucket(value):
        if ipv6:
            return (value[0], value[1])
        return ((value >> 24) & 0xFF, (value >> 16) & 0xFF)

    blob = bytearray()
    entries = []
    for start, end, region in segments:
        d_bytes = region.encode("utf-8")
        entries.append(
            (_store(start), _store(end), len(d_bytes), data_start + len(blob))
        )
        blob += d_bytes
    index_start = data_start + len(blob)
    buckets: dict[tuple[int, int], list[int]] = {}
    head = bytearray(data_start)
    struct.pack_into(
        "<HHIIIHH", head, 0, version, 0, 0, index_start, index_start, ip_version_id, 4
    )
    index_buf = bytearray(len(entries) * index_size)
    for i, (start_b, end_b, dlen, dptr) in enumerate(entries):
        idx_ptr = index_start + i * index_size
        bucket = _bucket(segments[i][0])
        slots = buckets.setdefault(bucket, [i, i])
        slots[1] = i
        first_ptr = index_start + slots[0] * index_size
        last_ptr = index_start + slots[1] * index_size
        struct.pack_into(
            "<II",
            head,
            header_len + bucket[0] * 2048 + bucket[1] * 8,
            first_ptr,
            last_ptr,
        )
        struct.pack_into(
            entry_fmt,
            index_buf,
            i * index_size,
            start_b,
            end_b,
            dlen,
            dptr,
        )
    path.write_bytes(bytes(head) + bytes(blob) + bytes(index_buf))


class TestIp2RegionXdbIntegration:
    """vendored searcher 真实文件冒烟：手工 xdb 端到端查询 + mtime 缓存重建。"""

    SEGMENTS = [
        (
            0xCA71C000,
            0xCA71DFFF,
            "中国|天津市|天津市|中国教育网|CN",
        ),  # 202.113.192.0/19
        (0x3B4D0000, 0x3B4DFFFF, "中国|华东|福建省|厦门市|电信"),  # 59.77.0.0/16
        (0x8080000, 0x808FFFF, "United States|California|0|Google LLC|US"),
    ]

    V6_SEGMENTS = [
        (
            ipaddress.ip_address("2001:db8::").packed,
            ipaddress.ip_address("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff").packed,
            "中国|北京市|北京市|教育网|CN",
        ),  # 2001:db8::/32
    ]

    @pytest.fixture(autouse=True)
    def _isolated_cache(self):
        regpolicy.close_geo_cache()
        yield
        regpolicy.close_geo_cache()

    def test_lookup_real_xdb_china_new_format(self, tmp_path):
        db_file = tmp_path / "mini.xdb"
        _build_mini_xdb(db_file, self.SEGMENTS)
        geo = regpolicy.ip2region_lookup(str(db_file), "202.113.200.5")
        assert geo == {"country": "CN", "city_names": ["天津市", "天津"]}
        # 同桶内无命中段 → 空 region → None
        assert regpolicy.ip2region_lookup(str(db_file), "202.113.1.1") is None

    def test_lookup_old_format_and_foreign_fallback_to_none(self, tmp_path):
        db_file = tmp_path / "mini.xdb"
        _build_mini_xdb(db_file, self.SEGMENTS)
        old = regpolicy.ip2region_lookup(str(db_file), "59.77.1.2")
        assert old == {
            "country": "CN",
            "city_names": ["福建省", "福建", "厦门市", "厦门"],
        }
        # 非中国 → None（回落语义）；库外 IP → 空 region → None
        assert regpolicy.ip2region_lookup(str(db_file), "8.8.8.8") is None
        assert regpolicy.ip2region_lookup(str(db_file), "9.9.9.9") is None

    def test_searcher_cached_until_mtime_change(self, tmp_path, monkeypatch):
        import backend.core.multiuser.ip2region_xdb as xdb_pkg

        db_file = tmp_path / "mini.xdb"
        _build_mini_xdb(db_file, self.SEGMENTS[:1])
        opens = []
        real_open = xdb_pkg.open_content_searcher

        def counting_open(path):
            opens.append(path)
            return real_open(path)

        monkeypatch.setattr(xdb_pkg, "open_content_searcher", counting_open)
        first = regpolicy.ip2region_lookup(str(db_file), "202.113.200.5")
        second = regpolicy.ip2region_lookup(str(db_file), "202.113.200.5")
        assert first == second and len(opens) == 1
        st = db_file.stat()
        os.utime(db_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        third = regpolicy.ip2region_lookup(str(db_file), "202.113.200.5")
        assert third == first and len(opens) == 2

    def test_corrupt_db_degrades_to_none(self, tmp_path):
        bad = tmp_path / "bad.xdb"
        bad.write_bytes(b"not-an-xdb")
        assert regpolicy.ip2region_lookup(str(bad), "202.113.10.5") is None
        missing = tmp_path / "missing.xdb"
        assert regpolicy.ip2region_lookup(str(missing), "202.113.10.5") is None

    def test_ipv6_xdb_end_to_end(self, tmp_path):
        """IPv6 xdb（结构 v3）真实文件查询 + 地址族不匹配静默降级。"""
        db_file = tmp_path / "mini-v6.xdb"
        _build_mini_xdb(db_file, self.V6_SEGMENTS, ipv6=True)
        geo = regpolicy.ip2region_lookup(str(db_file), "2001:db8::1")
        assert geo == {"country": "CN", "city_names": ["北京市", "北京"]}
        # 族不匹配：v4 查询打在 v6 库上 → None（不抛错，由调用方回落）
        assert regpolicy.ip2region_lookup(str(db_file), "202.113.200.5") is None


class TestGeoResolutionChain:
    """resolve_geo 决策链：ip2region 中国优先采纳，非/未知中国回落 GeoLite2。"""

    @staticmethod
    def _cfg(**kw) -> MultiUserConfig:
        base = {"auto_approve_enabled": True}
        base.update(kw)
        return MultiUserConfig(**base)

    def test_china_result_short_circuits_geolite2(self, monkeypatch):
        monkeypatch.setattr(
            regpolicy,
            "ip2region_lookup",
            lambda p, ip: {"country": "CN", "city_names": ["合肥市", "合肥"]},
        )

        def _no_geolite(path, ip):
            raise AssertionError("China hit must not fall back to GeoLite2")

        monkeypatch.setattr(regpolicy, "geo_lookup", _no_geolite)
        cfg = self._cfg(
            auto_approve_ip2region_db_path="ip2region.xdb",
            auto_approve_geo_db_path="GeoLite2-City.mmdb",
            auto_approve_allowed_cities=["合肥"],
        )
        d = regpolicy.evaluate_registration_ip("202.113.64.1", 0, cfg)
        assert (d.approved, d.reason) == (True, regpolicy.REASON_GEO_MATCHED)

    def test_non_china_falls_back_to_geolite2(self, monkeypatch):
        """教育网痛点镜像场景：ip2region 认不出的国外 IP 由 GeoLite2 兜底判定。"""
        monkeypatch.setattr(regpolicy, "ip2region_lookup", lambda p, ip: None)
        seen: dict = {}
        monkeypatch.setattr(
            regpolicy,
            "geo_lookup",
            lambda p, ip: (
                seen.setdefault("args", (p, ip)),
                {"country": "US", "city_names": ["Los Angeles"]},
            )[1],
        )
        cfg = self._cfg(
            auto_approve_ip2region_db_path="ip2region.xdb",
            auto_approve_geo_db_path="GeoLite2-City.mmdb",
            auto_approve_allowed_countries=["US"],
            auto_approve_allowed_cities=["los angeles"],
        )
        d = regpolicy.evaluate_registration_ip("8.8.8.8", 0, cfg)
        assert (d.approved, d.reason) == (True, regpolicy.REASON_GEO_MATCHED)
        assert seen["args"] == ("GeoLite2-City.mmdb", "8.8.8.8")

    def test_ip2region_unavailable_falls_back_to_geolite2(self, monkeypatch):
        monkeypatch.setattr(regpolicy, "ip2region_lookup", lambda p, ip: None)
        monkeypatch.setattr(
            regpolicy,
            "geo_lookup",
            lambda p, ip: {"country": "CN", "city_names": ["Hefei", "合肥"]},
        )
        cfg = self._cfg(
            auto_approve_ip2region_db_path="missing.xdb",
            auto_approve_geo_db_path="GeoLite2-City.mmdb",
            auto_approve_allowed_cities=["合肥"],
        )
        assert regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg).approved

    def test_both_unavailable_fail_closed(self, monkeypatch):
        monkeypatch.setattr(regpolicy, "ip2region_lookup", lambda p, ip: None)
        monkeypatch.setattr(regpolicy, "geo_lookup", lambda p, ip: None)
        cfg = self._cfg(
            auto_approve_ip2region_db_path="i.xdb",
            auto_approve_geo_db_path="g.mmdb",
            auto_approve_allowed_countries=["CN"],
        )
        d = regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg)
        assert (d.approved, d.reason) == (False, regpolicy.REASON_GEO_UNAVAILABLE)

    def test_only_ip2region_configured_foreign_is_fail_closed(self, monkeypatch):
        """只配 ip2region 时非中国无回落源 → fail-closed 转人工（不误放行）。"""
        monkeypatch.setattr(regpolicy, "ip2region_lookup", lambda p, ip: None)

        def _no_geolite(path, ip):
            raise AssertionError("GeoLite2 not configured; must not be queried")

        monkeypatch.setattr(regpolicy, "geo_lookup", _no_geolite)
        cfg = self._cfg(
            auto_approve_ip2region_db_path="i.xdb",
            auto_approve_allowed_countries=["CN"],
        )
        d = regpolicy.evaluate_registration_ip("8.8.4.4", 0, cfg)
        assert (d.approved, d.reason) == (False, regpolicy.REASON_GEO_UNAVAILABLE)

    def test_geo_lists_without_any_db_keeps_rules_inactive(self, monkeypatch):
        """配了名单但两个库路径都为空 = 未启用地理规则（维持既有口径）。"""
        called = []
        monkeypatch.setattr(regpolicy, "resolve_geo", lambda ip, cfg: called.append(ip))
        cfg = self._cfg(auto_approve_allowed_countries=["CN"])
        d = regpolicy.evaluate_registration_ip("1.2.3.4", 0, cfg)
        assert d.approved and not called

    def test_ip2region_path_alone_activates_geo_rules(self, monkeypatch):
        """只配 ip2region 库路径 + 名单即激活地理规则（无需 GeoLite2 路径）。"""
        monkeypatch.setattr(
            regpolicy,
            "ip2region_lookup",
            lambda p, ip: {"country": "CN", "city_names": ["天津市", "天津"]},
        )
        cfg = self._cfg(
            auto_approve_ip2region_db_path="ip2region.xdb",
            auto_approve_allowed_cities=["天津"],
        )
        d = regpolicy.evaluate_registration_ip("202.113.64.1", 0, cfg)
        assert (d.approved, d.reason) == (True, regpolicy.REASON_GEO_MATCHED)

    def test_dual_family_routing_across_two_slots(self, tmp_path):
        """主路径 + v6 补充路径各一份 xdb：按注册 IP 地址族自动路由。"""
        v4_file = tmp_path / "v4.xdb"
        _build_mini_xdb(v4_file, TestIp2RegionXdbIntegration.SEGMENTS)
        v6_file = tmp_path / "v6.xdb"
        _build_mini_xdb(v6_file, TestIp2RegionXdbIntegration.V6_SEGMENTS, ipv6=True)
        cfg = self._cfg(
            auto_approve_ip2region_db_path=str(v4_file),
            auto_approve_ip2region_db_path_v6=str(v6_file),
            auto_approve_allowed_countries=["CN"],
            auto_approve_allowed_cities=["天津", "北京"],
        )
        v4_decision = regpolicy.evaluate_registration_ip("202.113.200.5", 0, cfg)
        assert (v4_decision.approved, v4_decision.reason) == (
            True,
            regpolicy.REASON_GEO_MATCHED,
        )
        v6_decision = regpolicy.evaluate_registration_ip("2001:db8::1", 0, cfg)
        assert (v6_decision.approved, v6_decision.reason) == (
            True,
            regpolicy.REASON_GEO_MATCHED,
        )

    def test_ipv6_query_without_v6_db_falls_back(self, tmp_path, monkeypatch):
        """只配 v4 主库时 IPv6 注册静默回落（不炸、不误放行）。"""
        v4_file = tmp_path / "v4.xdb"
        _build_mini_xdb(v4_file, TestIp2RegionXdbIntegration.SEGMENTS)
        # 场景 A：无 GeoLite2 → geo_unavailable 转人工
        cfg_a = self._cfg(
            auto_approve_ip2region_db_path=str(v4_file),
            auto_approve_allowed_countries=["CN"],
        )
        d = regpolicy.evaluate_registration_ip("2001:db8::1", 0, cfg_a)
        assert (d.approved, d.reason) == (False, regpolicy.REASON_GEO_UNAVAILABLE)
        # 场景 B：配了 GeoLite2 → 由回落源判定（mock 回落查询）
        monkeypatch.setattr(
            regpolicy,
            "geo_lookup",
            lambda p, ip: {"country": "AU", "city_names": ["Brisbane"]},
        )
        cfg_b = self._cfg(
            auto_approve_ip2region_db_path=str(v4_file),
            auto_approve_geo_db_path="GeoLite2-City.mmdb",
            auto_approve_allowed_cities=["brisbane"],
        )
        d = regpolicy.evaluate_registration_ip("2001:db8::1", 0, cfg_b)
        assert (d.approved, d.reason) == (True, regpolicy.REASON_GEO_MATCHED)

    def test_v6_slot_alone_activates_geo_rules_for_v6_clients(self, tmp_path):
        """只配 v6 补充库 + 名单：IPv6 客户端可自动通过，规则正确激活。"""
        v6_file = tmp_path / "v6.xdb"
        _build_mini_xdb(v6_file, TestIp2RegionXdbIntegration.V6_SEGMENTS, ipv6=True)
        cfg = self._cfg(
            auto_approve_ip2region_db_path_v6=str(v6_file),
            auto_approve_allowed_countries=["CN"],
            auto_approve_allowed_cities=["北京"],
        )
        d = regpolicy.evaluate_registration_ip("2001:db8::1", 0, cfg)
        assert (d.approved, d.reason) == (True, regpolicy.REASON_GEO_MATCHED)


# ── 4. 注册 API 集成 ─────────────────────────────────────────────
def _register(client, username, password="pass-12345678"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "email": ""},
    )


class TestRegisterAutoApproveAPI:
    def test_register_records_ip_pending_by_default(self, tmp_path):
        """默认关闭自动审批：仍记录 IP，账号进 pending。"""
        build_mu_env(tmp_path)
        client = make_client("203.0.113.10")
        r = _register(client, "alice")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending"
        user = db_module.db_query_one("SELECT * FROM users WHERE username = 'alice'")
        assert user["registered_ip"] == "203.0.113.10"
        assert user["auto_approved"] == 0

    def test_auto_approved_user_can_login_immediately(self, tmp_path):
        env = build_mu_env(tmp_path, mu_extra={"auto_approve_enabled": True})
        client = make_client("203.0.113.10")
        r = _register(client, "bob")
        assert r.status_code == 200
        assert r.json()["status"] == "active"
        user = db_module.db_query_one("SELECT * FROM users WHERE username = 'bob'")
        assert user["registered_ip"] == "203.0.113.10"
        assert user["auto_approved"] == 1
        # 无需等待审批，直接登录成功，并回填 last_login_ip
        r = client.post(
            "/api/auth/login",
            json={"username": "bob", "password": "pass-12345678"},
        )
        assert r.status_code == 200, r.text
        row = db_module.db_query_one("SELECT * FROM users WHERE username = 'bob'")
        assert row["last_login_ip"] == "203.0.113.10"

    def test_second_account_from_same_ip_falls_back_to_pending(self, tmp_path):
        """同 IP 第二个账号（上限 1）不自动通过 → 转人工待审批。"""
        env = build_mu_env(tmp_path, mu_extra={"auto_approve_enabled": True})
        client = make_client("203.0.113.10")
        assert _register(client, "first").json()["status"] == "active"
        r2 = _register(client, "second")
        assert r2.status_code == 200
        assert r2.json()["status"] == "pending"
        other = make_client("198.51.100.77")
        assert _register(other, "third").json()["status"] == "active"

    def test_higher_limit_allows_shared_exit_ip(self, tmp_path):
        """共享出口 IP 的机构部署调大上限后可批量自动通过。"""
        env = build_mu_env(
            tmp_path,
            mu_extra={
                "auto_approve_enabled": True,
                "auto_approve_max_accounts_per_ip": 3,
            },
        )
        client = make_client("10.1.2.3")
        for i in range(3):
            assert _register(client, f"user{i}").json()["status"] == "active"
        assert _register(client, "user3").json()["status"] == "pending"

    def test_cidr_allowlist_flow(self, tmp_path):
        env = build_mu_env(
            tmp_path,
            mu_extra={
                "auto_approve_enabled": True,
                "auto_approve_allowed_cidrs": ["203.0.113.0/24"],
            },
        )
        campus = make_client("203.0.113.99")
        assert _register(campus, "campus-user").json()["status"] == "active"
        outside = make_client("198.51.100.5")
        assert _register(outside, "outside-user").json()["status"] == "pending"

    def test_register_rate_limit_per_ip(self, tmp_path):
        env = build_mu_env(
            tmp_path,
            mu_extra={
                "auto_approve_enabled": True,
                "register_rate_limit_per_hour": 2,
            },
        )
        client = make_client("203.0.113.50")
        assert _register(client, "rl-one").status_code == 200
        assert _register(client, "rl-two").status_code == 200
        r = _register(client, "rl-three")
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "register_rate_limited"
        # 其他 IP 不受影响
        other = make_client("198.51.100.1")
        assert _register(other, "rl-other-ip").status_code == 200

    def test_admin_list_exposes_ip_audit_fields(self, tmp_path):
        env = build_mu_env(tmp_path, mu_extra={"auto_approve_enabled": True})
        admin_client = TestClient(build_mu_app(), client=(ADMIN_IP, 40000))
        from backend.core.multiuser import auth as auth_core

        auth_core.create_user(
            "root",
            "root-pass-123",
            role="admin",
            status="active",
            registered_ip=ADMIN_IP,
        )
        r = admin_client.post(
            "/api/auth/login", json={"username": "root", "password": "root-pass-123"}
        )
        assert r.status_code == 200
        make_client("203.0.113.10").post(
            "/api/auth/register",
            json={"username": "audited", "password": "pass-12345678"},
        )
        r = admin_client.get("/api/admin/users", headers=csrf(admin_client))
        assert r.status_code == 200
        users = {u["username"]: u for u in r.json()["users"]}
        assert users["audited"]["registered_ip"] == "203.0.113.10"
        assert users["audited"]["auto_approved"] is True


ADMIN_IP = "203.0.113.1"


# ── 5. v9 迁移 ──────────────────────────────────────────────────
class TestMigrationV9:
    def test_v9_column_reentry_idempotent(self, mu_env):
        """半途态重入：列已存在而版本未推进时重放 v9 不报 duplicate column。"""
        conn = db_module._require_conn()
        conn.execute("PRAGMA user_version = 8")  # 版本回退制造半途态
        db_module._migration_v9_registration_ip_policy(conn)
        applied = db_module.apply_migrations(conn)
        # v9 幂等：无重复列；版本推进到最新
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        assert {"registered_ip", "last_login_ip", "auto_approved"} <= cols
        assert applied == [] or applied[-1] <= db_module.SCHEMA_VERSION
        conn.rollback()

    def test_legacy_v8_db_upgrades_with_data_intact(self, tmp_path):
        """存量 v8 库升级到 v9：新列带默认值，既有用户数据完好。"""
        import json as _json
        import os as _os
        from unittest import mock as _mock

        from backend.core import config as cfg_module
        from tests.test_multiuser.conftest import MINIMAL_CONFIG, _reset_config_state

        db_path = tmp_path / "v8.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            for target, _description, fn in db_module.MIGRATIONS:
                if target > 8:
                    break
                fn(conn)
            conn.execute(
                "INSERT INTO users (id, username, password_hash, email, role, "
                "status, created_at, updated_at) VALUES "
                "('u-old', 'old-user', 'hash', '', 'user', 'pending', ?, ?)",
                ("2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        data = dict(MINIMAL_CONFIG)
        data["multi_user"] = {
            "enabled": True,
            "db_path": str(db_path),
            "users_root": str(tmp_path / "users") + "/",
            "staging_root": str(tmp_path / "staging") + "/",
        }
        config_path = tmp_path / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            _json.dump(data, f)
        with _mock.patch.dict(_os.environ, {}, clear=True):
            cfg_module.load_config(str(config_path))
        try:
            db_module.init_db()
            assert db_module.read_user_version(db_module._require_conn()) == 9
            user = db_module.db_query_one("SELECT * FROM users WHERE id = 'u-old'")
            assert user is not None
            assert user["registered_ip"] == "" and user["last_login_ip"] == ""
            assert user["auto_approved"] == 0
        finally:
            db_module.close_db()
            _reset_config_state()
