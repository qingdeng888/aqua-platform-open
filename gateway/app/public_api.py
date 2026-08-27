"""
公开API路由 - OpenAI兼容端点

端点列表：
- POST /v1/chat/completions    OpenAI兼容聊天补全（支持流式）
- POST /api/v1/chat/completions /api/v1前缀兼容路径
- GET  /v1/models              OpenAI兼容模型列表
- GET  /api/v1/models          /api/v1前缀兼容路径
- GET  /api/public/models      公开模型列表（无需认证）
- POST /v1/embeddings          OpenAI兼容向量化

关键流程：
1. 客户端认证（下游密钥验证）
2. 调度器选择密钥（select_key）
3. 上游NVIDIA API调用
4. 流式SSE响应 / 非流式响应
5. 错误处理（429自动切换密钥，tenacity自动重试）
6. 请求日志记录

 流式稳定性优化：
- 流式per-chunk空闲超时检测（替代全局超时）
- SSE keepalive心跳（30秒间隔发送": ping"注释行）
- 流式中途断连优雅处理（发送错误SSE事件 + [DONE]）
- 差异化403/429/timeout冷却（配合scheduler.py的差异化策略）
"""
import asyncio
import json
import time
import traceback
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, ConfigDict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.database import (
    fetch_one, fetch_all, execute, get_setting, insert_audit, utcnow, localnow,
)
from app.security import (
    decrypt_secret, decrypt_upstream_key, hash_secret, mask_secret,
)
from app.scheduler import (
    get_scheduler, get_threshold_for_model, get_timeout_for_model,
    apply_model_defaults, REASONING_MODELS, SLOW_MODELS,
    STREAM_FIRST_BYTE_TIMEOUT, STREAM_IDLE_TIMEOUT,
)
from app.middleware import is_cc_switch_request, get_client_ip
from app.request_validator import (
    validate_and_correct_model, build_model_error_suggestion,
    clean_and_validate_api_key, normalize_base_url,
    validate_and_sanitize, refresh_verified_models,
)
from app.ip_monitor import get_real_client_ip, get_ip_monitor
from app.circuit_breaker import (
    get_circuit_breaker,
    get_model_timeout, graceful_degradation_response,
)

logger = logging.getLogger("acu.api")

router = APIRouter()

# ========== 流式稳定性配置 ==========
# SSE keepalive心跳间隔（秒）- 在流式传输空闲期间发送": ping"注释行
# 防止客户端/Nginx因长时间无数据而断开连接
SSE_KEEPALIVE_INTERVAL = 15  # 15秒发送一次心跳
# 流式per-chunk空闲超时（秒）- 两个SSE事件之间的最大间隔
# 超过此时间未收到上游数据则视为超时
STREAM_CHUNK_IDLE_TIMEOUT = 180  # 3分钟（推理模型思考时间可能很长）


# ========== 自定义异常 ==========

class UpstreamRetryableError(Exception):
    """上游可重试错误（429/5xx/连接错误），触发tenacity重试并切换密钥"""
    pass


# ========== 请求模型 ==========

class ChatRequest(BaseModel):
    """聊天请求 - 全量透传，支持所有模型参数"""
    model: str
    messages: list
    stream: Optional[bool] = False
    model_config = ConfigDict(extra="allow")


# ========== 客户端认证 ==========

def authenticate_client(request: Request) -> dict:
    """
    验证下游客户端API密钥

    支持两种认证方式：
    1. Authorization: Bearer sk-xxx
    2. x-api-key: sk-xxx
    """
    # 提取密钥
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        raw_token = auth[7:]
    else:
        raw_token = request.headers.get("x-api-key", "")

    # === v9.2: API Key 清洗（去除空白/换行/多余引号） ===
    cleaned_token, key_error = clean_and_validate_api_key(raw_token)
    if key_error:
        # 测试/演示key直接拦截
        raise HTTPException(status_code=401, detail={
            "message": key_error,
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        })

    if not cleaned_token or not cleaned_token.startswith(("acu_", "sk-")):
        raise HTTPException(status_code=401, detail={
            "message": "无效的API密钥",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        })

    # 查询密钥（包含 user_type）— 优先使用缓存
    key_hash = hash_secret(cleaned_token)
    
    # 检查缓存
    from app.scheduler import get_scheduler
    scheduler = get_scheduler()
    cached = scheduler.get_client_key_cache(key_hash)
    if cached is not None:
        return dict(cached)

    key_row = fetch_one(
        "SELECT ck.id, ck.client_id, ck.status, c.name as client_name, c.status as client_status, "
        "COALESCE(c.user_type, 'old') as user_type "
        "FROM client_api_keys ck "
        "JOIN clients c ON ck.client_id = c.id "
        "WHERE ck.key_hash = %s AND ck.status = 'active' AND c.status = 'active'",
        (key_hash,),
    )

    if not key_row:
        raise HTTPException(status_code=401, detail={
            "message": "API密钥无效或已被禁用",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        })

    # v10.0: last_used_at 更新改为后台线程（避免同步DB写阻塞事件循环）
    try:
        import threading
        _now = utcnow()
        _kid = key_row["id"]
        threading.Thread(
            target=lambda: _async_update_last_used(_kid, _now),
            daemon=True
        ).start()
    except Exception:
        pass

    # 写入缓存
    result = dict(key_row)
    scheduler.set_client_key_cache(key_hash, result)
    return result


def _async_update_last_used(key_id, ts):
    """Background thread: update last_used_at without blocking event loop"""
    try:
        execute("UPDATE client_api_keys SET last_used_at = %s WHERE id = %s", (ts, key_id))
    except Exception:
        pass


# ========== 模型列表缓存 ==========

_models_cache = {"data": None, "expires": 0}

# 经过实际API调用验证的可用模型集合（硬编码白名单+运行时验证）
# NVIDIA NIM的/v1/models端点会列出所有平台模型，但很多实际上无法通过chat/completions调用
# 这里只保留实测可用的模型ID，确保用户看到的每个模型都能实际使用
_VERIFIED_WORKING_MODELS = {
    "deepseek-ai/deepseek-v4-pro-0813",
    "google/diffusiongemma-26b-a4b-it",
    "google/gemma-4-31b-it",
    "meta/llama-3.2-11b-vision-instruct",
    "meta/llama-3.2-90b-vision-instruct",
    "meta/muse-glimmer-30b",
    "minimaxai/minimax-m3",
    "moonshotai/kimi-k3",
    "nvidia/ising-calibration-1.5-31b",
    "nvidia/llama-3.1-nemoguard-8b-content-safety",
    "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-content-safety",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/riva-translate-4b-instruct-v1.1",
    "nvidia/riva-translate-4b-instruct-v2",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "poolside/laguna-xs-2.1",
    "stepfun-ai/step-3.7-flash",
    "mistralai/mistral-nemotron",
    "nvidia/nemotron-3-ultra-550b-a55b",
}


