"""上游密钥批量添加单元测试（gateway/app/admin_api.py 的纯函数与契约，不连库不联网）

批量添加：POST /gw/admin/upstreams/bulk，请求体 api_keys 为多行文本、每行一个密钥，
名称由后端按「前缀-序号」自动生成；单个添加 POST /gw/admin/upstreams 保持不变。

覆盖：
- parse_bulk_keys：行号、空行与 # 注释跳过、含空格/过短/过长的行、批内查重
- gen_bulk_names：序号续排、跳过已占用名、前缀清洗与兜底
- 契约守卫：单个添加路径必须保留；批量端点存在且不回传密钥明文；上限常量就位
"""
import os
import re
from pathlib import Path

import pytest

# 导入链上两处模块级强校验：database 要 PG_PASSWORD、admin_api 要管理员密码。
# 单测不连库不登录，仅提供占位值满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")
os.environ.setdefault("ACU_ADMIN_PASSWORD", "unit-test-placeholder")

from app.admin_api import (  # noqa: E402
    BULK_KEY_MAX_LEN,
    BULK_KEY_MIN_LEN,
    BULK_MAX_LINES,
    BULK_NAME_PREFIX,
    BULK_NAME_PREFIX_MAX,
    gen_bulk_names,
    parse_bulk_keys,
)

_REPO = Path(__file__).resolve().parent.parent
_MODULE_SRC = (_REPO / "gateway" / "app" / "admin_api.py").read_text(encoding="utf-8")

_K1 = "nvapi-aaaaaaaaaaaaaaaa"
_K2 = "nvapi-bbbbbbbbbbbbbbbb"


class TestParseBulkKeys:
    """多行文本 → 逐行结果"""

    def test_empty_text_yields_nothing(self):
        for raw in ("", "   ", "\n\n\t\n", None):
            assert parse_bulk_keys(raw) == []

    def test_line_numbers_count_blank_and_comment_lines(self):
        # 行号必须对应用户在输入框里看到的行，跳过的空行/注释行同样占号
        items = parse_bulk_keys("\n# 备注\n" + _K1 + "\n\n" + _K2)
        assert items == [
            {"line": 3, "api_key": _K1},
            {"line": 5, "api_key": _K2},
        ]

    def test_surrounding_whitespace_stripped(self):
        assert parse_bulk_keys("  " + _K1 + "\t")[0]["api_key"] == _K1

    def test_crlf_input(self):
        # Windows 粘贴过来的 \r\n 不能把 \r 带进密钥
        items = parse_bulk_keys(_K1 + "\r\n" + _K2 + "\r\n")
        assert [it["api_key"] for it in items] == [_K1, _K2]

    def test_inner_whitespace_rejected(self):
        (it,) = parse_bulk_keys("nvapi-aaaa nvapi-bbbb")
        assert "含空格" in it["reason"] and "api_key" not in it

    @pytest.mark.parametrize("bad", ["a" * (BULK_KEY_MIN_LEN - 1), "a" * (BULK_KEY_MAX_LEN + 1)])
    def test_length_bounds_rejected(self, bad):
        (it,) = parse_bulk_keys(bad)
        assert "长度" in it["reason"] and "api_key" not in it

    @pytest.mark.parametrize("ok_len", [BULK_KEY_MIN_LEN, BULK_KEY_MAX_LEN])
    def test_length_bounds_inclusive(self, ok_len):
        (it,) = parse_bulk_keys("a" * ok_len)
        assert it["api_key"] == "a" * ok_len

    def test_intra_batch_duplicate_reports_first_line(self):
        items = parse_bulk_keys(_K1 + "\n" + _K2 + "\n" + _K1)
        assert items[2] == {"line": 3, "reason": "与本批第 1 行重复"}

    def test_reason_never_echoes_the_key(self):
        # 跳过原因会进响应体，绝不能把密钥明文带出去
        (it,) = parse_bulk_keys(_K1 + " " + _K2)
        assert _K1 not in it["reason"] and _K2 not in it["reason"]


