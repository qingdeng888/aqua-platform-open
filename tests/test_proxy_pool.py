"""
代理池模块纯单元测试（不依赖数据库/网络）

覆盖：gateway/app/proxy_pool.py
- build_proxy_url：协议白名单、地址与端口校验、凭据 percent-encoding、无认证/有认证
- build_client：直连与代理两种客户端的超时/连接池口径
- ProxyPool._select_url：direct / bind / rotate 三态选路与安全回退
- ProxyPool._pick_rotate_url：多代理轮询覆盖全部节点
"""
import httpx
import pytest

from app.proxy_pool import (
    ALLOWED_MODES,
    ALLOWED_SCHEMES,
    MODE_BIND,
    MODE_DIRECT,
    MODE_ROTATE,
    ProxyPool,
    build_client,
    build_proxy_url,
)


class TestBuildProxyUrl:
    """代理 URL 拼装"""

    @pytest.mark.parametrize("scheme", ALLOWED_SCHEMES)
    def test_allowed_schemes(self, scheme):
        assert build_proxy_url(scheme, "10.0.0.1", 1080) == f"{scheme}://10.0.0.1:1080"

    def test_scheme_normalized(self):
        # 大小写与空白由函数归一化，避免管理端录入差异导致选路失败
        assert build_proxy_url("  SOCKS5 ", "h", 1) == "socks5://h:1"

    def test_rejects_unknown_scheme(self):
        for bad in ("socks4", "ftp", "", "socks", "https-proxy"):
            with pytest.raises(ValueError):
                build_proxy_url(bad, "10.0.0.1", 1080)

    def test_rejects_empty_host(self):
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                build_proxy_url("socks5", bad, 1080)

    def test_rejects_port_out_of_range(self):
        for bad in (0, -1, 65536, 99999):
            with pytest.raises(ValueError):
                build_proxy_url("http", "10.0.0.1", bad)

    def test_port_boundaries_accepted(self):
        assert build_proxy_url("http", "h", 1).endswith(":1")
        assert build_proxy_url("http", "h", 65535).endswith(":65535")

    def test_anonymous_has_no_auth_part(self):
        url = build_proxy_url("socks5", "proxy.example.com", 1080, "", "")
        assert "@" not in url

    def test_username_password_encoded(self):
        # @ : / 等字符必须 percent-encode，否则会破坏 URL 结构（凭据泄漏到 host 段）
        url = build_proxy_url("http", "p.example.com", 8080, "u@ser", "p:a/ss@w")
        assert url == "http://u%40ser:p%3Aa%2Fss%40w@p.example.com:8080"

    def test_username_only(self):
        assert build_proxy_url("socks5", "h", 1080, "user") == "socks5://user@h:1080"

    def test_password_without_username_ignored(self):
        # 半套凭据不构成认证信息，端点层另有 400 校验
        assert build_proxy_url("socks5", "h", 1080, "", "pass") == "socks5://h:1080"


class TestBuildClient:
    """httpx 客户端工厂（直连池与代理池共用）；客户端未发起请求，无需异步关闭"""

    def test_direct_non_stream_timeout(self):
        client = build_client(None, stream=False)
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 120.0

    def test_stream_timeout_longer(self):
        client = build_client(None, stream=True)
        assert client.timeout.read == 600.0

    def test_proxy_client_constructs(self):
        client = build_client("socks5://127.0.0.1:1080", stream=False)
        assert isinstance(client, httpx.AsyncClient)


def _pool_with(active, bindings):
    """构造一个已就绪快照的 ProxyPool（不触碰 DB）"""
    pool = ProxyPool()
    pool._active = active
    pool._bindings = bindings
    pool._snapshot_at = 1.0
    return pool


class TestSelectUrl:
    """出网模式选路：direct / bind / rotate"""

    P1 = {"id": "p1", "name": "n1", "url": "socks5://1.1.1.1:1080"}
    P2 = {"id": "p2", "name": "n2", "url": "http://2.2.2.2:8080"}

    def test_modes_whitelist(self):
        assert ALLOWED_MODES == (MODE_DIRECT, MODE_BIND, MODE_ROTATE)

    def test_direct_returns_none(self):
        pool = _pool_with([self.P1], {"k1": (MODE_DIRECT, None)})
        assert pool._select_url("k1") is None

    def test_unknown_key_defaults_to_direct(self):
        pool = _pool_with([self.P1], {})
        assert pool._select_url("nobody") is None

    def test_bind_returns_bound_url(self):
        pool = _pool_with([self.P1, self.P2], {"k1": (MODE_BIND, "p2")})
        assert pool._select_url("k1") == self.P2["url"]

    def test_bind_missing_proxy_falls_back_to_direct(self):
        # 代理被删或被停用时回退直连，而不是让请求直接失败
        pool = _pool_with([self.P1], {"k1": (MODE_BIND, "gone")})
        assert pool._select_url("k1") is None

    def test_rotate_picks_from_pool(self):
        pool = _pool_with([self.P1, self.P2], {"k1": (MODE_ROTATE, None)})
        assert pool._select_url("k1") in (self.P1["url"], self.P2["url"])

    def test_rotate_empty_pool_falls_back_to_direct(self):
        pool = _pool_with([], {"k1": (MODE_ROTATE, None)})
        assert pool._select_url("k1") is None

    def test_rotate_covers_all_nodes(self):
        pool = _pool_with([self.P1, self.P2], {"k1": (MODE_ROTATE, None)})
        picked = {pool._select_url("k1") for _ in range(6)}
        assert picked == {self.P1["url"], self.P2["url"]}

    def test_invalidate_expires_snapshot(self):
        pool = _pool_with([self.P1], {"k1": (MODE_BIND, "p1")})
        pool.invalidate()
        assert pool._snapshot_at == 0.0

    def test_get_status_shape(self):
        pool = _pool_with([self.P1], {"k1": (MODE_BIND, "p1"), "k2": (MODE_DIRECT, None)})
        st = pool.get_status()
        assert st["active_proxies"] == 1
        assert st["cached_clients"] == 0