async def fetch_upstream_models() -> list:
    """从上游NVIDIA获取模型列表，过滤只保留可用的对话模型（60秒缓存）"""
    now_ts = time.time()
    if _models_cache["data"] and _models_cache["expires"] > now_ts:
        return _models_cache["data"]

    raw_base_url = get_setting("upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    base_url, _ = normalize_base_url(raw_base_url)
    models_path = get_setting("models_path") or "/models"
    url = f"{base_url}{models_path}"

    # 通过调度器选择一个当前最优的密钥（避免取到冷却中的密钥）
    scheduler = get_scheduler()
    select_result = await asyncio.to_thread(scheduler.select_any_key)
    if not select_result:
        # 所有密钥均不可用
        if _models_cache["data"]:
            return _models_cache["data"]
        raise HTTPException(status_code=503, detail={
            "message": "服务繁忙，所有上游密钥暂不可用",
            "type": "service_unavailable",
            "code": "all_keys_exhausted",
        })

    key_id, api_key = select_result

    try:
        await scheduler._ensure_pools()
        client = scheduler._http_pool  # Use existing pool
        resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code == 200:
            data = resp.json()
            all_models = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            upstream_model_ids = set()
            for m in all_models:
                mid = m.get("id", "") if isinstance(m, dict) else str(m)
                if mid:
                    upstream_model_ids.add(mid)

            # 过滤：只保留经过验证可用或在NIM目录中且未弃用的模型
            try:
                from app.nim_models import NIM_MODEL_CATALOG
                nim_catalog = NIM_MODEL_CATALOG
            except ImportError:
                nim_catalog = {}

            filtered_models = []
            removed_models = []
            for m in all_models:
                mid = m.get("id", "") if isinstance(m, dict) else ""
                if not mid:
                    continue

                # 检查是否在已验证可用列表
                if mid in _VERIFIED_WORKING_MODELS:
                    filtered_models.append(m)
                    continue

                # 检查是否在NIM目录中且未弃用
                info = nim_catalog.get(mid)
                if info:
                    tags = [t.lower() for t in (info.tags or [])]
                    if "deprecated" in tags:
                        removed_models.append(mid)
                        continue
                    filtered_models.append(m)
                    continue

                # 不在任何白名单中，排除
                removed_models.append(mid)

            # 记录被移除的模型（便于排查新增的可用模型）
            if removed_models:
                logger.info(f"模型列表过滤: 排除{len(removed_models)}个不可用模型: {', '.join(removed_models[:20])}")

            # 检查已验证列表中是否含有上游已不存在的模型
            upstream_set = {m.get("id", "") for m in all_models if isinstance(m, dict)}
            stale_verified = [m for m in _VERIFIED_WORKING_MODELS if m not in upstream_set]
            if stale_verified:
                logger.warning(f"已验证模型中以下模型上游已不存在(可能已弃用): {', '.join(stale_verified)}")
                # 不移除，保留为后备（上游可能临时下线）
                for m in stale_verified:
                    # 添加回列表（作为后备）
                    filtered_models.append({"id": m})

            _models_cache["data"] = filtered_models
            _models_cache["expires"] = now_ts + 60  # 60秒缓存（接近实时）
            # v9.2: 同步更新验证器缓存
            refresh_verified_models(filtered_models)
            logger.info(f"模型列表已更新: {len(filtered_models)}个可用模型 (排除{len(removed_models)}个)")
            return filtered_models
    except Exception as e:
        logger.error(f"获取上游模型列表失败: {e}")

    return _models_cache["data"] or []


# ========== 模型列表端点 ==========

def _enrich_model_list(models: list) -> list:
    """为模型列表添加能力标签和友好名称（基于NIM模型目录）"""
    try:
        from app.nim_models import NIM_MODEL_CATALOG, get_model_sort_priority
    except ImportError:
        return models

    priorities = get_model_sort_priority()

    enriched = []
    for m in models:
        model_id = m.get("id", "")
        info = NIM_MODEL_CATALOG.get(model_id)

        enriched_model = dict(m)

        if info:
            # 从NIM模型目录获取详细信息
            enriched_model["display_name"] = info.display_name
            enriched_model["context_length"] = info.context_length
            enriched_model["max_output_tokens"] = info.max_output_tokens

            # 能力标签
            capabilities = []
            if info.supports_images:
                capabilities.append("视觉")
            if info.supports_tools:
                capabilities.append("工具调用")
            if info.context_length >= 1000000:
                capabilities.append("1M上下文")

            # 基于标签推断
            tags_lower = [t.lower() for t in (info.tags or [])]
            if any(kw in tags_lower for kw in ["embedding", "embed"]):
                capabilities.append("嵌入")
            if any(kw in tags_lower for kw in ["safety", "guard", "guardrails"]):
                capabilities.append("安全")
            if any(kw in tags_lower for kw in ["coding", "code generation", "code"]):
                capabilities.append("代码")
            if any(kw in tags_lower for kw in ["ocr", "table extraction"]):
                capabilities.append("OCR")
            if any(kw in tags_lower for kw in ["asr", "tts", "speech"]):
                capabilities.append("语音")
            if any(kw in tags_lower for kw in ["translation", "translate"]):
                capabilities.append("翻译")
            if "deprecated" in tags_lower:
                capabilities.append("已弃用")
            if any(kw in tags_lower for kw in ["reasoning", "agent"]):
                if "推理" not in capabilities:
                    capabilities.append("推理")

            # 如果没有推理/嵌入/安全/代码等标签，默认标记为推理
            if not capabilities and info.model_family in (
                "deepseek", "glm", "qwen", "kimi", "nemotron", "gpt-oss",
                "step", "llama", "mistral", "gemma", "minimax", "yi",
                "jamba", "phi", "granite", "dbrx", "solar", "palmyra",
                "codestral", "mixtral", "codellama",
            ):
                capabilities.append("推理")

            enriched_model["capabilities"] = capabilities
        else:
            # 不在目录中的模型，推断基本能力
            capabilities = []
            model_lower = model_id.lower()
            if any(kw in model_lower for kw in ["vision", "vl", "fuyu", "kosmos", "omni"]):
                capabilities.append("视觉")
            if any(kw in model_lower for kw in ["embed", "bge-m3", "arctic-embed"]):
                capabilities.append("嵌入")
            if any(kw in model_lower for kw in ["safety", "guard", "gliner-pii", "content-safety"]):
                capabilities.append("安全")
            if any(kw in model_lower for kw in ["codestral", "starcoder", "codegemma", "coder"]):
                capabilities.append("代码")
            if any(kw in model_lower for kw in ["ocr", "parse"]):
                capabilities.append("OCR")
            if any(kw in model_lower for kw in ["riva-translate"]):
                capabilities.append("翻译")
            if any(kw in model_lower for kw in ["gemma-3n", "dracarys", "seed-oss"]):
                capabilities.append("已弃用")
            if not capabilities:
                capabilities.append("推理")
            enriched_model["capabilities"] = capabilities

        # 排序优先级
        enriched_model["sort_priority"] = priorities.get(model_id, 999)

        enriched.append(enriched_model)

    # 按排序优先级排序
    enriched.sort(key=lambda m: m.get("sort_priority", 999))

    return enriched


@router.get("/v1/models", tags=["公共API"])
@router.get("/api/v1/models", tags=["公共API"])
async def list_models(request: Request):
    """OpenAI兼容模型列表（含能力标签和友好名称）"""
    # 尝试认证（可选，不强制）
    try:
        await asyncio.to_thread(authenticate_client, request)
    except HTTPException:
        pass  # 未认证也允许查看模型列表

    models = await fetch_upstream_models()
    enriched = _enrich_model_list(models)
    return {"object": "list", "data": enriched}


@router.get("/api/public/models", tags=["公共API"])
async def public_models():
    """公开模型列表（无需认证，5分钟缓存，含能力标签）"""
    models = await fetch_upstream_models()
    enriched = _enrich_model_list(models)
    return {"object": "list", "data": enriched}


# ========== 流式请求处理 ==========

async def _handle_stream_request(
    scheduler, client_id, key_id, model,
    upstream_url, headers, body, timeout, start_time, request
):
    """处理流式SSE请求

     优化：
    - per-chunk空闲超时检测（而非全局超时）
    - SSE keepalive心跳（防止客户端/Nginx断连）
    - 流式中途断连优雅处理

    先发起请求并检查状态码：
    - 429/5xx: 抛出UpstreamRetryableError触发重试
    - 4xx: 直接返回错误响应
    - 200: 返回流式SSE响应
    """
    pool = await scheduler.get_stream_pool()

    # 预检：发起请求并检查状态码，再决定是重试还是流式返回
    # : 流式请求使用600秒httpx超时（大于per-chunk空闲超时180秒）
    # 实际的空闲检测由stream_generator中的per-chunk逻辑处理
    req = pool.build_request(
        "POST", upstream_url, headers=headers, json=body,
        timeout=httpx.Timeout(600.0, connect=30.0),
    )
    resp = await pool.send(req, stream=True)

    if resp.status_code != 200:
        # 读取错误信息后关闭连接
        error_bytes = await resp.aread()
        await resp.aclose()
        error_text = error_bytes.decode("utf-8", errors="replace")[:500]
        rt = time.time() - start_time

        if resp.status_code == 429:
            scheduler.record_response(key_id, model, False, rt, 429, "429")
            scheduler.trigger_hard_cooldown(key_id, model, "429")
            logger.warning(f"上游429: key={key_id[:8]} model={model} 触发桶级冷却")
            raise UpstreamRetryableError(f"429: key={key_id[:8]} model={model}")
        elif resp.status_code == 403:
            # 403 - 上游密钥被限制，触发差异化冷却并重试其他密钥
            scheduler.record_response(key_id, model, False, rt, 403, "4xx")
            scheduler.trigger_hard_cooldown(key_id, model, "403")
            logger.warning(f"上游403: key={key_id[:8]} model={model} 触发差异化冷却")
            scheduler.release_client_request(client_id)
            raise UpstreamRetryableError(f"403: key={key_id[:8]} model={model}")
        elif resp.status_code >= 500:
            scheduler.record_response(key_id, model, False, rt, resp.status_code, "5xx")
            scheduler.trigger_hard_cooldown(key_id, model, "5xx")
            logger.warning(f"上游{resp.status_code}: key={key_id[:8]} model={model} 触发桶级冷却+5xx熔断检测")
            raise UpstreamRetryableError(f"{resp.status_code}: key={key_id[:8]} model={model}")
        else:
            # 4xx（非429）- 返回错误给客户端（统一OpenAI兼容格式）
            scheduler.record_response(key_id, model, False, rt, resp.status_code, "4xx")
            scheduler.release_client_request(client_id)
            try:
                error_body = json.loads(error_text)
            except Exception:
                error_body = {"error": {"message": error_text}}
            # 确保错误格式为标准 OpenAI 格式
            if "error" not in error_body or not isinstance(error_body["error"], dict):
                error_body = {"error": {"message": str(error_body)}}
            
            # 为模型不存在返回清晰中文提示
            if resp.status_code == 404:
                err_msg = error_body.get("error", {}).get("message", "").lower()
                if "model" in err_msg and ("not found" in err_msg or "not exist" in err_msg or "unknown" in err_msg):
                    return JSONResponse(status_code=404, content={
                        "error": {
                            "message": f"模型 '{model}' 在上游服务中不存在。请检查模型名称是否正确，或查看模型列表获取可用模型。",
                            "type": "invalid_request_error",
                            "code": "model_not_found",
                        }
                    })
            
            return JSONResponse(status_code=resp.status_code, content=error_body)

    # 状态码200 - 创建流式生成器（: 带keepalive和per-chunk空闲检测）
    async def stream_generator():
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        first_byte_received = False
        last_data_time = time.time()  # 上次收到上游数据的时间
        last_keepalive_time = time.time()  # 上次发送keepalive的时间
        chunks_received = 0  # 已接收的SSE事件数

        try:
            # 使用aiter_lines逐行读取，配合空闲检测
            async for line in resp.aiter_lines():
                now = time.time()

                # per-chunk空闲超时检测
                idle_time = now - last_data_time
                if idle_time > STREAM_CHUNK_IDLE_TIMEOUT:
                    # 空闲超时 - 优雅终止流式传输
                    logger.warning(
                        f"流式空闲超时: key={key_id[:8]} model={model} "
                        f"idle={idle_time:.0f}s chunks={chunks_received} "
                        f"已接收部分数据，优雅终止"
                    )
                    # 如果已收到部分数据，发送超时错误事件但保留已生成内容
                    rt = now - start_time
                    scheduler.record_response(key_id, model, False, rt, 0, "timeout")
                    scheduler.trigger_hard_cooldown(key_id, model, "timeout")
                    error_data = {"error": {"message": "上游响应超时（空闲时间过长）",
                                            "type": "timeout_error", "code": "upstream_idle_timeout"}}
                    yield f"data: {json.dumps(error_data)}\n\n"
                    yield "data: [DONE]\n\n"
                    scheduler.release_client_request(client_id)
                    await _log_request(
                        client_id, key_id, model, 504, rt,
                        prompt_tokens, completion_tokens, total_tokens, True,
                        f"流式空闲超时(idle={idle_time:.0f}s)",
                        start_ts=start_time,
                        error_type="IdleTimeout", error_detail=f"空闲{idle_time:.0f}s超时",
                        business_code="upstream_idle_timeout",
                        request_path=str(request.url.path), http_method=request.method,
                        client_ip=get_client_ip(request), user_agent=request.headers.get("user-agent", "")[:512],
                    )
                    return

                if line:
                    last_data_time = now
                    chunks_received += 1
                    if not first_byte_received:
                        first_byte_received = True
                    # 解析usage信息
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            data = json.loads(line[6:])
                            if "usage" in data and data["usage"]:
                                usage = data["usage"]
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", 0)
                                total_tokens = usage.get("total_tokens", 0)
                        except json.JSONDecodeError:
                            pass
                    yield f"{line}\n\n"
                    last_keepalive_time = now  # 收到数据时重置keepalive计时
                else:
                    # 空行 - 检查是否需要发送keepalive
                    if now - last_keepalive_time >= SSE_KEEPALIVE_INTERVAL:
                        # 发送SSE注释行作为keepalive（客户端会忽略SSE注释）
                        yield ": ping\n\n"
                        last_keepalive_time = now
                        logger.debug(f"流式keepalive: key={key_id[:8]} model={model}")

            # 流式完成 - 发送[DONE]标记
            yield "data: [DONE]\n\n"

            # 正常结束：所有数据已接收完毕
            rt = time.time() - start_time
            scheduler.record_response(key_id, model, True, rt, 200, "")
            scheduler.release_client_request(client_id)

            # v10.0 防呆防傻：记录熔断器成功
            cb = get_circuit_breaker()
            cb.record_success(f"model:{model}")

            # 记录请求日志（全量日志）
            await _log_request(
                client_id, key_id, model, 200, rt,
                prompt_tokens, completion_tokens, total_tokens, True, "",
                start_ts=start_time,
                request_path=getattr(request, 'url', None) and str(request.url.path) or "",
                http_method=getattr(request, 'method', ''),
                client_ip=get_client_ip(request),
                user_agent=getattr(request, 'headers', {}).get('user-agent', '')[:512] if hasattr(request, 'headers') else "",
            )
            return  # 正常返回，不执行下面的错误处理

        except httpx.TimeoutException:
            rt = time.time() - start_time
            scheduler.record_response(key_id, model, False, rt, 0, "timeout")
            # 超时触发差异化冷却（15秒，不是隔离5分钟）
            scheduler.trigger_hard_cooldown(key_id, model, "timeout")
            error_data = {"error": {"message": "上游响应超时",
                                    "type": "timeout_error", "code": "upstream_timeout"}}
            yield f"data: {json.dumps(error_data)}\n\n"
            yield "data: [DONE]\n\n"
            scheduler.release_client_request(client_id)
            # : 记录超时错误日志
            await _log_request(
                client_id, key_id, model, 504, rt, 0, 0, 0, True, "上游响应超时",
                start_ts=start_time,
                error_type="TimeoutException", error_detail="上游响应超时",
                business_code="upstream_timeout",
                request_path=str(request.url.path), http_method=request.method,
                client_ip=get_client_ip(request), user_agent=request.headers.get("user-agent", "")[:512],
            )
        except httpx.ReadError as e:
            # : 上游连接断开（常见的流式中途断连场景）
            rt = time.time() - start_time
            scheduler.record_response(key_id, model, False, rt, 0, "conn_error")
            logger.warning(f"流式读取错误: key={key_id[:8]} model={model} chunks={chunks_received} error={e}")
            # 如果已收到部分数据，只记录为部分成功（不触发冷却/隔离）
            if chunks_received > 0 and prompt_tokens > 0:
                logger.info(f"流式部分完成: key={key_id[:8]} model={model} 已接收{chunks_received}个chunk, prompt={prompt_tokens} tokens")
                # 有部分数据时尝试恢复：发送stop信号让客户端继续处理已收到的内容
                finish_data = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {json.dumps(finish_data)}\n\n"
            else:
                scheduler.trigger_hard_cooldown(key_id, model, "timeout")
                # 无有效数据时发送error让客户端知道出错了，但不发送[DONE]阻止重试
                error_data = {"error": {"message": f"流式传输中断: {str(e)}",
                                        "type": "connection_error", "code": "stream_interrupted"}}
                yield f"data: {json.dumps(error_data)}\n\n"
            yield "data: [DONE]\n\n"
            scheduler.release_client_request(client_id)
            await _log_request(
                client_id, key_id, model, 502, rt,
                prompt_tokens, completion_tokens, total_tokens, True,
                f"流式传输中断(已接收{chunks_received}chunks): {str(e)}",
                start_ts=start_time,
                error_type="ReadError", error_detail=str(e)[:1024],
                error_stack=traceback.format_exc()[:4096],
                business_code="stream_interrupted",
                request_path=str(request.url.path), http_method=request.method,
                client_ip=get_client_ip(request), user_agent=request.headers.get("user-agent", "")[:512],
            )
        except Exception as e:
            rt = time.time() - start_time
            scheduler.record_response(key_id, model, False, rt, 0, "error")
            error_data = {"error": {"message": f"流式传输错误: {str(e)}",
                                    "type": "internal_error", "code": "stream_error"}}
            yield f"data: {json.dumps(error_data)}\n\n"
            yield "data: [DONE]\n\n"
            scheduler.release_client_request(client_id)
            # : 记录流式异常日志
            await _log_request(
                client_id, key_id, model, 500, rt, 0, 0, 0, True, f"流式传输错误: {str(e)}",
                start_ts=start_time,
                error_type=type(e).__name__, error_detail=str(e)[:1024],
                error_stack=traceback.format_exc()[:4096],
                business_code="stream_error",
                request_path=str(request.url.path), http_method=request.method,
                client_ip=get_client_ip(request), user_agent=request.headers.get("user-agent", "")[:512],
            )
        finally:
            await resp.aclose()

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ========== 非流式请求处理 ==========

async def _handle_nonstream_request(
    scheduler, client_id, key_id, model,
    upstream_url, headers, body, timeout, start_time, request=None
):
    """处理非流式请求

    返回值：
    - 成功: JSONResponse
    - 4xx: JSONResponse（错误）
    - 429/5xx/超时/连接错误: 抛出UpstreamRetryableError触发重试
    """
    pool = await scheduler.get_http_pool()

    try:
        resp = await pool.post(
            upstream_url, headers=headers, json=body,
            timeout=httpx.Timeout(timeout, connect=30.0),
        )
        rt = time.time() - start_time

        if resp.status_code == 200:
            # 成功（v10.0: 防御上游返回空body）
            try:
                data = resp.json()
                if data is None:
                    data = {}
            except Exception:
                data = {}
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            scheduler.record_response(key_id, model, True, rt, 200, "")
            scheduler.release_client_request(client_id)

            # v10.0 防呆防傻：记录熔断器成功
            cb = get_circuit_breaker()
            cb.record_success(f"model:{model}")

            # 记录请求日志（全量日志）
            await _log_request(
                client_id, key_id, model, 200, rt,
                prompt_tokens, completion_tokens, total_tokens, False, "",
                start_ts=start_time,
                response_body=json.dumps(data, ensure_ascii=False)[:65536],
                request_path=str(request.url.path) if request else "",
                http_method=request.method if request else "",
                client_ip=get_client_ip(request) if request else "",
                user_agent=request.headers.get("user-agent", "")[:512] if request else "",
            )

            return JSONResponse(status_code=200, content=data)

        elif resp.status_code == 429:
            # 429 - 触发桶级冷却
            scheduler.record_response(key_id, model, False, rt, 429, "429")
            scheduler.trigger_hard_cooldown(key_id, model, "429")
            logger.warning(f"上游429: key={key_id[:8]} model={model} 触发桶级冷却")
            raise UpstreamRetryableError(f"429: key={key_id[:8]} model={model}")

        elif resp.status_code == 403:
            # 403 - 上游密钥被限制，触发差异化冷却并重试其他密钥
            scheduler.record_response(key_id, model, False, rt, 403, "4xx")
            scheduler.trigger_hard_cooldown(key_id, model, "403")
            logger.warning(f"上游403: key={key_id[:8]} model={model} 触发差异化冷却")
            scheduler.release_client_request(client_id)
            # 记录403日志
            try:
                error_body = resp.json()
                if error_body is None:
                    error_body = {"error": {"message": (resp.text or "")[:1024]}}
            except Exception:
                error_body = {"error": {"message": (resp.text or "")[:1024]}}
            err_msg = ""
            if isinstance(error_body, dict):
                err_obj = error_body.get("error", error_body)
                if isinstance(err_obj, dict):
                    err_msg = err_obj.get("message", "")
            await _log_request(
                client_id, key_id, model, 403, rt,
                0, 0, 0, False, err_msg or f"上游403错误",
                start_ts=start_time,
                error_type="upstream_403",
                error_detail=err_msg or resp.text[:1024],
                business_code="upstream_403",
                response_body=json.dumps(error_body, ensure_ascii=False)[:65536] if isinstance(error_body, dict) else str(error_body)[:65536],
                request_path=str(request.url.path) if request else "",
                http_method=request.method if request else "",
                client_ip=get_client_ip(request) if request else "",
                user_agent=request.headers.get("user-agent", "")[:512] if request else "",
            )
            raise UpstreamRetryableError(f"403: key={key_id[:8]} model={model}")

        elif resp.status_code >= 500:
            # 5xx - 触发桶级冷却+模型级5xx熔断检测
            scheduler.record_response(key_id, model, False, rt, resp.status_code, "5xx")
            scheduler.trigger_hard_cooldown(key_id, model, "5xx")
            logger.warning(f"上游{resp.status_code}: key={key_id[:8]} model={model} 触发桶级冷却+5xx熔断检测")
            raise UpstreamRetryableError(f"{resp.status_code}: key={key_id[:8]} model={model}")

        else:
            # 4xx
            scheduler.record_response(key_id, model, False, rt, resp.status_code, "4xx")
            scheduler.release_client_request(client_id)
            try:
                error_body = resp.json()
                if error_body is None:
                    error_body = {"error": {"message": (resp.text or "")[:1024]}}
            except Exception:
                error_body = {"error": {"message": (resp.text or "")[:1024]}}
            # : 记录4xx错误日志
            err_msg = ""
            err_code = ""
            err_type = ""
            if isinstance(error_body, dict):
                err_obj = error_body.get("error", error_body)
                if isinstance(err_obj, dict):
                    err_msg = err_obj.get("message", "")
                    err_code = err_obj.get("code", "")
                    err_type = err_obj.get("type", "")
            await _log_request(
                client_id, key_id, model, resp.status_code, rt,
                0, 0, 0, False, err_msg or f"上游{resp.status_code}错误",
                start_ts=start_time,
                error_type=err_type or f"upstream_{resp.status_code}",
                error_detail=err_msg or resp.text[:1024],
                business_code=err_code or f"upstream_{resp.status_code}",
                response_body=json.dumps(error_body, ensure_ascii=False)[:65536] if isinstance(error_body, dict) else str(error_body)[:65536],
                request_path=str(request.url.path) if request else "",
                http_method=request.method if request else "",
                client_ip=get_client_ip(request) if request else "",
                user_agent=request.headers.get("user-agent", "")[:512] if request else "",
            )
            return JSONResponse(status_code=resp.status_code, content=error_body)

    except UpstreamRetryableError:
        raise  # 重新抛出给tenacity处理
    except httpx.TimeoutException:
        rt = time.time() - start_time
        scheduler.record_response(key_id, model, False, rt, 0, "timeout")
        # 超时只触发冷却，不触发隔离（超时可能是NVIDIA响应慢，不代表密钥故障）
        scheduler.trigger_hard_cooldown(key_id, model, "timeout")
        logger.warning(f"请求超时: key={key_id[:8]} model={model} rt={rt:.1f}s")
        raise UpstreamRetryableError(f"timeout: key={key_id[:8]} model={model}")
    except httpx.ConnectError as e:
        rt = time.time() - start_time
        scheduler.record_response(key_id, model, False, rt, 0, "conn_error")
        scheduler.trigger_isolation(key_id, model, "conn_error")
        logger.error(f"连接错误: key={key_id[:8]} model={model} error={e}")
        raise UpstreamRetryableError(f"conn_error: key={key_id[:8]} model={model}")


# ========== 带重试的上游调用（聊天补全） ==========

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception_type(UpstreamRetryableError),
    reraise=True
)
async def _call_upstream(
    scheduler, client_id, model, upstream_url, body, timeout, is_stream, request,
    dispatch_start_time=None
):
    """带tenacity重试的上游调用 - 聊天补全

    每次重试会重新选择密钥，遇到429/5xx/连接错误抛出UpstreamRetryableError触发重试。
    """
    # : 记录密钥选择耗时（网关调度延迟度量）
    _key_select_start = time.time()
    select_result = await asyncio.to_thread(scheduler.select_key, model)
    _key_select_ms = (time.time() - _key_select_start) * 1000

    # 将调度耗时存入scheduler临时属性和request.state，供日志记录使用
    scheduler._last_dispatch_ms = _key_select_ms
    if request and hasattr(request, 'state'):
        request.state.gateway_dispatch_ms = _key_select_ms
    if not select_result:
        scheduler.release_client_request(client_id)
        # 检查是否是模型级熔断（而非真正的密钥不可用）
        now = time.time()
        circuit_breaker_until = scheduler._model_429_circuit_breaker.get(model, 0)
        circuit_breaker_5xx_until = scheduler._model_5xx_circuit_breaker.get(model, 0)
        if now < circuit_breaker_until:
            raise HTTPException(status_code=429, detail={
                "message": "上游限流中，请稍后重试",
                "type": "rate_limit_exceeded",
                "code": "model_rate_limited",
            })
        if now < circuit_breaker_5xx_until:
            raise HTTPException(status_code=503, detail={
                "message": f"模型 '{model}' 上游服务暂时不可用（5xx错误过多），请稍后重试",
                "type": "service_unavailable",
                "code": "model_5xx_circuit_broken",
            })
        raise HTTPException(status_code=503, detail={
            "message": "服务繁忙，所有上游密钥暂不可用，请稍后重试",
            "type": "service_unavailable",
            "code": "all_keys_exhausted",
        })

    key_id, selected_model, api_key = select_result

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # CC Switch适配
    if is_cc_switch_request(request):
        headers["User-Agent"] = request.headers.get("user-agent", "")

    start_time = time.time()

    try:
        if is_stream:
            return await _handle_stream_request(
                scheduler, client_id, key_id, model,
                upstream_url, headers, body, timeout, start_time, request
            )
        else:
            return await _handle_nonstream_request(
                scheduler, client_id, key_id, model,
                upstream_url, headers, body, timeout, start_time, request
            )
    except UpstreamRetryableError:
        raise  # 让tenacity处理重试
    except Exception as e:
        rt = time.time() - start_time
        scheduler.record_response(key_id, model, False, rt, 0, "error")
        scheduler.release_client_request(client_id)
        logger.error(f"请求异常: key={key_id[:8]} model={model} error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={
            "message": f"内部错误: {str(e)}",
            "type": "internal_error",
            "code": "internal_error",
        })