class TestGenBulkNames:
    """自动命名 {前缀}-{序号}"""

    def test_starts_from_one_on_empty_db(self):
        assert gen_bulk_names("nv", [], 3) == ["nv-01", "nv-02", "nv-03"]

    def test_continues_from_existing_max(self):
        # 库里最大是 nv-03 就从 04 续排，不复用中间空出的 02
        assert gen_bulk_names("nv", ["nv-01", "nv-03", "手工建的"], 2) == ["nv-04", "nv-05"]

    def test_other_prefix_does_not_shift_sequence(self):
        assert gen_bulk_names("nv", ["other-09"], 1) == ["nv-01"]

    def test_skips_taken_names(self):
        # nv-02 被占（比如手工建过），生成时跳过而不是重名
        assert gen_bulk_names("nv", ["nv-02"], 2) == ["nv-03", "nv-04"]

    def test_sequence_beyond_two_digits(self):
        assert gen_bulk_names("nv", ["nv-99"], 1) == ["nv-100"]

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_blank_prefix_falls_back_to_default(self, raw):
        assert gen_bulk_names(raw, [], 1) == [BULK_NAME_PREFIX + "-01"]

    def test_prefix_trimmed_and_capped(self):
        long_prefix = "x" * (BULK_NAME_PREFIX_MAX + 10)
        (name,) = gen_bulk_names("  " + long_prefix, [], 1)
        assert name == "x" * BULK_NAME_PREFIX_MAX + "-01"

    def test_regex_metachar_prefix_is_literal(self):
        # 前缀直接进正则，未转义会炸或误匹配
        assert gen_bulk_names("a.b", ["axb-07"], 1) == ["a.b-01"]

    def test_zero_count(self):
        assert gen_bulk_names("nv", ["nv-01"], 0) == []


class TestContract:
    """源码契约守卫"""

    def test_single_add_path_preserved(self):
        # 批量添加是新增能力，单个添加必须原样保留
        assert '@router.post("/upstreams", tags=["管理员"])' in _MODULE_SRC
        assert "async def create_upstream(" in _MODULE_SRC

    def test_bulk_endpoint_registered(self):
        assert '@router.post("/upstreams/bulk", tags=["管理员"])' in _MODULE_SRC
        assert "async def bulk_create_upstreams(" in _MODULE_SRC

    def test_bulk_requires_admin(self):
        body = _MODULE_SRC.split("async def bulk_create_upstreams(")[1]
        assert "await require_admin(request)" in body.split("\n\n\n")[0]

    def test_bulk_response_carries_only_masked_prefix(self):
        # 响应逐行回显名称与掩码前缀，绝不回传明文密钥
        body = _MODULE_SRC.split("async def bulk_create_upstreams(")[1].split("\n\n\n")[0]
        created = re.search(r"created = \[\{.*?for it in todo\]", body, re.S)
        assert created, "未找到批量创建结果的响应体构造"
        assert '"api_key"' not in created.group(0)
        assert 'mask_secret(it["api_key"])' in body

    def test_bulk_line_cap_enforced(self):
        assert BULK_MAX_LINES > 0
        assert "BULK_MAX_LINES" in _MODULE_SRC.split("async def bulk_create_upstreams(")[1]

    def test_bulk_dedupes_against_existing_keys(self):
        # Fernet 密文带随机 IV，同一明文两次加密不同，查重只能解密后比对
        body = _MODULE_SRC.split("async def bulk_create_upstreams(")[1]
        assert "decrypt_upstream_key(" in body
        assert "库中已存在相同密钥" in body

    def test_bulk_invalidates_caches(self):
        body = _MODULE_SRC.split("async def bulk_create_upstreams(")[1].split("\n\n\n")[0]
        assert "invalidate_key_cache(" in body
        assert "proxy_pool.invalidate()" in body

    def test_bulk_writes_one_audit_row_per_key(self):
        body = _MODULE_SRC.split("async def bulk_create_upstreams(")[1].split("\n\n\n")[0]
        assert "insert_audit_many" in body
