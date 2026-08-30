"""代理池批量添加单元测试（gateway/app/admin_api.py 的纯函数与契约，不连库不联网）

批量添加：POST /gw/admin/proxies/bulk，请求体 proxy_urls 为多行文本、每行一个
`scheme://[user:pass@]host:port`，名称由后端按「前缀-序号」自动生成；
单个添加 POST /gw/admin/proxies 保持不变。

覆盖：
- parse_bulk_proxies：行号、空行与 # 注释跳过、协议白名单、端口边界、凭据解析
  （含 @ / : / percent-encoding）、路径拒收、IPv6 拒收、批内查重
- 与 build_proxy_url 的往返对称性（解析出来的凭据拼回 URL 再解一次必须一致）
- 契约守卫：单个添加路径必须保留；批量端点存在、需鉴权、不回传密码；查重不解密
"""
import os

import pytest

# 导入链上两处模块级强校验：database 要 PG_PASSWORD、admin_api 要管理员密码。
# 单测不连库不登录，仅提供占位值满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")
os.environ.setdefault("ACU_ADMIN_PASSWORD", "unit-test-placeholder")

from app.admin_api import (  # noqa: E402
    BULK_MAX_LINES,
    BULK_PROXY_NAME_PREFIX,
    parse_bulk_proxies,
)
from app.proxy_pool import ALLOWED_SCHEMES, build_proxy_url  # noqa: E402

from pathlib import Path  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_MODULE_SRC = (_REPO / "gateway" / "app" / "admin_api.py").read_text(encoding="utf-8")

_P1 = "http://user:pass@1.2.3.4:8080"
_P2 = "socks5://5.6.7.8:1080"


class TestParseBulkProxiesBasics:
    """多行文本 → 逐行结果"""

    def test_empty_text_yields_nothing(self):
        for raw in ("", "   ", "\n\n\t\n", None):
            assert parse_bulk_proxies(raw) == []

    def test_line_numbers_count_blank_and_comment_lines(self):
        # 行号必须对应用户在输入框里看到的行，跳过的空行/注释行同样占号
        items = parse_bulk_proxies("\n# 备注\n" + _P1 + "\n\n" + _P2)
        assert [(it["line"], it["host"]) for it in items] == [(3, "1.2.3.4"), (5, "5.6.7.8")]

    def test_surrounding_whitespace_stripped(self):
        (it,) = parse_bulk_proxies("  " + _P1 + "\t")
        assert (it["host"], it["port"]) == ("1.2.3.4", 8080)

    def test_crlf_input(self):
        items = parse_bulk_proxies(_P1 + "\r\n" + _P2 + "\r\n")
        assert [it["host"] for it in items] == ["1.2.3.4", "5.6.7.8"]

    def test_no_auth_line_yields_empty_credentials(self):
        (it,) = parse_bulk_proxies(_P2)
        assert it["username"] == "" and it["password"] == ""

    @pytest.mark.parametrize("scheme", list(ALLOWED_SCHEMES))
    def test_all_allowed_schemes_accepted(self, scheme):
        (it,) = parse_bulk_proxies(f"{scheme}://1.2.3.4:8080")
        assert it["scheme"] == scheme

    def test_scheme_case_normalized(self):
        (it,) = parse_bulk_proxies("SOCKS5://1.2.3.4:1080")
        assert it["scheme"] == "socks5"

    def test_hostname_case_normalized(self):
        (it,) = parse_bulk_proxies("http://Proxy.Example.COM:8080")
        assert it["host"] == "proxy.example.com"


