"""
中间件模块 - CC Switch适配、维护模式、CORS、请求大小限制、全量请求日志

关键设计：
- 维护模式：全局原子布尔值，热更新（先切换调度器再切换维护标志）
- CC Switch适配：识别User-Agent，标准化SSE流式响应
- OpenAI错误格式：/v1/和/api/v1/路径返回 {"error": {"message", "type", "code"}}
-  全量请求日志：中间件层捕获100%请求，含请求体/响应体/错误详情
"""
import asyncio
import json
import os
import re
import time
import traceback
import uuid
import logging
from app.error_tracker import track_error
from app.security import hash_secret
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from fastapi import Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("acu.middleware")

# v10.0 修复：客户端名称内存缓存（避免中间件中每请求同步DB查询阻塞事件循环）
_client_name_cache: Dict[str, tuple] = {}
_client_name_cache_ts: Dict[str, float] = {}
_CLIENT_NAME_CACHE_TTL = 60.0  # 60秒过期

# ========== 维护模式全局原子标志 ==========
# 热更新流程：先切换调度器到维护模式（拒绝新请求排队），再切换此标志
_maintenance_mode = False


def is_maintenance_mode() -> bool:
    return _maintenance_mode


def set_maintenance_mode(enabled: bool) -> None:
    global _maintenance_mode
    _maintenance_mode = enabled


# ========== CC Switch 适配 ==========
# 识别CC Switch / Claude Code / Codex 等IDE工具的User-Agent
CC_SWITCH_MARKERS = ("claude-cli", "cc-switch", "codex", "copilot", "cursor")


def is_cc_switch_request(request: Request) -> bool:
    """检测是否为CC Switch等IDE工具请求"""
    ua = request.headers.get("user-agent", "").lower()
    return any(marker in ua for marker in CC_SWITCH_MARKERS)


# ========== 请求大小限制中间件 ==========

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """限制请求体大小（10MB）——按实际读取字节强制，防chunked绕过content-length"""
    MAX_BYTES = 10 * 1024 * 1024
    # 分块读取粒度（ASGI 服务端分块通常 <= 64KB，逐块累计校验，超限即中断）
    CHUNK_SIZE = 64 * 1024

    def _payload_too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"error": {
                "message": "请求体过大，最大10MB",
                "type": "invalid_request_error",
                "code": "payload_too_large",
            }},
        )

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            transfer_encoding = (request.headers.get("transfer-encoding") or "").lower()
            if content_length and "chunked" not in transfer_encoding:
                # 有 content-length 且非 chunked：仅校验头（保持现状）
                try:
                    if int(content_length) > self.MAX_BYTES:
                        return self._payload_too_large()
                except ValueError:
                    # content-length 畸形 → 400 而非 500
                    return JSONResponse(
                        status_code=400,
                        content={"error": {
                            "message": "Content-Length 请求头格式非法",
                            "type": "invalid_request_error",
                            "code": "invalid_content_length",
                        }},
                    )
            else:
                # 无 content-length 或 Transfer-Encoding: chunked：按实际读取字节强制
                # （逐块累计，累计到 MAX_BYTES+1 即中断返回 413，不再信任头声明）
                chunks = []
                total = 0
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.MAX_BYTES:
                        return self._payload_too_large()
                    chunks.append(chunk)
                # 未超限：把已读字节缓存到 request._body（Starlette 会复用），下游无需重复读流
                request._body = b"".join(chunks)
        return await call_next(request)


# ========== 维护模式中间件 ==========

class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """维护模式检查 - 非管理端点返回503"""

    # 允许在维护模式下访问的路径前缀
    ALLOWED_PREFIXES = (
        "/gw/admin/",
        "/admin",
        "/healthz",
        "/static/",
    )

    async def dispatch(self, request: Request, call_next):
        if not is_maintenance_mode():
            return await call_next(request)

        path = request.url.path
        for prefix in self.ALLOWED_PREFIXES:
            if path.startswith(prefix) or path == prefix:
                return await call_next(request)

        # 维护模式：非管理端点返回503
        return JSONResponse(
            status_code=503,
            content={"error": {
                "message": "系统维护中，请稍后再试",
                "type": "service_unavailable",
                "code": "maintenance_mode",
            }},
        )


