"""
模型连通性测试 - 控制台「模型测试」页后端

两条测试通道：
  1. upstream（直连 NVIDIA 上游）：由本模块代为请求——调度器选一个健康上游密钥，
     走该密钥自己的出网通道（direct/bind/rotate），浏览器不接触上游密钥明文。
  2. gateway（走网关中转）：前端持「内置自测密钥」直接请求本网关 /v1/chat/completions，
     完整经过下游认证 → 调度选池 → 转发 → 落日志的真实链路，后端只负责下发该密钥。

内置自测密钥：归属专用客户 __console_selftest__，首次索取时随机生成（sk- + 32 位），
加密落库后固定复用同一把，可随时轮换。它与普通下游密钥完全等价——同样计入调度、
限流、请求日志与商用检测，因此仅用于控制台自测，不应外发。

批量测试的编排（选模型、并发、逐条更新、中止）在前端完成：本模块只提供单模型探测，
避免长任务占住事件循环，也让 gateway 通道走的是浏览器 → 网关的真实请求。
批量场景下不逐条写审计（否则一次全模型测试会刷爆 audit_logs），只审计密钥下发与轮换。

端点（均需管理员 Token）：
  GET  /gw/admin/model-test/models              实时模型列表（refresh=1 强制回源）
  GET  /gw/admin/model-test/selftest-key        取内置自测密钥（不存在则随机生成）
  POST /gw/admin/model-test/selftest-key/rotate 轮换内置自测密钥
  POST /gw/admin/model-test/probe               单模型直连上游探测
"""
import asyncio
import logging
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.admin_api import require_admin
from app.database import execute, fetch_one, get_setting, insert_audit, utcnow
from app.proxy_pool import proxy_pool
from app.request_validator import normalize_base_url
from app.scheduler import get_scheduler
from app.security import (
    decrypt_secret, encrypt_secret, generate_client_key, hash_secret, mask_secret,
)

logger = logging.getLogger("acu.model_test")

router = APIRouter(prefix="/gw/admin/model-test", tags=["模型测试"])

# 默认测试提示词（控制台可改写；空提示词回落到这句）
DEFAULT_TEST_PROMPT = "你是什么模型，你可以帮我干什么事情"

# 内置自测客户名：双下划线前缀标识「非真实客户，控制台自用」
SELFTEST_CLIENT_NAME = "__console_selftest__"

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
PROBE_TIMEOUT = 60.0        # 单模型探测超时（秒）
PROMPT_MAX_CHARS = 2000     # 提示词长度上限（测试用途，无需长文）
MAX_TOKENS_LIMIT = 512      # max_tokens 上限（连通性测试不需要长输出）
DEFAULT_MAX_TOKENS = 256    # 默认输出预算：推理模型的思维链会先吃掉配额，太小拿不到正文
REPLY_MAX_CHARS = 300       # 回复摘要截断长度


# ========== 纯函数（无 I/O，便于单测） ==========

def normalize_prompt(prompt: Optional[str]) -> str:
    """提示词归一：空/纯空白回落默认值，超长截断"""
    text = (prompt or "").strip()
    if not text:
        text = DEFAULT_TEST_PROMPT
    return text[:PROMPT_MAX_CHARS]


