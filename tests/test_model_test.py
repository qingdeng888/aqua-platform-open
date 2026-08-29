"""
模型测试模块单元测试（gateway/app/model_test.py，纯函数与契约，不连数据库/网络）

覆盖：
- normalize_prompt：空/纯空白回落默认提示词、超长截断
- clamp_max_tokens：非法值取 DEFAULT_MAX_TOKENS、越界收敛到 [1, 512]
- extract_reply：content / reasoning_content / text 三态与异常结构
- extract_error：error 对象 / error 字符串 / detail / raw 与兜底空串
- 路由与常量契约：prefix、四条端点路径、默认提示词字面量、自测客户名
- ProbeRequest：extra="forbid"（拼错字段直接报错而非静默忽略）
- 源码守卫：探测响应不得回传代理 URL（内嵌账号密码）
"""
import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

# 导入链上两处模块级强校验：database 要 PG_PASSWORD、admin_api 要管理员密码哈希。
# 单测不连库不登录，仅提供占位值满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")
os.environ.setdefault(
    "ACU_ADMIN_PASSWORD_HASH",
    "$2b$12$abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTU",
)

from app.model_test import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEST_PROMPT,
    MAX_TOKENS_LIMIT,
    PROMPT_MAX_CHARS,
    REPLY_MAX_CHARS,
    SELFTEST_CLIENT_NAME,
    ProbeRequest,
    clamp_max_tokens,
    extract_error,
    extract_reply,
    normalize_prompt,
    router,
)

_MODULE_SRC = (Path(__file__).resolve().parent.parent / "gateway" / "app" / "model_test.py").read_text(encoding="utf-8")


class TestNormalizePrompt:
    """提示词归一"""

    @pytest.mark.parametrize("raw", [None, "", "   ", "\n\t "])
    def test_empty_falls_back_to_default(self, raw):
        assert normalize_prompt(raw) == DEFAULT_TEST_PROMPT

    def test_keeps_custom_prompt_trimmed(self):
        assert normalize_prompt("  你好呀  ") == "你好呀"

    def test_truncates_overlong_prompt(self):
        assert len(normalize_prompt("啊" * (PROMPT_MAX_CHARS + 500))) == PROMPT_MAX_CHARS


class TestClampMaxTokens:
    """max_tokens 收敛"""

    @pytest.mark.parametrize("bad", [None, "", "abc", object()])
    def test_invalid_defaults_to_default_budget(self, bad):
        # 非法值回落默认输出预算（256）：推理模型的思维链会先吃掉配额，太小拿不到正文
        assert clamp_max_tokens(bad) == DEFAULT_MAX_TOKENS
        assert DEFAULT_MAX_TOKENS == 256

    @pytest.mark.parametrize("raw,expect", [(0, 1), (-9, 1), (1, 1), (128, 128), ("32", 32)])
    def test_within_range(self, raw, expect):
        assert clamp_max_tokens(raw) == expect

    def test_upper_bound(self):
        assert clamp_max_tokens(99999) == MAX_TOKENS_LIMIT


class TestExtractReply:
    """回复摘要提取"""

    def test_regular_content(self):
        data = {"choices": [{"message": {"content": " 我是模型 "}}]}
        assert extract_reply(data) == "我是模型"

    def test_falls_back_to_reasoning_content(self):
        # 推理模型可能只给 reasoning_content，此时不能判成「无回复」
        data = {"choices": [{"message": {"content": "", "reasoning_content": "思考中"}}]}
        assert extract_reply(data) == "思考中"

    def test_falls_back_to_text(self):
        assert extract_reply({"choices": [{"text": "补全结果"}]}) == "补全结果"

    @pytest.mark.parametrize("data", [
        None, {}, "not-a-dict", {"choices": []}, {"choices": "x"},
        {"choices": [{"message": {"content": "   "}}]},
    ])
    def test_empty_or_malformed(self, data):
        assert extract_reply(data) == ""

    def test_truncated(self):
        data = {"choices": [{"message": {"content": "字" * (REPLY_MAX_CHARS + 100)}}]}
        assert len(extract_reply(data)) == REPLY_MAX_CHARS


class TestExtractError:
    """错误信息提取（上游错误结构不统一）"""

    def test_error_object_message(self):
        assert extract_error({"error": {"message": "模型不存在"}}) == "模型不存在"

    def test_error_object_type_fallback(self):
        assert extract_error({"error": {"type": "invalid_request"}}) == "invalid_request"

    def test_error_string(self):
        assert extract_error({"error": "Bad Gateway"}) == "Bad Gateway"

    def test_detail_and_raw(self):
        assert extract_error({"detail": "未授权"}) == "未授权"
        assert extract_error({"raw": "<html>502</html>"}) == "<html>502</html>"

    @pytest.mark.parametrize("data", [None, {}, "x", {"error": {}}, {"detail": "  "}])
    def test_empty(self, data):
        assert extract_error(data) == ""


class TestContract:
    """路由与常量契约（前端与文档都按这套路径/文案对齐）"""

    def test_default_prompt_literal(self):
        assert DEFAULT_TEST_PROMPT == "你是什么模型，你可以帮我干什么事情"

    def test_selftest_client_name_marked_internal(self):
        # 双下划线前缀标识「非真实客户」，避免与真实客户混淆
        assert SELFTEST_CLIENT_NAME == "__console_selftest__"
        assert SELFTEST_CLIENT_NAME.startswith("__")

    def test_router_paths(self):
        paths = {r.path for r in router.routes}
        assert paths == {
            "/gw/admin/model-test/models",
            "/gw/admin/model-test/selftest-key",
            "/gw/admin/model-test/selftest-key/rotate",
            "/gw/admin/model-test/probe",
        }

    def test_all_endpoints_require_admin(self):
        # 四个端点都必须先过 require_admin（管理员 Token）
        assert _MODULE_SRC.count("await require_admin(request)") == len(router.routes)


class TestProbeRequest:
    """探测请求体校验"""

    def test_defaults(self):
        req = ProbeRequest(model="openai/gpt-oss-20b")
        assert req.prompt is None and req.max_tokens == DEFAULT_MAX_TOKENS

    def test_rejects_unknown_field(self):
        # extra="forbid"：拼错字段直接 422，而不是静默按默认值跑
        with pytest.raises(ValidationError):
            ProbeRequest(model="m", maxTokens=8)

    def test_model_required(self):
        with pytest.raises(ValidationError):
            ProbeRequest()


class TestSecurityGuards:
    """源码守卫：测试通道不得外泄凭据"""

    def test_no_proxy_url_in_response(self):
        # 代理 URL 内嵌账号密码，探测结果只能回 direct/proxy 两态
        assert "proxy_url" not in _MODULE_SRC
        assert re.search(r'egress\s*=\s*"proxy"\s+if\s+await\s+proxy_pool\.resolve_url', _MODULE_SRC)

    def test_upstream_key_masked_only(self):
        # 上游密钥只以掩码形式回前端
        assert '"key_masked": mask_secret(api_key)' in _MODULE_SRC
        assert '"api_key": api_key' not in _MODULE_SRC

    def test_selftest_key_writes_audit(self):
        # 密钥下发/轮换必须留审计痕迹（批量探测本身不写审计，避免刷爆 audit_logs）
        assert _MODULE_SRC.count("insert_audit") == 3   # 1 处 import + 2 处调用
        assert "invalidate_client_key_cache()" in _MODULE_SRC


