"""AI对话路由 - 聊天代理/模型列表/对话历史"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from app.database import execute, fetch_one, fetch_all, utcnow, localnow
from app.security import generate_uuid
from app.gateway_client import GatewayClient
from app.routes.auth import get_current_user

router = APIRouter(prefix="/api/chat", tags=["AI对话"])

# ========== v9.0: 共享HTTP连接池（复用连接，避免每次请求创建新客户端） ==========
_http_pool: httpx.AsyncClient = None

async def _get_http_pool(timeout: float = 600.0) -> httpx.AsyncClient:
    """获取共享HTTP连接池（延迟初始化）"""
    global _http_pool
    if _http_pool is None or _http_pool.is_closed:
        _http_pool = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=30,
                keepalive_expiry=120,
            ),
        )
    return _http_pool

async def _close_http_pool():
    """关闭共享HTTP连接池"""
    global _http_pool
    if _http_pool and not _http_pool.is_closed:
        await _http_pool.aclose()
        _http_pool = None

# 网关客户端实例 - v10.0: 延迟初始化
_gw: GatewayClient = None

def _get_gw() -> GatewayClient:
    global _gw
    if _gw is None:
        _gw = GatewayClient(
            base_url=GATEWAY_BASE_URL,
            platform_token=os.environ.get("AQUA_PLATFORM_TOKEN", ""),
        )
    return _gw

# 网关代理地址
GATEWAY_BASE_URL = os.environ.get("GW_BASE_URL", "http://127.0.0.1:8001")
GATEWAY_CHAT_URL = f"{GATEWAY_BASE_URL}/v1/chat/completions"

# API密钥加解密 - 使用Fernet对称加密
_logger = logging.getLogger("aqua.chat")

_platform_key = os.environ.get("PLATFORM_ENCRYPT_KEY")
if not _platform_key:
    raise RuntimeError(
        "[FATAL] 环境变量 PLATFORM_ENCRYPT_KEY 未设置！"
        "请设置后重新启动，否则已加密的API密钥将无法解密。"
    )
_fernet = Fernet(_platform_key.encode("utf-8"))


def encrypt_api_key(key: str) -> str:
    """使用Fernet对称加密API密钥"""
    return _fernet.encrypt(key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(encrypted: str) -> str:
    """解密API密钥"""
    return _fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")


# ========== 请求模型 ==========

class CreateHistoryRequest(BaseModel):
    title: str
    model: str
    messages: list


class UpdateHistoryRequest(BaseModel):
    title: str
    messages: list


# ========== 辅助函数 ==========

def _error_response(message: str, error_type: str, code: str, status_code: int = 400):
    """生成OpenAI格式错误响应"""
    raise HTTPException(
        status_code=status_code,
        detail={"message": message, "type": error_type, "code": code},
    )


def _get_model_capabilities(model_id: str) -> list:
    """根据模型名称和NIM模型目录判断能力标签"""
    from app.nim_models_compat import get_model_capabilities as _nim_caps
    caps = _nim_caps(model_id)
    if caps:
        return caps

    capabilities = []
    model_lower = model_id.lower()

    # 推理能力
    if any(kw in model_lower for kw in ["deepseek", "glm", "qwen", "kimi", "nemotron", "llama", "mistral", "gpt-oss", "gemma", "minimax", "step"]):
        capabilities.append("推理")

    # 视觉能力
    if any(kw in model_lower for kw in ["vision", "vl", "fuyu", "phi-3.5", "kosmos", "omni"]):
        capabilities.append("视觉")

    # 嵌入能力
    if any(kw in model_lower for kw in ["embed", "bge-m3", "arctic-embed"]):
        capabilities.append("嵌入")

    # 安全检测
    if any(kw in model_lower for kw in ["safety", "guard", "jailbreak", "gliner-pii", "content-safety"]):
        capabilities.append("安全")

    # 代码能力
    if any(kw in model_lower for kw in ["codestral", "starcoder", "codegemma", "granite-.*-code", "coder"]):
        capabilities.append("代码")

    # OCR/文档
    if any(kw in model_lower for kw in ["ocr", "parse", "nemotron-parse", "nemoretriever-parse"]):
        capabilities.append("OCR")

    # TTS/ASR
    if any(kw in model_lower for kw in ["tts", "asr", "parakeet", "canary", "whisper", "voicechat", "studiovoice", "magpie"]):
        capabilities.append("语音")

    # 翻译
    if any(kw in model_lower for kw in ["riva-translate", "nmt"]):
        capabilities.append("翻译")

    # 3D/视频生成
    if any(kw in model_lower for kw in ["cosmos", "flux", "stable-diffusion", "trellis"]):
        capabilities.append("生成")

    # Deprecated
    if any(kw in model_lower for kw in ["gemma-3n", "dracarys", "seed-oss"]):
        capabilities.append("已弃用")

    return capabilities if capabilities else ["推理"]


def _model_sort_priority(model_id: str) -> int:
    """模型排序优先级：国产模型优先，热门模型靠前"""
    from app.nim_models_compat import get_model_sort_priority_compat as _nim_priority
    p = _nim_priority(model_id)
    if p is not None:
        return p
    model_lower = model_id.lower()
    if model_lower.startswith("deepseek"):
        return 0
    if model_lower.startswith("z-ai") or model_lower.startswith("glm"):
        return 1
    if model_lower.startswith("qwen"):
        return 2
    if model_lower.startswith("moonshotai") or model_lower.startswith("kimi"):
        return 3
    if model_lower.startswith("minimaxai"):
        return 4
    if model_lower.startswith("stepfun"):
        return 5
    if model_lower.startswith("openai"):
        return 6
    if model_lower.startswith("meta/llama"):
        return 7
    if model_lower.startswith("mistralai"):
        return 8
    if model_lower.startswith("nvidia/nemotron"):
        return 9
    if model_lower.startswith("google"):
        return 10
    return 20


async def _log_request(
    user_id: int,
    key_id: str,
    model: str,
    is_stream: bool,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: float,
    status: str,
    error_msg: str,
    start_ts: float = None,
):
    """异步记录请求日志（毫秒级精准统计）

    Args:
        start_ts: 请求开始的时间戳(time.time())，用于记录精确的started_at/completed_at
    """
    try:
        from datetime import timezone, timedelta
        CST = timezone(timedelta(hours=8))
        now = localnow()
        latency_us = int(latency_ms * 1000)

        # 精确的开始和结束时间（本地CST时间，毫秒精度）
        if start_ts is not None:
            start_dt = datetime.fromtimestamp(start_ts, tz=CST)
            end_dt = datetime.fromtimestamp(start_ts + latency_ms / 1000, tz=CST)
            started_at = start_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{start_dt.microsecond // 1000:03d}+08:00"
            completed_at = end_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{end_dt.microsecond // 1000:03d}+08:00"
        else:
            started_at = ""
            completed_at = ""

        execute(
            """INSERT INTO request_logs
               (user_id, key_id, model, is_stream, prompt_tokens, completion_tokens,
                total_tokens, latency_ms, status, error_msg, created_at,
                started_at, completed_at, latency_us)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                user_id,
                key_id,
                model,
                1 if is_stream else 0,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                round(latency_ms, 3),
                status,
                error_msg,
                now,
                started_at,
                completed_at,
                latency_us,
            ),
        )
    except Exception:
        pass