class TestParseBulkProxiesRejects:
    """畸形行逐行拒收，且不阻断其余行"""

    def test_missing_scheme(self):
        (it,) = parse_bulk_proxies("1.2.3.4:8080")
        assert "协议前缀" in it["reason"] and "host" not in it

    def test_unsupported_scheme(self):
        (it,) = parse_bulk_proxies("ftp://1.2.3.4:21")
        assert "不支持" in it["reason"]

    def test_missing_port(self):
        (it,) = parse_bulk_proxies("http://1.2.3.4")
        assert "端口" in it["reason"]

    @pytest.mark.parametrize("bad", ["http://1.2.3.4:abc", "http://1.2.3.4:0", "http://1.2.3.4:65536"])
    def test_illegal_port(self, bad):
        (it,) = parse_bulk_proxies(bad)
        assert "端口" in it["reason"] and "host" not in it

    @pytest.mark.parametrize("ok", ["http://1.2.3.4:1", "http://1.2.3.4:65535"])
    def test_port_bounds_inclusive(self, ok):
        (it,) = parse_bulk_proxies(ok)
        assert it["port"] in (1, 65535)

    def test_missing_host(self):
        (it,) = parse_bulk_proxies("http://:8080")
        assert "地址" in it["reason"]

    def test_password_without_username(self):
        (it,) = parse_bulk_proxies("http://:pass@1.2.3.4:8080")
        assert "用户名" in it["reason"] and "host" not in it

    @pytest.mark.parametrize("bad", ["http://1.2.3.4:8080/path", "http://1.2.3.4:8080?x=1",
                                     "http://1.2.3.4:8080#frag"])
    def test_path_query_fragment_rejected(self, bad):
        (it,) = parse_bulk_proxies(bad)
        assert "路径" in it["reason"]

    def test_bare_trailing_slash_accepted(self):
        # 从浏览器地址栏复制过来常带一个尾斜杠，这个要容忍
        (it,) = parse_bulk_proxies("http://1.2.3.4:8080/")
        assert it["host"] == "1.2.3.4"

    def test_ipv6_literal_rejected(self):
        # build_proxy_url 拼回 URL 时不补方括号，收下就是存坏数据
        (it,) = parse_bulk_proxies("http://[2001:db8::1]:8080")
        assert "IPv6" in it["reason"] and "host" not in it

    def test_reason_never_echoes_the_line(self):
        # 跳过原因会进响应体，而原始行里带着密码明文，绝不能回显
        secret = "sup3rs3cr3t"
        for bad in (f"ftp://u:{secret}@1.2.3.4:21", f"http://u:{secret}@1.2.3.4:99999",
                    f"http://u:{secret}@1.2.3.4:8080/path", f"u:{secret}@1.2.3.4:8080"):
            (it,) = parse_bulk_proxies(bad)
            assert secret not in it["reason"], bad


class TestParseBulkProxiesCredentials:
    """凭据解析：@ / : / percent-encoding，并与 build_proxy_url 往返对称"""

    @pytest.mark.parametrize("raw,user,pwd", [
        ("http://u:p@ss@1.2.3.4:8080", "u", "p@ss"),        # 密码含裸 @：按最右 @ 切 userinfo
        ("http://u:p%40ss@1.2.3.4:8080", "u", "p@ss"),      # 密码含 %40 转义
        ("http://u:a:b@1.2.3.4:8080", "u", "a:b"),          # 密码含 :：按首个 : 切
        ("http://us%3Aer:pw@1.2.3.4:8080", "us:er", "pw"),  # 用户名含 %3A 转义
        ("http://Default:861298438g@194.55.15.86:10008", "Default", "861298438g"),
    ])
    def test_credentials_decoded(self, raw, user, pwd):
        (it,) = parse_bulk_proxies(raw)
        assert (it["username"], it["password"]) == (user, pwd)

    @pytest.mark.parametrize("raw", [
        "http://u:p@ss@1.2.3.4:8080",
        "http://us%3Aer:a:b@1.2.3.4:8080",
        "socks5://用户:密码@1.2.3.4:1080",
        "http://1.2.3.4:8080",
    ])
    def test_round_trip_through_build_proxy_url(self, raw):
        # 解析 → 拼装 → 再解析，凭据必须逐字不变（解析的 unquote 与拼装的 quote 对称）
        (a,) = parse_bulk_proxies(raw)
        rebuilt = build_proxy_url(a["scheme"], a["host"], a["port"], a["username"], a["password"])
        (b,) = parse_bulk_proxies(rebuilt)
        assert (b["username"], b["password"]) == (a["username"], a["password"])
        assert (b["scheme"], b["host"], b["port"]) == (a["scheme"], a["host"], a["port"])


