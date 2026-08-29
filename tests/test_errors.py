"""
统一异常体系测试 - 对标 litellm 异常测试
"""
import pytest
from fastapi.responses import JSONResponse

from app.errors_v2 import (
    AquaError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)


class TestAquaErrors:
    """AQUA 统一异常体系测试"""

    def test_authentication_error(self):
        err = AuthenticationError("自定义错误")
        assert err.status_code == 401
        assert err.code == "authentication_error"
        detail = err.to_openai_error()
        assert detail["error"]["message"] == "自定义错误"
        assert detail["error"]["type"] == "authentication_error"

    def test_rate_limit_error_headers(self):
        err = RateLimitError(retry_after=10)
        assert err.status_code == 429
        assert err.custom_headers["Retry-After"] == "10"
        resp = err.to_response()
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 429

    def test_to_response(self):
        err = InternalServerError("测试错误", provider="nvidia")
        resp = err.to_response()
        assert resp.headers["X-Error-Provider"] == "nvidia"
        assert resp.headers["X-Error-Code"] == "internal_error"

    def test_error_inheritance(self):
        """验证异常层次：具体异常可被父类捕获"""
        errors = [
            BadRequestError("bad"),
            NotFoundError("404"),
            RateLimitError("429"),
            AuthenticationError("401"),
        ]
        for err in errors:
            assert isinstance(err, AquaError)
            assert isinstance(err.to_openai_error(), dict)