# ========== 聊天补全端点 ==========

@router.post("/v1/chat/completions", tags=["公共API"])
@router.post("/api/v1/chat/completions", tags=["公共API"])
async def chat_completions(request: Request):
    """
    OpenAI兼容聊天补全端点

    流程：
    1. 客户端认证
    2. 解析请求体
    3. 调度器选择密钥
    4. 调用上游NVIDIA API（tenacity自动重试）
    5. 流式/非流式响应
    6. 记录请求日志
    """
    # 1. 客户端认证 (v10.0: 异步调用避免同步DB查询阻塞事件循环)
    try:
        client_info = await asyncio.to_thread(authenticate_client, request)
        client_id = client_info["client_id"]
    except HTTPException as e:
        # : 认证失败日志 - 尝试从key_prefix解析client信息
        _auth_key_prefix = ""
        _auth_client_id = ""
        _auth_header = request.headers.get("authorization", "")
        if _auth_header.startswith("Bearer "):
            _auth_key_prefix = _auth_header[7:20]
            try:
                _key_row = await asyncio.to_thread(
                    fetch_one,
                    "SELECT client_id FROM client_api_keys WHERE key_prefix LIKE %s LIMIT 1",
                    (f"{_auth_key_prefix}%",),
                )
                if _key_row:
                    _auth_client_id = _key_row["client_id"]
            except Exception:
                pass
        await _log_request(
            client_id=_auth_client_id, key_id="", model="", status_code=e.status_code, rt=0,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            is_stream=False, error_msg="API密钥无效或已被禁用",
            error_type="AuthenticationError",
            error_detail=e.detail.get("message", str(e.detail)) if isinstance(e.detail, dict) else str(e.detail),
            business_code=e.detail.get("code", "invalid_api_key") if isinstance(e.detail, dict) else "invalid_api_key",
            request_path=str(request.url.path),
            http_method=request.method,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        raise

    # 2. 解析请求体
    try:
        body = await request.json()
    except Exception:
        # : 记录JSON解析失败
        await _log_request(
            client_id=client_id, key_id="", model="", status_code=400, rt=0,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            is_stream=False, error_msg="请求体JSON解析失败",
            error_type="ParseError", error_detail="请求体JSON解析失败",
            business_code="invalid_json",
            request_path=str(request.url.path),
            http_method=request.method,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        raise HTTPException(status_code=400, detail={
            "message": "请求体JSON解析失败",
            "type": "invalid_request_error",
            "code": "invalid_json",
        })

    model = body.get("model", "")

    # === v10.0: 实时IP监控（CDN穿透检测）- 移到body解析后，可记录model ===
    try:
        real_ip = get_real_client_ip(request)
        if real_ip and client_id:
            key_prefix = client_info.get("key_prefix", "")
            get_ip_monitor().record_request(real_ip, client_id, key_prefix=key_prefix,
                                            user_agent=request.headers.get("user-agent", "")[:256])
            # IP封禁检查
            if get_ip_monitor().check_ip_blocked(real_ip):
                await _log_request(
                    client_id=client_id, key_id="", model=model, status_code=403, rt=0,
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    is_stream=False, error_msg="IP已被封禁",
                    error_type="IPBlocked", error_detail=f"IP {real_ip} 因异常行为被封禁",
                    business_code="ip_blocked",
                    request_path=str(request.url.path),
                    http_method=request.method,
                    client_ip=real_ip,
                    user_agent=request.headers.get("user-agent", "")[:512],
                )
                raise HTTPException(status_code=403, detail={
                    "message": "您的IP因异常使用行为被临时限制，请联系管理员解封",
                    "type": "forbidden",
                    "code": "ip_blocked",
                })
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"IP监控异常: {e}")
    if not model:
        raise HTTPException(status_code=400, detail={
            "message": "缺少model参数",
            "type": "invalid_request_error",
            "code": "missing_model",
        })

    # === v9.2: 模型ID智能映射与纠错 ===
    corrected_model, was_corrected = validate_and_correct_model(model)
    if was_corrected:
        logger.info(f"模型ID已自动修正: '{model}' -> '{corrected_model}'")
        body["model"] = corrected_model
        model = corrected_model
    elif model != corrected_model:
        # 无法自动修正，返回建议
        suggestion = build_model_error_suggestion(model)
        raise HTTPException(status_code=400, detail={
            "message": f"模型ID '{model}' 无效。{suggestion}",
            "type": "invalid_request_error",
            "code": "invalid_model",
        })

    # === v9.2: 检查模型是否存在（对照NIM模型目录 + 上游实时列表） ===
    try:
        from app.nim_models import NIM_MODEL_CATALOG
        is_known = model in _VERIFIED_WORKING_MODELS or model in NIM_MODEL_CATALOG
        if not is_known and _models_cache["data"]:
            is_known = any(m.get("id") == model for m in _models_cache["data"] if isinstance(m, dict))
        if not is_known:
            # 尝试从上游实时获取
            try:
                upstream_models = await fetch_upstream_models()
                is_known = any(m.get("id") == model for m in upstream_models if isinstance(m, dict))
            except Exception:
                pass
        if not is_known:
            suggestion = build_model_error_suggestion(model)
            logger.warning(f"模型ID不在可用列表中: '{model}' (可能不存在于上游，已放行)")
            # 不拦截，仅记录日志 - 上游可能有新增模型
    except ImportError:
        pass

    # === v9.2: 请求体格式强制校验 + 参数容错 ===
    validate_and_sanitize(body)

    # === v10.0: 请求安全校验已移除（不再限制消息数量/内容长度等） ===

    is_stream = body.get("stream", False)

    # 自动补全模型默认参数
    body = apply_model_defaults(body)

    # 流式请求注入 stream_options.include_usage
    if is_stream:
        if "stream_options" not in body:
            body["stream_options"] = {}
        body["stream_options"]["include_usage"] = True

    # === v10.0 防呆防傻：熔断器检查 ===
    cb = get_circuit_breaker()
    cb_key = f"model:{model}"
    if not cb.can_request(cb_key):
        degradation = graceful_degradation_response("circuit_open")
        raise HTTPException(status_code=degradation["status_code"], detail={
            "message": degradation["message"],
            "type": degradation["type"],
            "code": degradation["code"],
            "suggestion": degradation["suggestion"],
        })

    # 3. 调度器初始化
    scheduler = get_scheduler()
    await scheduler._ensure_pools()

    # 记录客户端请求（算法5/6/7数据采集）
    scheduler.record_client_request(client_id, model)

    # 4. 带重试的上游调用
    raw_base_url = get_setting("upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    base_url, url_warning = normalize_base_url(raw_base_url)
    if url_warning:
        logger.info(f"Base URL 标准化: {url_warning}")
    chat_path = get_setting("chat_path") or "/chat/completions"
    upstream_url = f"{base_url}{chat_path}"
    timeout = get_timeout_for_model(model)

    _request_start_ts = time.time()
    try:
        return await _call_upstream(
            scheduler, client_id, model, upstream_url, body, timeout, is_stream, request,
            dispatch_start_time=_request_start_ts
        )
    except HTTPException as e:
        # v10.0 修复：记录服务不可用等HTTP异常日志
        if e.status_code >= 400:
            await _log_request(
                client_id=client_id, key_id="", model=model,
                status_code=e.status_code, rt=time.time() - _request_start_ts,
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                is_stream=is_stream,
                error_msg=e.detail.get("message", str(e.detail)) if isinstance(e.detail, dict) else str(e.detail),
                error_type="HTTPException",
                error_detail=e.detail.get("message", str(e.detail)) if isinstance(e.detail, dict) else str(e.detail),
                business_code=e.detail.get("code", "http_error") if isinstance(e.detail, dict) else "http_error",
                request_path=str(request.url.path), http_method=request.method,
                client_ip=get_client_ip(request),
                user_agent=request.headers.get("user-agent", "")[:512],
            )
        raise
    except UpstreamRetryableError as e:
        # 重试次数用尽
        scheduler.release_client_request(client_id)
        # v10.0 防呆防傻：记录熔断器失败
        cb = get_circuit_breaker()
        cb_key = f"model:{model}"
        cb.record_failure(cb_key, str(e))

        # v10.0 修复：记录上游重试失败日志
        last_err = str(e)
        if "429" in last_err:
            degradation = graceful_degradation_response("rate_limited")
            _err_type = "RateLimited"
            _biz_code = "upstream_rate_limited"
        elif "timeout" in last_err:
            degradation = graceful_degradation_response("timeout")
            _err_type = "TimeoutError"
            _biz_code = "upstream_timeout"
        elif "conn_error" in last_err:
            degradation = graceful_degradation_response("connection_error")
            _err_type = "ConnectionError"
            _biz_code = "upstream_connection_error"
        elif "403" in last_err:
            degradation = {
                "status_code": 403,
                "message": "上游AI模型访问被拒绝。API密钥权限不足或模型已被禁用，已自动切换到其他可用密钥。",
                "type": "permission_error",
                "code": "upstream_access_denied",
                "suggestion": "请联系管理员检查上游密钥权限和模型白名单配置",
            }
            _err_type = "PermissionError"
            _biz_code = "upstream_access_denied"
        else:
            degradation = {
                "status_code": 502,
                "message": "上游AI模型服务返回错误，已自动重试所有可用密钥后仍然失败。",
                "type": "upstream_error",
                "code": "max_retries_exceeded",
                "suggestion": "请稍后重试，如频繁遇到此错误请联系管理员检查上游服务状态",
            }
            _err_type = "UpstreamError"
            _biz_code = "max_retries_exceeded"
        await _log_request(
            client_id=client_id, key_id="", model=model,
            status_code=degradation["status_code"], rt=time.time() - _request_start_ts,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            is_stream=is_stream, error_msg=last_err[:512],
            error_type=_err_type, error_detail=last_err[:1024],
            business_code=_biz_code,
            request_path=str(request.url.path), http_method=request.method,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        raise HTTPException(status_code=degradation["status_code"], detail=degradation)


# ========== 请求日志记录 ==========

def _log_request_sync(
    client_id: str, key_id: str, model: str, status_code: int,
    rt: float, prompt_tokens: int, completion_tokens: int,
    total_tokens: int, is_stream: bool, error_msg: str,
    start_ts: float = None,
    error_type: str = "", error_detail: str = "", error_stack: str = "",
    business_code: str = "", request_body: str = "", response_body: str = "",
    request_path: str = "", http_method: str = "",
    client_ip: str = "", user_agent: str = "",
    gateway_dispatch_ms: float = 0.0,
):
    """记录请求日志到数据库（毫秒级精准统计 + 全量字段）

    增强：支持全量请求日志字段，完整记录请求/响应/错误详情。
    中间件层已记录基础日志，此函数用于补充业务层数据（client_id、key_id、token统计等）。

    Args:
        gateway_dispatch_ms: 网关调度耗时(密钥选择时间, ms)。如果为0，自动从scheduler._last_dispatch_ms获取
    """
    # 如果未传入gateway_dispatch_ms，尝试从scheduler获取
    if gateway_dispatch_ms == 0.0:
        try:
            from app.scheduler import get_scheduler
            gateway_dispatch_ms = getattr(get_scheduler(), '_last_dispatch_ms', 0.0)
        except Exception:
            gateway_dispatch_ms = 0.0

    try:
        from app.database import localnow
        now_str = localnow()
        latency_ms = round(rt * 1000, 3)
        latency_us = int(rt * 1_000_000)

        # 精确的开始和结束时间（本地CST时间，毫秒精度）
        started_at = ""
        completed_at = ""
        if start_ts is not None:
            from datetime import datetime, timezone, timedelta
            CST = timezone(timedelta(hours=8))
            start_dt = datetime.fromtimestamp(start_ts, tz=CST)
            end_dt = datetime.fromtimestamp(start_ts + rt, tz=CST)
            started_at = start_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{start_dt.microsecond // 1000:03d}+08:00"
            completed_at = end_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{end_dt.microsecond // 1000:03d}+08:00"

        # 日志分类
        log_category = "normal"
        if status_code == 401:
            log_category = "auth_fail"
        elif status_code == 403:
            log_category = "error"
        elif status_code >= 400 or status_code == 0:
            log_category = "error"

        execute(
            """INSERT INTO request_logs
               (id, client_id, upstream_key_id, model, status_code, latency_ms, latency_us, retried,
                prompt_tokens, completion_tokens, total_tokens, is_stream, error_msg, created_at,
                started_at, completed_at,
                request_path, http_method, client_ip, user_agent,
                request_params, request_body, response_body,
                error_type, error_detail, error_stack, business_code, log_category, gateway_dispatch_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                str(uuid.uuid4()), client_id, key_id, model,
                status_code, latency_ms, latency_us,
                prompt_tokens, completion_tokens, total_tokens,
                1 if is_stream else 0, error_msg, now_str,
                started_at, completed_at,
                request_path, http_method, client_ip, user_agent,
                "", request_body, response_body,
                error_type, error_detail, error_stack, business_code, log_category,
                round(gateway_dispatch_ms, 2),
            ),
        )
    except Exception as e:
        logger.error(f"记录请求日志失败: {e}")

    # v2.0: 商用检测数据收集（异步，不阻塞响应）
    try:
        from app.commercial_detect import get_detector
        detector = get_detector()
        if detector.detection_enabled and client_id:
            # 记录Token使用情况（蒸馏行为检测）
            if total_tokens > 0:
                detector.record_token_usage(client_id, prompt_tokens, completion_tokens)
            # 记录请求时间（时间窗口异常检测）
            detector.record_request_time(client_id)
            # 记录IP（账号农场检测）
            if client_ip:
                detector.record_ip(client_id, client_ip)
    except Exception:
        pass  # 商用检测数据收集不应影响主流程


async def _log_request(*args, **kwargs):
    """异步包装器：将同步的 DB 写入操作放到线程池中执行，避免阻塞事件循环"""
    await asyncio.to_thread(_log_request_sync, *args, **kwargs)


# ========== Embeddings带重试上游调用 ==========

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception_type(UpstreamRetryableError),
    reraise=True
)
async def _call_upstream_embeddings(scheduler, client_id, model, url, body):
    """带tenacity重试的上游调用 - 向量化

    每次重试会重新选择密钥，遇到429/5xx/连接错误抛出UpstreamRetryableError触发重试。
    """
    select_result = await asyncio.to_thread(scheduler.select_key, model)
    if not select_result:
        if client_id:
            scheduler.release_client_request(client_id)
        raise HTTPException(status_code=503, detail={
            "message": "服务繁忙，请稍后重试",
            "type": "service_unavailable",
            "code": "all_keys_exhausted",
        })

    key_id, selected_model, api_key = select_result

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start_time = time.time()
    try:
        pool = await scheduler.get_http_pool()
        resp = await pool.post(url, headers=headers, json=body, timeout=60.0)
        rt = time.time() - start_time

        if resp.status_code == 200:
            scheduler.record_response(key_id, model, True, rt, 200, "")
            # v10.0 防呆防傻：记录熔断器成功
            cb = get_circuit_breaker()
            cb.record_success(f"embeddings:{model}")
            return JSONResponse(status_code=200, content=resp.json())
        elif resp.status_code == 429:
            # 429 - 触发桶级冷却并切换密钥重试
            scheduler.record_response(key_id, model, False, rt, 429, "429")
            scheduler.trigger_hard_cooldown(key_id, model, "429")
            logger.warning(f"embeddings上游429: key={key_id[:8]} model={model}")
            raise UpstreamRetryableError(f"429: key={key_id[:8]} model={model}")
        elif resp.status_code >= 500:
            # 5xx - 记录并切换密钥重试
            scheduler.record_response(key_id, model, False, rt, resp.status_code, "5xx")
            logger.warning(f"embeddings上游{resp.status_code}: key={key_id[:8]} model={model}")
            raise UpstreamRetryableError(f"{resp.status_code}: key={key_id[:8]} model={model}")
        else:
            # 4xx（非429）- 直接返回错误
            scheduler.record_response(key_id, model, False, rt, resp.status_code, "4xx")
            try:
                return JSONResponse(status_code=resp.status_code, content=resp.json())
            except Exception:
                return JSONResponse(status_code=resp.status_code, content={"error": {"message": resp.text}})

    except UpstreamRetryableError:
        raise  # 让tenacity处理重试
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        # 连接错误 - 触发隔离并切换密钥重试
        rt = time.time() - start_time
        scheduler.record_response(key_id, model, False, rt, 0, "conn_error")
        scheduler.trigger_isolation(key_id, model, "conn_error")
        logger.warning(f"embeddings连接错误: key={key_id[:8]} model={model} error={e}")
        raise UpstreamRetryableError(f"conn_error: key={key_id[:8]} model={model}")
    except Exception as e:
        rt = time.time() - start_time
        scheduler.record_response(key_id, model, False, rt, 0, "error")
        raise HTTPException(status_code=500, detail={
            "message": f"内部错误: {str(e)}",
            "type": "internal_error",
            "code": "internal_error",
        })


# ========== Embeddings端点 ==========

@router.post("/v1/embeddings", tags=["公共API"])
@router.post("/api/v1/embeddings", tags=["公共API"])
async def embeddings(request: Request):
    """OpenAI兼容向量化端点（tenacity自动重试）"""
    client_info = await asyncio.to_thread(authenticate_client, request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={
            "message": "请求体JSON解析失败",
            "type": "invalid_request_error",
            "code": "invalid_json",
        })

    model = body.get("model", "")
    if not model:
        raise HTTPException(status_code=400, detail={
            "message": "缺少model参数",
            "type": "invalid_request_error",
            "code": "missing_model",
        })

    # === v10.0: embeddings 安全校验已移除 ===

    # v10.0 防呆防傻：embeddings 熔断器检查
    cb = get_circuit_breaker()
    cb_key = f"embeddings:{model}"
    if not cb.can_request(cb_key):
        degradation = graceful_degradation_response("circuit_open")
        raise HTTPException(status_code=degradation["status_code"], detail=degradation)

    scheduler = get_scheduler()
    await scheduler._ensure_pools()

    base_url = get_setting("upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    url = f"{base_url}/embeddings"

    _emb_start_ts = time.time()
    try:
        result = await _call_upstream_embeddings(scheduler, client_info["client_id"], model, url, body)
        cb.record_success(cb_key)
        # v10.0 修复：记录 embeddings 成功日志
        await _log_request(
            client_id=client_info["client_id"], key_id="", model=model,
            status_code=200, rt=time.time() - _emb_start_ts,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            is_stream=False, error_msg="",
            request_path=str(request.url.path), http_method=request.method,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        return result
    except UpstreamRetryableError as e:
        cb.record_failure(cb_key, "embeddings_retry_exhausted")
        degradation = graceful_degradation_response("all_keys_exhausted")
        # v10.0 修复：记录 embeddings 失败日志
        await _log_request(
            client_id=client_info["client_id"], key_id="", model=model,
            status_code=degradation["status_code"], rt=time.time() - _emb_start_ts,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            is_stream=False, error_msg=str(e)[:512],
            error_type="UpstreamRetryableError", error_detail=str(e)[:1024],
            business_code="embeddings_retry_exhausted",
            request_path=str(request.url.path), http_method=request.method,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        raise HTTPException(status_code=degradation["status_code"], detail=degradation)