class TestParseBulkProxiesDedupe:
    """批内查重：协议+地址+端口+用户名四元组"""

    def test_exact_duplicate_reports_first_line(self):
        items = parse_bulk_proxies(_P1 + "\n" + _P2 + "\n" + _P1)
        assert items[2] == {"line": 3, "reason": "与本批第 1 行重复"}

    def test_same_endpoint_same_user_different_password_is_duplicate(self):
        # 同端点同账号只是口令不同，几乎只会是粘贴错误，按重复处理
        items = parse_bulk_proxies("http://u:p1@1.2.3.4:8080\nhttp://u:p2@1.2.3.4:8080")
        assert "重复" in items[1]["reason"]

    def test_same_endpoint_different_user_is_not_duplicate(self):
        # 住宅代理常以用户名区分会话/出口，同 IP 端口不同账号是不同代理
        items = parse_bulk_proxies("http://u1:p@1.2.3.4:8080\nhttp://u2:p@1.2.3.4:8080")
        assert all("host" in it for it in items)

    def test_different_scheme_same_endpoint_is_not_duplicate(self):
        items = parse_bulk_proxies("http://1.2.3.4:8080\nsocks5://1.2.3.4:8080")
        assert all("host" in it for it in items)

    def test_trailing_slash_form_dedupes_against_plain_form(self):
        items = parse_bulk_proxies("http://1.2.3.4:8080\nhttp://1.2.3.4:8080/")
        assert "重复" in items[1]["reason"]


class TestContract:
    """源码契约守卫"""

    def _bulk_body(self) -> str:
        return _MODULE_SRC.split("async def bulk_create_proxies(")[1].split("\n\n\n")[0]

    def test_single_add_path_preserved(self):
        # 批量添加是新增能力，单个添加必须原样保留
        assert '@router.post("/proxies", tags=["管理员"])' in _MODULE_SRC
        assert "async def create_proxy(" in _MODULE_SRC

    def test_bulk_endpoint_registered(self):
        assert '@router.post("/proxies/bulk", tags=["管理员"])' in _MODULE_SRC
        assert "async def bulk_create_proxies(" in _MODULE_SRC

    def test_bulk_requires_admin(self):
        assert "await require_admin(request)" in self._bulk_body()

    def test_bulk_response_never_carries_password(self):
        body = self._bulk_body()
        created = body.split("created = [{")[1].split("for it in todo]")[0]
        assert '"password"' not in created and "password" not in created

    def test_bulk_line_cap_enforced(self):
        assert BULK_MAX_LINES > 0
        assert "BULK_MAX_LINES" in self._bulk_body()

    def test_bulk_dedupes_without_decrypting(self):
        # 代理的身份列（协议/地址/端口/用户名）是明文，查重不该动解密
        body = self._bulk_body()
        assert "SELECT name, scheme, host, port, username FROM proxies" in body
        assert "decrypt" not in body

    def test_bulk_encrypts_password_in_thread(self):
        body = self._bulk_body()
        assert "encrypt_proxy_secret(" in body
        assert "asyncio.to_thread(_encrypt_all)" in body

    def test_bulk_invalidates_proxy_pool(self):
        assert "proxy_pool.invalidate()" in self._bulk_body()

    def test_bulk_writes_one_audit_row_per_proxy(self):
        assert "insert_audit_many" in self._bulk_body()

    def test_bulk_reuses_name_generator(self):
        # 命名规则与上游密钥批量添加共用一个实现，避免两套序号逻辑
        assert "gen_bulk_names(req.name_prefix" in self._bulk_body()

    def test_default_name_prefix_present(self):
        assert BULK_PROXY_NAME_PREFIX
        assert f'BULK_PROXY_NAME_PREFIX = "{BULK_PROXY_NAME_PREFIX}"' in _MODULE_SRC