def clamp_max_tokens(value: Optional[int]) -> int:
    """max_tokens 收敛到 [1, MAX_TOKENS_LIMIT]，非法值取默认值"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return max(1, min(n, MAX_TOKENS_LIMIT))


def extract_reply(data: dict) -> str:
    """从 chat/completions 响应里取回复摘要

    兼容三种形态：常规 content、推理模型只给 reasoning_content、
    以及 completions 风格的 text。取不到返回空串。
    """
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    msg = first.get("message") if isinstance(first.get("message"), dict) else {}
    for candidate in (msg.get("content"), msg.get("reasoning_content"), first.get("text")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:REPLY_MAX_CHARS]
    return ""


def extract_error(data: dict) -> str:
    """从错误响应里提取可读错误信息（上游错误结构不统一）"""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("detail") or err.get("type")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:REPLY_MAX_CHARS]
    if isinstance(err, str) and err.strip():
        return err.strip()[:REPLY_MAX_CHARS]
    for field in ("detail", "message", "title", "raw"):
        val = data.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()[:REPLY_MAX_CHARS]
    return ""


# ========== 内置自测密钥 ==========

def _ensure_selftest_key_sync(rotate: bool = False) -> dict:
    """确保内置自测密钥存在并返回明文（同步，整组 DB 操作在一次 to_thread 内完成）

    rotate=True 时先清掉该客户名下所有旧密钥再重建。
    密文解不开（主密钥换过）时同样重建，避免控制台卡在「密钥不可用」。
    """
    master_key = get_setting("upstream_master_key")
    if not master_key:
        raise HTTPException(status_code=500, detail="主密钥未配置，无法生成自测密钥")

    now = utcnow()
    client = fetch_one("SELECT id, status FROM clients WHERE name = %s", (SELFTEST_CLIENT_NAME,))
    client_created = False
    if not client:
        client_id = str(uuid.uuid4())
        execute(
            "INSERT INTO clients (id, name, user_type, status, created_at, updated_at) "
            "VALUES (%s, %s, 'old', 'active', %s, %s)",
            (client_id, SELFTEST_CLIENT_NAME, now, now),
        )
        client_created = True
    else:
        client_id = client["id"]
        if client["status"] != "active":
            # 自测通道要求客户处于 active（认证时 client 与 key 都必须 active）
            execute("UPDATE clients SET status = 'active', updated_at = %s WHERE id = %s", (now, client_id))

    row = None
    if rotate:
        execute("DELETE FROM client_api_keys WHERE client_id = %s", (client_id,))
    else:
        row = fetch_one(
            "SELECT id, key_ciphertext, key_prefix FROM client_api_keys "
            "WHERE client_id = %s AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (client_id,),
        )

    if row:
        try:
            plaintext = decrypt_secret(row["key_ciphertext"] or "", master_key)
        except Exception:
            plaintext = ""
        if plaintext.startswith("sk-"):
            return {
                "client_id": client_id, "key_id": row["id"], "key": plaintext,
                "key_prefix": row["key_prefix"], "reused": True, "rotated": False,
                "client_created": client_created,
            }
        logger.warning("内置自测密钥无法复原（主密钥可能已更换），重建一把")
        execute("DELETE FROM client_api_keys WHERE id = %s", (row["id"],))

    api_key = generate_client_key()
    key_id = str(uuid.uuid4())
    execute(
        "INSERT INTO client_api_keys "
        "(id, client_id, key_hash, key_prefix, key_ciphertext, status, created_at, last_used_at) "
        "VALUES (%s, %s, %s, %s, %s, 'active', %s, NULL)",
        (key_id, client_id, hash_secret(api_key), mask_secret(api_key),
         encrypt_secret(api_key, master_key), now),
    )
    return {
        "client_id": client_id, "key_id": key_id, "key": api_key,
        "key_prefix": mask_secret(api_key), "reused": False, "rotated": rotate,
        "client_created": client_created,
    }


# ========== 端点：实时模型列表 ==========

@router.get("/models", tags=["模型测试"])
async def list_test_models(request: Request, refresh: int = Query(0, ge=0, le=1)):
    """实时模型列表（复用公开接口的取数与富化逻辑，refresh=1 跳过 60 秒缓存回源）"""
    await require_admin(request)

    from app.public_api import _enrich_model_list, _models_cache, get_model_list

    from_cache = bool(_models_cache["data"]) and _models_cache["expires"] > time.time()
    if refresh:
        _models_cache["expires"] = 0      # 强制回源，与 /gw/admin/sync-models 同一手法
        from_cache = False

    # 与下游客户看到的列表一致（已剔除管理员隐藏项、含手动补录项）
    enriched = _enrich_model_list(await get_model_list())
    models = [
        {
            "id": m.get("id", ""),
            "display_name": m.get("display_name") or m.get("id", ""),
            "capabilities": m.get("capabilities") or [],
            "context_length": m.get("context_length"),
        }
        for m in enriched if m.get("id")
    ]
    return {
        "count": len(models), "models": models,
        "from_cache": from_cache, "fetched_at": utcnow(),
        "default_prompt": DEFAULT_TEST_PROMPT,
    }


# ========== 端点：内置自测密钥 ==========

@router.get("/selftest-key", tags=["模型测试"])
async def get_selftest_key(request: Request):
    """取内置自测密钥明文（不存在则随机生成一把并落库）"""
    await require_admin(request)
    info = await asyncio.to_thread(_ensure_selftest_key_sync, False)
    if not info["reused"]:
        get_scheduler().invalidate_client_key_cache()
        get_scheduler().invalidate_active_keys_cache()
        await asyncio.to_thread(
            insert_audit, "create", "client_key", info["key_id"],
            f"生成内置自测密钥: {info['key_prefix']} (client={SELFTEST_CLIENT_NAME})",
        )
    return {
        "key": info["key"], "key_prefix": info["key_prefix"],
        "key_id": info["key_id"], "client_id": info["client_id"],
        "client_name": SELFTEST_CLIENT_NAME, "created": not info["reused"],
    }


@router.post("/selftest-key/rotate", tags=["模型测试"])
async def rotate_selftest_key(request: Request):
    """轮换内置自测密钥（删掉旧的，重新随机生成一把）"""
    await require_admin(request)
    info = await asyncio.to_thread(_ensure_selftest_key_sync, True)
    get_scheduler().invalidate_client_key_cache()
    get_scheduler().invalidate_active_keys_cache()
    await asyncio.to_thread(
        insert_audit, "update", "client_key", info["key_id"],
        f"轮换内置自测密钥: {info['key_prefix']} (client={SELFTEST_CLIENT_NAME})",
    )
    return {
        "key": info["key"], "key_prefix": info["key_prefix"],
        "key_id": info["key_id"], "client_id": info["client_id"],
        "client_name": SELFTEST_CLIENT_NAME, "created": True,
    }


# ========== 端点：单模型直连上游探测 ==========

class ProbeRequest(BaseModel):
    """单模型探测请求（extra=forbid：拼错字段直接 422，避免静默忽略）"""
    model_config = ConfigDict(extra="forbid")

    model: str
    prompt: Optional[str] = None
    max_tokens: Optional[int] = DEFAULT_MAX_TOKENS


@router.post("/probe", tags=["模型测试"])
async def probe_model(req: ProbeRequest, request: Request):
    """直连上游探测单个模型：调度器选健康密钥，走该密钥自己的出网通道

    只回结构化结果，不回上游密钥明文、不回代理 URL（含账号密码）。
    并发编排在前端，本端点保持单次请求语义。
    """
    await require_admin(request)

    model = (req.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model 不能为空")

    prompt = normalize_prompt(req.prompt)
    max_tokens = clamp_max_tokens(req.max_tokens)

    select_result = await asyncio.to_thread(get_scheduler().select_any_key)
    if not select_result:
        raise HTTPException(status_code=503, detail="所有上游密钥暂不可用，无法执行直连测试")
    key_id, api_key = select_result

    base_url, _ = normalize_base_url(
        await asyncio.to_thread(get_setting, "upstream_base_url") or DEFAULT_BASE_URL
    )
    chat_path = await asyncio.to_thread(get_setting, "chat_path") or "/chat/completions"
    url = f"{base_url}{chat_path}"

    # egress 只暴露「是否经代理」，代理 URL 内嵌账号密码，绝不外发
    egress = "proxy" if await proxy_pool.resolve_url(key_id) else "direct"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    result = {
        "model": model, "channel": "upstream", "egress": egress,
        "key_masked": mask_secret(api_key), "prompt_used": prompt,
    }

    started = time.monotonic()
    try:
        client = await get_scheduler().get_http_pool(key_id)
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body, timeout=PROBE_TIMEOUT,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:REPLY_MAX_CHARS]}
        ok = resp.status_code == 200 and bool(extract_reply(data))
        result.update({
            "ok": ok, "status_code": resp.status_code, "latency_ms": latency_ms,
            "reply": extract_reply(data),
            "error": "" if ok else (extract_error(data) or f"HTTP {resp.status_code}"),
        })
        return result
    except httpx.TimeoutException:
        result.update({
            "ok": False, "status_code": 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": "", "error": f"请求超时（>{int(PROBE_TIMEOUT)}s）",
        })
        return result
    except Exception as e:
        result.update({
            "ok": False, "status_code": 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": "", "error": f"{type(e).__name__}: {e}"[:REPLY_MAX_CHARS],
        })
        return result