# ========== IP速率限制器 ==========

class IPRateLimiter:
    """简单的IP速率限制器（用于登录等敏感端点）

    内部使用 collections.deque(maxlen=200) 实现 O(1) 过期清理，
    通过 max_ips 限制防止内存溢出。
    """
    def __init__(self, max_requests: int = 10, window_seconds: int = 60, max_ips: int = 10000):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_ips = max_ips
        self._requests: Dict[str, deque] = {}
        self._last_cleanup = time.time()

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        if now - self._last_cleanup > 300:
            self.cleanup()
            self._last_cleanup = now
        cutoff = now - self.window_seconds
        if ip not in self._requests:
            if len(self._requests) >= self.max_ips:
                return False
            self._requests[ip] = deque(maxlen=200)
        timestamps = self._requests[ip]
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        if len(timestamps) >= self.max_requests:
            return False
        timestamps.append(now)
        return True

    def cleanup(self):
        """清理所有超时IP条目"""
        now = time.time()
        cutoff = now - self.window_seconds
        for ip in list(self._requests.keys()):
            timestamps = self._requests[ip]
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()
            if not timestamps:
                del self._requests[ip]


_login_rate_limiter = IPRateLimiter(max_requests=10, window_seconds=60)
_admin_rate_limiter = IPRateLimiter(max_requests=60, window_seconds=60)


# ========== 异步批量日志写入器 ==========
#
# 使用 asyncio.Queue 收集请求日志，后台协程批量写入异步DB池。
# start_log_worker() 必须在 main.py 的 lifespan 中调用。

_log_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
_log_worker_task: Optional[asyncio.Task] = None


def start_log_worker():
    """启动后台批量日志写入协程"""
    global _log_worker_task
    if _log_worker_task is None or _log_worker_task.done():
        _log_worker_task = asyncio.create_task(_log_worker())


async def stop_log_worker():
    """停止后台批量日志写入协程，并刷新剩余日志"""
    global _log_worker_task
    if _log_worker_task and not _log_worker_task.done():
        _log_worker_task.cancel()
        try:
            await _log_worker_task
        except asyncio.CancelledError:
            pass
        _log_worker_task = None


async def _flush_logs(batch):
    """批量写入日志到数据库（静默降级）"""
    if not batch:
        return
    try:
        from app.db_async import async_session_factory
        from sqlalchemy import text

        from app.database import localnow
        now_str = localnow()

        async with async_session_factory() as session:
            for item in batch:
                # v10.1修复：token 字段——条目携带则用真实值，否则写 NULL（不再硬编码0）
                await session.execute(
                    text("""INSERT INTO request_logs
                           (id, client_id, upstream_key_id, model, status_code, latency_ms, latency_us,
                            is_stream, error_msg, created_at, started_at, completed_at,
                            request_path, http_method, client_ip, user_agent,
                            request_params, request_body, response_body,
                            error_type, error_detail, error_stack, business_code, log_category, retried,
                            prompt_tokens, completion_tokens, total_tokens)
                           VALUES (:id, :client_id, :upstream_key_id, :model, :status_code, :latency_ms, :latency_us,
                                   :is_stream, :error_msg, :created_at, :started_at, :completed_at,
                                   :request_path, :http_method, :client_ip, :user_agent,
                                   :request_params, :request_body, :response_body,
                                   :error_type, :error_detail, :error_stack, :business_code, :log_category, 0,
                                   :prompt_tokens, :completion_tokens, :total_tokens)"""),
                    {
                        **item,
                        "created_at": now_str,
                        "prompt_tokens": item.get("prompt_tokens"),
                        "completion_tokens": item.get("completion_tokens"),
                        "total_tokens": item.get("total_tokens"),
                    },
                )
            await session.commit()
    except ImportError:
        # 异步DB池未配置（db_async.py 不存在或 SQLAlchemy 未安装），静默跳过
        pass
    except Exception:
        # 静默处理写入异常，不干扰主请求流程
        pass


