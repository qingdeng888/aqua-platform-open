"""
统一异常体系 - v10.0 对标 litellm

litellm 所有异常继承自 openai.*Error，所以我们继承自 HTTPException：
- 下游 SDK / Agent 框架可直接用 except 捕获
- 统一的 .to_dict() 序列化为 OpenAI 兼容格式
- 完整的错误链追踪
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from typing import Optional


class AquaError(HTTPException):
    """AQUA 统一异常基类（继承 FastAPI HTTPException，确保中间件正确捕获）"""
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        code: str = "internal_error",
        type: str = "server_error",
        provider: Optional[str] = None,
        headers: Optional[dict] = None,
    ):
        super().__init__(status_code=status_code, detail=self._build_detail(message, code, type))
        self.message = message
        self.code = code
        self.error_type = type
        self.provider = provider
        self.custom_headers = headers or {}

    def _build_detail(self, message: str, code: str, type: str) -> dict:
        """OpenAI 兼容错误格式"""
        return {
            "error": {
                "message": message,
                "type": type,
                "code": code,
            }
        }

    def to_openai_error(self) -> dict:
        """序列化为 OpenAI 兼容错误响应"""
        return self.detail

    def to_response(self) -> JSONResponse:
        """生成 FastAPI JSONResponse"""
        headers = {}
        if self.provider:
            headers["X-Error-Provider"] = self.provider
        headers["X-Error-Code"] = self.code
        return JSONResponse(
            status_code=self.status_code,
            content=self.to_openai_error(),
            headers={**headers, **self.custom_headers},
        )


# ========== 具体异常类型（参照 litellm 异常层次） ==========

class AuthenticationError(AquaError):
    """认证失败 (401)"""
    def __init__(self, message: str = "认证失败", code: str = "authentication_error"):
        super().__init__(message, 401, code, "authentication_error")


class RateLimitError(AquaError):
    """速率限制 (429)"""
    def __init__(self, message: str = "请求过于频繁", retry_after: int = 5, code: str = "rate_limit_exceeded"):
        super().__init__(message, 429, code, "rate_limit_error",
                         headers={"Retry-After": str(retry_after)})


class BadRequestError(AquaError):
    """请求参数错误 (400)"""
    def __init__(self, message: str = "请求参数错误", code: str = "bad_request"):
        super().__init__(message, 400, code, "bad_request")


class NotFoundError(AquaError):
    """资源不存在 (404)"""
    def __init__(self, message: str = "资源不存在", code: str = "not_found"):
        super().__init__(message, 404, code, "not_found")


class InternalServerError(AquaError):
    """服务器内部错误 (500)"""
    def __init__(self, message: str = "服务器内部错误", code: str = "internal_error", provider: str = ""):
        super().__init__(message, 500, code, "server_error", provider=provider)


class ContextWindowExceededError(BadRequestError):
    """上下文窗口超限 - 对标 litellm ContextWindowExceededError"""
    def __init__(self, message: str = "上下文长度超过模型限制", code: str = "context_length_exceeded"):
        super().__init__(message, code)


class InsufficientQuotaError(AquaError):
    """配额不足 (402)"""
    def __init__(self, message: str = "配额不足", code: str = "insufficient_quota"):
        super().__init__(message, 402, code, "insufficient_quota")


class UpstreamServiceUnavailable(AquaError):
    """上游服务不可用 (503)"""
    def __init__(self, message: str = "上游服务暂不可用", provider: str = "", code: str = "upstream_unavailable"):
        super().__init__(message, 503, code, "upstream_error", provider=provider)


# ========== 错误中间件注册函数 ==========

# ========== 错误码定义（供前端控制台展示） ==========

ERROR_CODE_DEFINITIONS = {
    "400": {
        "title": "请求格式错误",
        "description": "请求参数格式不正确，服务器无法解析您的请求。",
        "common_causes": [
            "JSON格式错误（花括号/引号不匹配）",
            "缺少必填参数（如 model、messages）",
            "messages 数组中缺少 role 或 content 字段",
            "模型ID拼写错误或不存在",
        ],
        "solutions": [
            "检查请求体是否为有效的JSON格式",
            "确保包含 model 和 messages 参数",
            "确保 messages 数组中的每条消息都包含 role 和 content",
            "在模型列表页面查看可用的模型ID",
        ],
        "code_examples": {
            "invalid_json": {"message": "请求体JSON解析失败", "type": "invalid_request_error"},
            "missing_model": {"message": "缺少model参数", "type": "invalid_request_error"},
            "invalid_model": {"message": "模型ID无效", "type": "invalid_request_error"},
        },
    },
    "401": {
        "title": "认证失败",
        "description": "API密钥无效或未提供有效的认证信息。",
        "common_causes": [
            "API密钥为空或格式不正确",
            "API密钥已过期或已被吊销",
            "API Key 中包含了多余的空格或换行符",
            "使用了测试密钥（包含 test/demo 等字样）",
        ],
        "solutions": [
            "检查 Authorization 请求头格式：Bearer acu_xxx",
            "确保API密钥以 acu_ 开头且无多余空格",
            "在控制台重新生成API密钥",
            "检查密钥状态是否为 active",
        ],
        "code_examples": {
            "invalid_api_key": {"message": "无效的API密钥", "type": "invalid_request_error"},
            "expired_token": {"message": "会话已过期，请重新登录", "type": "unauthorized"},
        },
    },
    "403": {
        "title": "访问被拒绝",
        "description": "您没有权限访问此资源或上游模型。",
        "common_causes": [
            "上游API密钥被拒绝访问请求的模型",
            "模型已被禁用或限制访问",
            "账户被系统封禁",
        ],
        "solutions": [
            "检查您使用的模型ID是否正确",
            "联系管理员确认您的账户状态",
            "等待一段时间后重试",
        ],
        "code_examples": {
            "upstream_access_denied": {"message": "上游AI模型访问被拒绝", "type": "permission_error"},
        },
    },
    "429": {
        "title": "请求频率过高",
        "description": "您的请求速率超过了系统限制，需要降低请求频率。",
        "common_causes": [
            "并发请求数超过账户限制（老用户4个/新用户2个）",
            "上游AI模型的速率限制",
            "短时间内的突发请求过多",
        ],
        "solutions": [
            "减少并发请求数量，等待当前请求完成后再发起新请求",
            "降低请求频率，在请求之间添加适当间隔",
            "对于流式请求，确保正确关闭连接",
            "如需要更高并发，请联系管理员",
        ],
        "code_examples": {
            "concurrency_limit_exceeded": {"message": "账号并发请求数已达上限", "type": "rate_limit_error"},
            "upstream_rate_limited": {"message": "上游AI模型限流", "type": "rate_limit_exceeded"},
            "model_rate_limited": {"message": "模型暂时限流中", "type": "rate_limit_exceeded"},
        },
    },
    "502": {
        "title": "上游服务不可用",
        "description": "无法连接到上游AI模型服务。系统会自动尝试切换备用密钥。",
        "common_causes": [
            "上游AI模型服务临时故障或维护中",
            "网络连接故障（如DNS解析失败、连接被重置）",
            "网关服务重启或负载过高",
        ],
        "solutions": [
            "请稍后重试，上游服务通常会快速恢复",
            "切换使用其他模型ID",
            "如果问题持续，请联系管理员检查上游服务状态",
        ],
        "code_examples": {
            "upstream_connection_failed": {"message": "无法连接到上游AI模型服务", "type": "connection_error"},
            "max_retries_exceeded": {"message": "上游服务错误次数过多", "type": "upstream_error"},
        },
    },
    "503": {
        "title": "服务暂不可用",
        "description": "网关系统暂时无法处理您的请求。",
        "common_causes": [
            "所有上游密钥暂时都在冷却中",
            "模型级别错误熔断（短时间内错误过多）",
            "系统资源不足（内存/连接池耗尽）",
        ],
        "solutions": [
            "稍等几秒后重试",
            "尝试使用其他模型",
            "如果问题持续，请联系管理员",
        ],
        "code_examples": {
            "all_keys_exhausted": {"message": "所有上游密钥暂不可用", "type": "service_unavailable"},
            "model_5xx_circuit_broken": {"message": "模型上游服务暂时不可用", "type": "service_unavailable"},
        },
    },
    "504": {
        "title": "网关超时",
        "description": "请求处理时间超过系统限制，连接已被关闭。",
        "common_causes": [
            "模型推理时间过长",
            "响应数据量过大导致传输超时",
            "上游服务响应缓慢",
        ],
        "solutions": [
            "使用推理速度更快的模型",
            "减少 max_tokens 参数值",
            "开启流式响应（stream=true）以获得更快的首字节体验",
        ],
        "code_examples": {
            "upstream_timeout": {"message": "上游AI模型响应超时", "type": "timeout_error"},
        },
    },
    "524": {
        "title": "上游响应超时",
        "description": "连接已建立但上游AI模型在指定时间内未完成响应。",
        "common_causes": [
            "模型推理时间过长（超过180秒）",
            "上游服务负载过高，请求排队时间过长",
            "复杂推理任务需要更长的处理时间",
        ],
        "solutions": [
            "切换到推理速度更快的模型",
            "简化请求内容（减少上下文长度）",
            "降低 temperature 值以获得更确定性的响应",
            "使用流式模式以实时获取部分响应",
        ],
        "code_examples": {
            "upstream_timeout": {"message": "上游AI模型响应超时", "type": "timeout_error"},
        },
    },
}


def register_error_handlers(app):
    """注册全局错误处理器 - 类似 litellm proxy 的错误拦截"""
    @app.exception_handler(AquaError)
    async def aqua_error_handler(request, exc: AquaError):
        return exc.to_response()

    @app.exception_handler(Exception)
    async def generic_error_handler(request, exc: Exception):
        import logging
        logger = logging.getLogger("acu.errors")
        logger.exception(f"未捕获异常: {exc}")
        return InternalServerError(str(exc)[:200]).to_response()