def _parse_usage_from_stream(chunks: list) -> dict:
    """从流式响应的chunk中提取usage信息"""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for chunk in chunks:
        try:
            if isinstance(chunk, str):
                if chunk.startswith("data: "):
                    data_str = chunk[6:].strip()
                    if data_str == "[DONE]":
                        continue
                    data = json.loads(data_str)
                else:
                    data = json.loads(chunk)
            else:
                data = chunk

            if "usage" in data and data["usage"]:
                u = data["usage"]
                usage["prompt_tokens"] = u.get("prompt_tokens", 0)
                usage["completion_tokens"] = u.get("completion_tokens", 0)
                usage["total_tokens"] = u.get("total_tokens", 0)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return usage


def _increment_daily_usage(user_id: int):
    """v9.0: 递增用户的日用量计数（仅对新用户有效）"""
    try:
        from app.database import execute as db_exec, today_start_utc
        today = today_start_utc()
        # 原子递增
        db_exec(
            "UPDATE users SET daily_used = daily_used + 1 WHERE id = %s",
            (user_id,),
        )
    except Exception:
        pass


# ========== 联网搜索 ==========

_WEB_SEARCH_CACHE = {}
_WEB_SEARCH_CACHE_TTL = 120  # 搜索结果缓存2分钟

async def _web_search(query: str, max_results: int = 6) -> list:
    """
    使用 DuckDuckGo 执行联网搜索（免费，无需API密钥）

    三级回退策略：
    1. 首选 DuckDuckGo Lite API (速度快，结构稳定)
    2. 回退 DuckDuckGo HTML 搜索 (解析HTML)
    3. 返回空列表

    Returns:
        [{"title": str, "url": str, "content": str, "snippet": str}, ...]
    """
    # 缓存命中
    cache_key = query.strip().lower()
    if cache_key in _WEB_SEARCH_CACHE:
        cached = _WEB_SEARCH_CACHE[cache_key]
        if time.time() - cached["ts"] < _WEB_SEARCH_CACHE_TTL:
            return cached["results"]

    results = []

    try:
        pool = await _get_http_pool()
        # 使用 DuckDuckGo Lite API
        resp = await pool.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AquaBot/1.0)",
                "Accept": "text/html",
            },
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # DuckDuckGo Lite 结果结构：每个结果占3行（标题/摘要/URL），中间隔空行
            # 查找所有带 href 的 a 标签，过滤出外部链接
            seen_urls = set()
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if not href.startswith("http") or "duckduckgo.com" in href:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                # 获取摘要（下一行tr的第二个td）
                snippet = ""
                parent_tr = a.find_parent("tr")
                if parent_tr:
                    next_tr = parent_tr.find_next_sibling("tr")
                    if next_tr:
                        cells = next_tr.find_all("td")
                        if len(cells) >= 2:
                            snippet = cells[1].get_text(strip=True)
                results.append({
                    "title": title[:200],
                    "url": href,
                    "snippet": snippet[:300],
                    "content": snippet[:500],
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        _logger.debug(f"DuckDuckGo Lite搜索失败，尝试HTML搜索: {e}")

    # 回退: DuckDuckGo HTML 搜索
    if not results:
        try:
            pool = await _get_http_pool()
            import urllib.parse
            encoded_q = urllib.parse.quote(query)
            resp = await pool.get(
                f"https://html.duckduckgo.com/html/?q={encoded_q}",
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html",
                },
                follow_redirects=True,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for result_div in soup.select(".result") or soup.select(".web-result"):
                    link = result_div.select_one("a")
                    if link:
                        url = link.get("href", "")
                        # DuckDuckGo 搜索结果链接需要解码
                        if url.startswith("//"):
                            url = "https:" + url
                        title = link.get_text(strip=True)
                        snippet_el = result_div.select_one(".result__snippet") or result_div.select_one(".snippet")
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                        if url and title:
                            results.append({
                                "title": title[:200],
                                "url": url,
                                "snippet": snippet[:300],
                                "content": snippet[:500],
                            })
                            if len(results) >= max_results:
                                break
        except Exception as e:
            _logger.debug(f"DuckDuckGo HTML搜索失败: {e}")

    # 写入缓存
    _WEB_SEARCH_CACHE[cache_key] = {"results": results, "ts": time.time()}

    # 清理过期缓存
    now = time.time()
    stale = [k for k, v in _WEB_SEARCH_CACHE.items() if now - v["ts"] > _WEB_SEARCH_CACHE_TTL * 2]
    for k in stale:
        _WEB_SEARCH_CACHE.pop(k, None)

    return results


# ========== 端点 ==========

@router.get("/models")
async def get_models(request: Request):
    """获取模型列表（可选认证，未登录也可访问）"""
    # 可选认证：未登录也能查看模型列表
    try:
        user = await get_current_user(request)
    except HTTPException:
        user = None

    gw = _get_gw()
    models = await gw.get_models()
    result = []
    for model in models:
        model_id = model.get("id", "")
        # v10.0: 优先使用网关已富化的 display_name，避免与 nim_models_compat 重复
        display_name = model.get("display_name", "")
        # 从网关获取的能力标签（网关的 _enrich_model_list 已基于NIM目录富化）
        gw_capabilities = model.get("capabilities", [])
        # 本地能力推断仅作为 fallback（当网关未返回能力标签时）
        if not gw_capabilities:
            gw_capabilities = _get_model_capabilities(model_id)

        result.append({
            "id": model_id,
            "display_name": display_name,
            "capabilities": gw_capabilities,
            "owned_by": model.get("owned_by", ""),
            "context_length": model.get("context_length"),
            "max_output_tokens": model.get("max_output_tokens"),
            "sort_priority": model.get("sort_priority", _model_sort_priority(model_id)),
        })

    # 排序：国产模型优先
    result.sort(key=lambda m: m.get("sort_priority", 999))

    return result


@router.post("/completions")
async def chat_completions(request: Request):
    """聊天代理 - 代理用户请求到网关"""
    user = await get_current_user(request)

    # 获取用户密钥（含加密数据回填）
    key_row = fetch_one(
        """SELECT id, gw_client_id, gw_key_id, api_key_encrypted, key_prefix FROM user_api_keys
           WHERE user_id=%s AND status='active' LIMIT 1""",
        (user["id"],),
    )
    if not key_row:
        _error_response("没有可用的API密钥", "forbidden", "no_api_key", 403)

    api_key = None
    if key_row["api_key_encrypted"]:
        try:
            api_key = decrypt_api_key(key_row["api_key_encrypted"])
        except Exception as e:
            _logger.warning(f"密钥解密失败，尝试网关回填: {e}")

    # 本地没有加密数据，从网关获取并回填
    if not api_key and key_row["gw_client_id"]:
        try:
            # 获取该客户端的所有密钥
            gw_keys = await _gw.list_client_keys(key_row["gw_client_id"])
            if gw_keys:
                # 优先匹配 gw_key_id，否则取第一个active的密钥
                target_key = None
                for gk in gw_keys:
                    if key_row["gw_key_id"] and gk.get("id") == key_row["gw_key_id"]:
                        target_key = gk
                        break
                if not target_key:
                    for gk in gw_keys:
                        if gk.get("status") == "active":
                            target_key = gk
                            break
                if not target_key:
                    target_key = gw_keys[0]

                gw_key_id = target_key.get("id", "")
                # 从网关reveal获取明文
                gw = _get_gw()
                reveal_result = await gw.reveal_client_key(key_row["gw_client_id"], gw_key_id)
                api_key = reveal_result.get("key", "")
                if api_key:
                    # 回填本地加密数据和gw_key_id
                    encrypted = encrypt_api_key(api_key)
                    execute(
                        "UPDATE user_api_keys SET api_key_encrypted=%s, gw_key_id=%s WHERE id=%s",
                        (encrypted, gw_key_id, key_row["id"]),
                    )
                    _logger.info(f"密钥回填成功: key_id={key_row['id']}")
        except Exception as e:
            _logger.error(f"网关密钥回填失败: {e}")

    if not api_key:
        _error_response("API密钥数据不可用，请重新创建密钥", "server_error", "key_data_missing", 500)

    # === v11.0: 已移除所有并发限制和软限速 ===
    import uuid
    request_id = str(uuid.uuid4())

    # 读取请求体
    body = await request.json()

    # 注入stream_options
    if body.get("stream", False):
        body["stream_options"] = {"include_usage": True}

    # === v10.0: 联网搜索 ===
    web_search_enabled = body.pop("web_search", False)
    search_results = []
    if web_search_enabled:
        try:
            # 获取最后一条用户消息
            messages = body.get("messages", [])
            last_user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user_msg = m.get("content", "")[:500]
                    break
            if last_user_msg:
                search_results = await _web_search(last_user_msg)
                if search_results:
                    # 构造搜索上下文
                    context_lines = ["以下是来自互联网的最新搜索结果，请基于这些信息回答：\n"]
                    for i, r in enumerate(search_results, 1):
                        context_lines.append(f"[{i}] {r['title']}")
                        context_lines.append(f"    来源: {r['url']}")
                        context_lines.append(f"    摘要: {r['snippet']}\n")
                    context = "\n".join(context_lines)
                    # 将搜索上下文注入到消息中（作为 system 消息插入）
                    body["messages"].insert(0, {
                        "role": "system",
                        "content": context,
                    })
                    _logger.info(f"联网搜索成功: query='{last_user_msg[:50]}' results={len(search_results)}")
        except Exception as e:
            _logger.warning(f"联网搜索失败: {e}")

    model = body.get("model", "unknown")
    is_stream = body.get("stream", False)
    key_id = key_row["gw_key_id"]
    start_time = time.time()

    if is_stream:
        # 流式代理
        collected_chunks = []
        search_sent = False

        async def stream_generator():
            nonlocal search_sent
            # 先发送搜索结果（如果有）
            if search_results:
                search_sent = True
                search_event = {
                    "search_results": [
                        {"title": r["title"], "url": r["url"], "snippet": r["snippet"]}
                        for r in search_results
                    ]
                }
                yield f"data: {json.dumps(search_event)}\n\n"
            try:
                pool = await _get_http_pool()
                async with pool.stream(
                    "POST",
                    GATEWAY_CHAT_URL,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        yield f"data: {error_body.decode()}\n\n"
                        latency = (time.time() - start_time) * 1000
                        await _log_request(
                            user["id"], key_id, model, True, 0, 0, 0,
                            latency, "error", error_body.decode()[:500],
                            start_ts=start_time,
                        )
                        return

                    async for line in resp.aiter_lines():
                        if line:
                            collected_chunks.append(line)
                            yield line + "\n\n"

                # 流结束后记录日志
                latency = (time.time() - start_time) * 1000
                usage = _parse_usage_from_stream(collected_chunks)
                await _log_request(
                    user["id"],
                    key_id,
                    model,
                    True,
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                    usage["total_tokens"],
                    latency,
                    "success",
                    "",
                    start_ts=start_time,
                )
                # 成功：增加日用量
                _increment_daily_usage(user["id"])
            except httpx.ConnectError:
                latency = (time.time() - start_time) * 1000
                _logger.error(f"网关连接失败: {GATEWAY_CHAT_URL}")
                yield f"data: {json.dumps({'error': {'message': '网关服务暂不可用', 'type': 'server_error', 'code': 'gateway_unavailable'}})}\n\n"
                yield "data: [DONE]\n\n"
                await _log_request(
                    user["id"], key_id, model, True, 0, 0, 0,
                    latency, "error", "网关连接失败",
                    start_ts=start_time,
                )
            except Exception as e:
                latency = (time.time() - start_time) * 1000
                await _log_request(
                    user["id"], key_id, model, True, 0, 0, 0,
                    latency, "error", str(e)[:500],
                    start_ts=start_time,
                )
            finally:
                pass

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )
    else:
        # 非流式代理（v9.0: 使用共享连接池）
        try:
            pool = await _get_http_pool()
            resp = await pool.post(
                GATEWAY_CHAT_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )

            latency = (time.time() - start_time) * 1000
            resp_data = resp.json()

            # 提取usage
            usage = resp_data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0) if usage else 0
            completion_tokens = usage.get("completion_tokens", 0) if usage else 0
            total_tokens = usage.get("total_tokens", 0) if usage else 0

            status = "success" if resp.status_code == 200 else "error"
            error_msg = "" if resp.status_code == 200 else json.dumps(resp_data, ensure_ascii=False)[:500]

            await _log_request(
                user["id"],
                key_id,
                model,
                False,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                latency,
                status,
                error_msg,
                start_ts=start_time,
            )

            # v9.0: 成功时增加日用量
            if resp.status_code == 200:
                _increment_daily_usage(user["id"])

            # v10.0: 非流式响应注入搜索结果
            if search_results and isinstance(resp_data, dict):
                resp_data["search_results"] = [
                    {"title": r["title"], "url": r["url"], "snippet": r["snippet"]}
                    for r in search_results
                ]
            return JSONResponse(content=resp_data, status_code=resp.status_code)
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            await _log_request(
                user["id"], key_id, model, False, 0, 0, 0,
                latency, "error", str(e)[:500],
                start_ts=start_time,
            )
            return JSONResponse(
                content={
                    "error": {
                        "message": str(e),
                        "type": "server_error",
                        "code": "proxy_error",
                    }
                },
                status_code=502,
            )


@router.get("/history")
async def list_history(request: Request):
    """获取对话历史列表"""
    user = await get_current_user(request)

    histories = fetch_all(
        """SELECT id, title, model, created_at, updated_at
           FROM chat_history WHERE user_id=%s
           ORDER BY updated_at DESC""",
        (user["id"],),
    )
    return histories


@router.get("/history/{history_id}")
async def get_history(history_id: str, request: Request):
    """获取对话历史详情"""
    user = await get_current_user(request)

    history = fetch_one(
        "SELECT * FROM chat_history WHERE id=%s AND user_id=%s",
        (history_id, user["id"]),
    )
    if not history:
        _error_response("对话历史不存在", "not_found", "history_not_found", 404)

    # 解析messages JSON
    messages = history["messages"]
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except json.JSONDecodeError:
            messages = []

    return {
        "id": history["id"],
        "title": history["title"],
        "model": history["model"],
        "messages": messages,
        "created_at": history["created_at"],
        "updated_at": history["updated_at"],
    }


@router.post("/history")
async def create_history(req: CreateHistoryRequest, request: Request):
    """创建对话历史"""
    user = await get_current_user(request)

    history_id = generate_uuid()
    now = utcnow()
    messages_json = json.dumps(req.messages, ensure_ascii=False)

    execute(
        """INSERT INTO chat_history (id, user_id, title, messages, model, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (history_id, user["id"], req.title, messages_json, req.model, now, now),
    )

    return {"id": history_id}


@router.put("/history/{history_id}")
async def update_history(history_id: str, req: UpdateHistoryRequest, request: Request):
    """更新对话历史"""
    user = await get_current_user(request)

    existing = fetch_one(
        "SELECT id FROM chat_history WHERE id=%s AND user_id=%s",
        (history_id, user["id"]),
    )
    if not existing:
        _error_response("对话历史不存在", "not_found", "history_not_found", 404)

    now = utcnow()
    messages_json = json.dumps(req.messages, ensure_ascii=False)

    execute(
        "UPDATE chat_history SET title=%s, messages=%s, updated_at=%s WHERE id=%s",
        (req.title, messages_json, now, history_id),
    )

    return {"message": "已更新"}


@router.delete("/history/{history_id}")
async def delete_history(history_id: str, request: Request):
    """删除对话历史"""
    user = await get_current_user(request)

    existing = fetch_one(
        "SELECT id FROM chat_history WHERE id=%s AND user_id=%s",
        (history_id, user["id"]),
    )
    if not existing:
        _error_response("对话历史不存在", "not_found", "history_not_found", 404)

    execute("DELETE FROM chat_history WHERE id=%s", (history_id,))

    return {"message": "已删除"}