async def _log_worker():
    """后台批量日志写入协程 - 每2秒或每50条刷新一次"""
    batch = []
    while True:
        try:
            # 等待最多2秒获取第一条日志（空闲时实现定期刷新）
            try:
                item = await asyncio.wait_for(_log_queue.get(), timeout=2.0)
                batch.append(item)
            except asyncio.TimeoutError:
                if batch:
                    await _flush_logs(batch)
                    batch = []
                continue

            # 非阻塞收集剩余日志（最多50条凑一批）
            while len(batch) < 50:
                try:
                    batch.append(_log_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # 批量写入
            await _flush_logs(batch)
            batch = []

        except asyncio.CancelledError:
            # 关闭时刷新剩余日志，防止数据丢失
            if batch:
                await _flush_logs(batch)
            raise
        except Exception:
            # 防止单个批次的异常导致worker退出
            batch = []


def _parse_ip(value: str):
    """解析IP字符串，非法（不可信）返回None"""
    try:
        import ipaddress
        return ipaddress.ip_address(value)
    except Exception:
        return None


def get_client_ip(request: Request) -> str:
    """获取客户端真实IP（v10.1修复：可信代理模型）

    仅当对端（request.client.host）为私网/回环（即经可信反向代理接入）时才信任代理头；
    公网直连时 CF-Connecting-IP / X-Forwarded-For 等头可被任意伪造，一律忽略。
    AQUA_TRUST_PROXY_HEADERS=1 可强制信任代理头（CF 全程代理场景），默认 auto。
    """
    peer = request.client.host if request.client else "unknown"

    # 强制信任开关（CF全程代理等场景）；默认 auto：按对端是否私网判断
    force_trust = os.environ.get("AQUA_TRUST_PROXY_HEADERS", "").strip().lower() in ("1", "true", "yes")
    if not force_trust and not _is_private_ip(peer):
        # 公网直连：忽略所有可伪造头，直接返回对端IP
        return peer

    # --- 以下仅在对端为可信反向代理（或强制信任）时执行 ---
    # 1. CF-Connecting-IP：Cloudflare 追加的真实客户端IP（取值且须为合法IP）
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip and _parse_ip(cf_ip) is not None:
        return cf_ip

    # 2. X-Forwarded-For：取最右一个非私网值（最右侧由可信代理追加，攻击者无法在其右侧插值）
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        for ip in reversed([x.strip() for x in forwarded.split(",")]):
            parsed = _parse_ip(ip)
            if parsed is None or parsed.is_private or parsed.is_loopback or parsed.is_reserved:
                continue
            return ip

    # 3. 头中解析不出可信IP → 回退对端直连IP
    return peer


def _is_private_ip(ip: str) -> bool:
    """判断是否内网/回环/保留IP（127.x、10.x、172.16-31.x、192.168.x、::1、fc00::/7 等）

    解析失败一律按"不可信"处理：返回 False → 对端不视为可信代理，不信任转发头。
    """
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_reserved
    except Exception:
        return False


def setup_middleware(app):
    """注册所有中间件到FastAPI应用"""
    # CORS - 白名单域名，避免 * 与 credentials 同时使用
    _cors_origins = os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:8001,http://localhost:8000"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 注意：Starlette 后 add 的中间件位于外层、先执行
    # 实际执行顺序：MaintenanceMode → RequestSizeLimit → RequestLogging → CORS
    # （SizeLimit 必须在 RequestLogging 外层：先按实际字节截断超大/chunked请求体，日志层才限量读前4KB）
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(MaintenanceModeMiddleware)


# ========== 全量请求日志中间件 ==========

# 不记录日志的路径（健康检查、静态文件等无需记录的路径）
_LOG_SKIP_PREFIXES = ("/healthz", "/static/", "/gw/static/", "/favicon.ico", "/robots.txt")
# 由业务逻辑(_log_request)完整记录的API路径 - 中间件跳过以避免重复
_LOG_BUSINESS_PREFIXES = ("/v1/chat/completions", "/api/v1/chat/completions", "/v1/embeddings", "/api/v1/embeddings")
# 需要记录请求体的路径前缀
_LOG_BODY_PREFIXES = ("/v1/", "/api/v1/")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """全量请求日志中间件 - 捕获100%通过网关的请求

    记录内容：
    - 完整请求路径、HTTP方法、客户端IP、User-Agent
    - URL查询参数、请求体（限API路径）
    - 响应状态码、响应体（限非流式且非过大）
    - 精确处理时间（毫秒级）
    - 错误类型/详情/堆栈（异常请求）
    - 日志分类：normal / error / auth_fail
    """

    # 响应体最大记录长度（避免内存爆炸）
    MAX_RESPONSE_BODY_LEN = 65536  # 64KB（读取上限）
    LOG_BODY_READ_LIMIT = 4096     # 请求体日志读取上限4KB（限量读，不为日志物化整个body）
    # 存储时截断长度（优化：避免DB膨胀）
    STORE_REQUEST_BODY_LEN = 4096   # 请求体存储上限4KB
    STORE_RESPONSE_BODY_LEN = 2048  # 响应体存储上限2KB
    STORE_ERROR_STACK_LEN = 2048    # 错误堆栈存储上限2KB

    @staticmethod
    async def _read_body_limited(request: Request, limit: int):
        """限量读取请求体：返回 (前limit字节, 实际总字节数)

        - 若外层中间件已缓存 request._body（chunked/无content-length场景），直接截取前缀；
        - 否则从流读取：为保下游能完整重放（Starlette 会复用 request._body），
          收干全部字节后缓存到 request._body（总量已由外层 SizeLimit 限制在10MB内），
          日志仅保留前 limit 字节。
        """
        cached = getattr(request, "_body", None)
        if cached is not None:
            return cached[:limit], len(cached)
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
        request._body = bytes(buf)  # 缓存供下游完整重放
        return bytes(buf[:limit]), len(buf)

    @staticmethod
    def _sanitize_request_body(body: str) -> str:
        """脱敏请求体（v10.1修复：此前方法缺失导致请求体日志全部变为"[读取失败]"）

        messages/input/contents 数组内容替换为 "[REDACTED len=N]"（保留 role/数量结构），
        序列化后截断4KB；JSON解析失败返回原文前4KB。
        """
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body[:RequestLoggingMiddleware.STORE_REQUEST_BODY_LEN]
        if isinstance(data, dict):
            for field in ("messages", "input", "contents"):
                items = data.get(field)
                if isinstance(items, list):
                    data[field] = [RequestLoggingMiddleware._redact_message_item(it) for it in items]
        try:
            return json.dumps(data, ensure_ascii=False)[:RequestLoggingMiddleware.STORE_REQUEST_BODY_LEN]
        except (TypeError, ValueError):
            return body[:RequestLoggingMiddleware.STORE_REQUEST_BODY_LEN]

    @staticmethod
    def _redact_message_item(item):
        """单条消息脱敏：保留 role/type/name 结构字段，正文替换为长度占位符"""
        if isinstance(item, dict):
            redacted = {k: v for k, v in item.items() if k in ("role", "type", "name")}
            content = item.get("content", item.get("text", item.get("parts")))
            redacted["content"] = f"[REDACTED len={len(content) if isinstance(content, (str, list, dict)) else 0}]"
            return redacted
        if isinstance(item, str):
            return f"[REDACTED len={len(item)}]"
        if isinstance(item, (list, dict)):
            return f"[REDACTED len={len(json.dumps(item, ensure_ascii=False, default=str))}]"
        return "[REDACTED len=0]"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 跳过不需要记录的路径
        for prefix in _LOG_SKIP_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # 跳过已由业务逻辑完整记录的API路径（避免重复日志）
        for prefix in _LOG_BUSINESS_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # 跳过OPTIONS请求（CORS预检，无业务意义）
        if request.method == "OPTIONS":
            return await call_next(request)

        start_ts = time.time()
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone(timedelta(hours=8)))

        # 采集请求信息
        http_method = request.method
        client_ip = get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")[:512]
        query_params = str(request.query_params) if request.query_params else ""

        # 采集请求体（仅API路径，限量读前4KB用于日志，不物化整个body）
        request_body = ""
        if any(path.startswith(p) for p in _LOG_BODY_PREFIXES) and request.method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes, body_total = await self._read_body_limited(request, self.LOG_BODY_READ_LIMIT)
                if body_bytes and body_total > self.LOG_BODY_READ_LIMIT:
                    request_body = f"[请求体过大: {body_total} bytes]"
                elif body_bytes:
                    request_body = body_bytes.decode("utf-8", errors="replace")
                    # v10.0: 脱敏处理 - 移除敏感字段
                    request_body = self._sanitize_request_body(request_body)
            except Exception:
                request_body = "[读取失败]"

        #  存储截断：防止大字段导致DB膨胀
        if len(request_body) > self.STORE_REQUEST_BODY_LEN:
            request_body = request_body[:self.STORE_REQUEST_BODY_LEN] + f"...[截断: 原{len(request_body)}B]"

        # 执行请求
        error_type = ""
        error_detail = ""
        error_stack = ""
        business_code = ""
        log_category = "normal"
        response_status = 200
        response_body_str = ""

        try:
            response = await call_next(request)
            response_status = response.status_code

            # 采集响应体（仅非流式JSON响应，且大小合理）
            if isinstance(response, JSONResponse) and response_status >= 400:
                try:
                    body = getattr(response, "body", b"")
                    if body and len(body) <= self.MAX_RESPONSE_BODY_LEN:
                        response_body_str = body.decode("utf-8", errors="replace")
                except Exception:
                    pass

            #  响应体存储截断：防止大字段导致DB膨胀
            if len(response_body_str) > self.STORE_RESPONSE_BODY_LEN:
                response_body_str = response_body_str[:self.STORE_RESPONSE_BODY_LEN] + f"...[截断: 原{len(response_body_str)}B]"

            # 分类
            if response_status == 401:
                log_category = "auth_fail"
            elif response_status >= 400:
                log_category = "error"

            # 从错误响应中提取业务错误码
            if response_body_str and response_status >= 400:
                try:
                    err_data = json.loads(response_body_str)
                    err_obj = err_data.get("error", err_data)
                    if isinstance(err_obj, dict):
                        business_code = err_obj.get("code", "")
                        error_detail = err_obj.get("message", "")
                        error_type = err_obj.get("type", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

            # 错误追踪：记录429/5xx/4xx响应
            if response_status == 429:
                track_error("ratelimit", "middleware", "rate limited", 429, path)
            elif response_status >= 500:
                track_error("upstream", "middleware", error_detail or f"HTTP {response_status}", response_status, path)
            elif response_status == 401:
                track_error("auth", "middleware", "authentication failed", 401, path)

        except HTTPException as e:
            response_status = e.status_code
            error_type = "HTTPException"
            error_detail = str(e.detail) if e.detail else ""
            business_code = ""
            if isinstance(e.detail, dict):
                error_detail = e.detail.get("message", str(e.detail))
                error_type = e.detail.get("type", "HTTPException")
                business_code = e.detail.get("code", "")
            error_stack = traceback.format_exc()[:self.STORE_ERROR_STACK_LEN]
            log_category = "auth_fail" if response_status == 401 else "error"
            response_body_str = json.dumps({"error": {"message": error_detail, "type": error_type, "code": business_code}}, ensure_ascii=False)
            response = JSONResponse(status_code=response_status, content={"error": {"message": error_detail, "type": error_type, "code": business_code}})
            track_error("auth" if response_status == 401 else "validation", "middleware", error_detail, response_status, path)

        except Exception as e:
            response_status = 500
            error_type = type(e).__name__
            error_detail = str(e)[:1024]
            error_stack = traceback.format_exc()[:self.STORE_ERROR_STACK_LEN]
            business_code = "internal_error"
            log_category = "error"
            response_body_str = json.dumps({"error": {"message": f"内部错误: {error_detail}", "type": "internal_error", "code": "internal_error"}}, ensure_ascii=False)
            response = JSONResponse(status_code=500, content={"error": {"message": f"内部错误: {error_detail}", "type": "internal_error", "code": "internal_error"}})
            track_error("runtime", "middleware", error_detail, 500, path)

            # 计算延迟
        end_ts = time.time()
        rt = end_ts - start_ts
        latency_ms = round(rt * 1000, 3)
        latency_us = int(rt * 1_000_000)

        end_dt = datetime.fromtimestamp(end_ts, tz=timezone(timedelta(hours=8)))
        started_at = start_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{start_dt.microsecond // 1000:03d}+08:00"
        completed_at = end_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{end_dt.microsecond // 1000:03d}+08:00"

        # 提取model信息（从请求体中解析）
        model = ""
        if request_body:
            try:
                body_data = json.loads(request_body)
                model = body_data.get("model", "")
            except (json.JSONDecodeError, AttributeError):
                pass

        # 提取client_id（从认证信息中）
        client_id = ""
        client_name = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:].strip()
            # v10.1修复：middleware持有完整key → hash_secret 后按 key_hash 精确查询
            # （此前用明文前13字符 LIKE 掩码列 key_prefix，永远 miss 且每请求2次同步DB）
            key_hash = hash_secret(api_key)
            cache_key = f"ck:{key_hash}"  # 以哈希为缓存键，避免明文key材料进入缓存键
            # 检查缓存是否过期
            cache_ts = _client_name_cache_ts.get(cache_key, 0)
            cached = _client_name_cache.get(cache_key)
            if cached and cache_ts and (time.time() - cache_ts > _CLIENT_NAME_CACHE_TTL):
                _client_name_cache.pop(cache_key, None)
                _client_name_cache_ts.pop(cache_key, None)
                cached = None
            if cached:
                client_id, client_name = cached
            else:
                # 同步DB调用放到线程池执行，避免阻塞事件循环；正/负结果均缓存60秒（负结果也缓存，防未知key每请求打DB）
                try:
                    from app.database import fetch_one as _fetch_one
                    key_row = await asyncio.to_thread(
                        _fetch_one,
                        "SELECT k.client_id, c.name "
                        "FROM client_api_keys k LEFT JOIN clients c ON c.id = k.client_id "
                        "WHERE k.key_hash = %s LIMIT 1",
                        (key_hash,),
                    )
                    if key_row:
                        client_id = key_row["client_id"] or ""
                        client_name = key_row["name"] or ""
                    _client_name_cache[cache_key] = (client_id, client_name)
                    _client_name_cache_ts[cache_key] = time.time()
                except Exception:
                    pass

        # 过滤无意义日志：没有client_id且没有model的请求不记录
        # （如扫描请求/.env、错误路径/v1/responses、未认证401等，无业务统计价值）
        # 管理员测试请求(client_id=None)也不记录，只有真实用户密钥请求才记录
        has_business_data = bool(client_id) or bool(model)
        if not has_business_data:
            return response

        # 异步写入日志（不阻塞响应）- 放入批量队列由后台协程写入
        try:
            _log_queue.put_nowait({
                "id": str(uuid.uuid4()),
                "client_id": client_id,
                "upstream_key_id": "",
                "model": model,
                "status_code": response_status,
                "latency_ms": latency_ms,
                "latency_us": latency_us,
                "is_stream": 1 if request_body and re.search(r'"stream"\s*:\s*true', request_body) else 0,
                "error_msg": error_detail or "",
                "request_path": path,
                "http_method": http_method,
                "client_ip": client_ip,
                "user_agent": user_agent,
                "request_params": query_params,
                "request_body": request_body,
                "response_body": response_body_str,
                "error_type": error_type,
                "error_detail": error_detail,
                "error_stack": error_stack,
                "business_code": business_code,
                "log_category": log_category,
                "started_at": started_at,
                "completed_at": completed_at,
            })
        except Exception:
            logger.exception("[v10.0] 日志入队异常，不影响请求响应")

        return response


# _write_request_log_async 已废弃，由异步批量日志写入器（_log_worker）替代。
# 详见本文件顶部的 "异步批量日志写入器" 章节。
# start_log_worker() 必须在 main.py 的 lifespan 中调用。
