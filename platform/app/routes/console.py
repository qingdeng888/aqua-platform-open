"""用户控制台路由 - 密钥管理/用量统计/请求日志/个人设置"""
import asyncio
import logging
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.database import execute, fetch_one, fetch_all, utcnow, today_start_utc, days_ago_utc
from app.security import generate_uuid
from app.gateway_client import GatewayClient
from app.routes.auth import get_current_user
from app.routes.platform_admin import require_admin

router = APIRouter(prefix="/api/user", tags=["用户控制台"])

_GATEWAY_BASE = "http://127.0.0.1:8000"
_GW_TOKEN = os.environ.get("AQUA_PLATFORM_TOKEN", "")


async def _get_gateway_special_status(gw_client_id: str) -> dict:
    """从网关查询客户端的特殊状态（临时标签/并发覆盖等）"""
    if not gw_client_id or not _GW_TOKEN:
        return {"is_special": False, "concurrency_limit": 0, "tag": "", "reason": ""}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{_GATEWAY_BASE}/gw/admin/clients/{gw_client_id}/special-status",
                headers={"Authorization": f"Bearer {_GW_TOKEN}"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug(f"查询网关特殊状态失败: {e}")
    return {"is_special": False, "concurrency_limit": 0, "tag": "", "reason": ""}


# 网关客户端实例
_gw = GatewayClient(
    base_url=_GATEWAY_BASE,
    platform_token=_GW_TOKEN,
)

logger = logging.getLogger("aqua.console")

def _pg_gw_conn():
    host = os.environ.get("PG_GATEWAY_HOST", "localhost")
    port = int(os.environ.get("PG_GATEWAY_PORT", "5432"))
    db = os.environ.get("PG_GATEWAY_DB", "aqua_gateway")
    user = os.environ.get("PG_GATEWAY_USER", "aqua")
    password = os.environ.get("PG_GATEWAY_PASSWORD", "")
    conn = psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)
    conn.autocommit = True
    return conn


def _gw_fetch_one(sql: str, params: tuple = ()) -> dict | None:
    conn = None
    try:
        conn = _pg_gw_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        return None
    finally:
        if conn: conn.close()


def _gw_fetch_all(sql: str, params: tuple = ()) -> list:
    conn = None
    try:
        conn = _pg_gw_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        return []
    finally:
        if conn: conn.close()


# ========== 请求模型 ==========

class CreateKeyRequest(BaseModel):
    label: str = ""


class UpdateSettingsRequest(BaseModel):
    display_name: str


# ========== 辅助函数 ==========

def _error_response(message: str, error_type: str, code: str, status_code: int = 400):
    """生成OpenAI格式错误响应"""
    raise HTTPException(
        status_code=status_code,
        detail={"message": message, "type": error_type, "code": code},
    )


# ========== 端点 ==========

@router.get("/profile")
async def get_profile(request: Request):
    """获取用户资料（含 v9.0 用户类型和并发信息）"""
    user = await get_current_user(request)

    # v9.0: 获取实时并发数
    concurrency_current = 0
    try:
        from app.concurrency import get_concurrency_controller
        concurrency_current = get_concurrency_controller().get_current(user["id"])
    except ImportError:
        pass

    # v9.2: 查询网关特殊状态（临时并发提升/考察期标签）
    special_status = {"is_special": False, "concurrency_limit": 0, "tag": "", "reason": ""}
    gw_client_id = user.get("gw_client_id", "")
    if gw_client_id:
        special_status = await _get_gateway_special_status(gw_client_id)
    concurrency_limit = special_status.get("concurrency_limit", 0) or 5

    return {
        "id": user["id"],
        "uuid": user["uuid"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
        "status": user["status"],
        "created_at": user["created_at"],
        "user_type": user.get("user_type", "old"),
        "daily_limit": user.get("daily_limit", -1),
        "daily_used": user.get("daily_used", 0) or 0,
        "concurrency_limit": concurrency_limit,
        "concurrency_current": concurrency_current,
        "special_tag": special_status.get("tag", ""),
        "special_reason": special_status.get("reason", ""),
    }


@router.get("/concurrency-stats")
async def get_concurrency_stats(request: Request):
    """v9.2: 获取用户实时并发统计（当前并发/限制/峰值/标签）"""
    user = await get_current_user(request)
    gw_client_id = user.get("gw_client_id", "")
    default = {"current": 0, "limit": 5, "limit_label": "5", "peak": 0,
               "tag": "", "is_special": False, "reason": ""}
    if not gw_client_id or not _GW_TOKEN:
        return default
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{_GATEWAY_BASE}/gw/admin/clients/{gw_client_id}/concurrency-stats",
                headers={"Authorization": f"Bearer {_GW_TOKEN}"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return default


@router.get("/usage-limits")
async def get_usage_limits(request: Request):
    """v9.0: 获取用户使用限制说明"""
    user = await get_current_user(request)
    user_type = user.get("user_type", "old")
    daily_limit = user.get("daily_limit", -1) or -1
    daily_used = user.get("daily_used", 0) or 0

    # v9.2: 查询网关特殊状态
    special_status = {"is_special": False, "concurrency_limit": 0, "tag": "", "reason": ""}
    gw_client_id = user.get("gw_client_id", "")
    if gw_client_id:
        special_status = await _get_gateway_special_status(gw_client_id)
    # v10.0: 所有用户默认5并发，特殊用户由网关覆盖
    concurrency_limit = special_status.get("concurrency_limit", 0) or 5
    is_special = special_status.get("is_special", False)
    special_tag = special_status.get("tag", "")

    limits = {
        "user_type": user_type,
        "user_type_label": "老用户" if user_type == "old" else "新用户",
        "concurrency_limit": concurrency_limit,
        "daily_limit": daily_limit,
        "daily_used": daily_used,
        "daily_remaining": -1 if daily_limit == -1 else max(0, daily_limit - daily_used),
        "speed_limited": False,  # v10.0: 全部不限速
        "is_special": is_special,
        "special_tag": special_tag,
        "special_reason": special_status.get("reason", ""),
        "description": "",
    }

    if is_special:
        limits["description"] = (
            f"您的账号当前为考察期用户（{special_tag}）。\n"
            f"- 并发额度：{concurrency_limit}个并发\n"
            f"- 每日使用量：不限量\n"
            f"- 访问速度：不受限制\n\n"
            f"注意：考察期间将进行行为监测，后续将根据使用情况调整额度。"
        )
    else:
        limits["description"] = (
            "当前所有用户享有以下权益：\n"
            "- 每日使用量：不限量\n"
            "- 访问速度：不受限制\n"
            f"- 并发请求数：{concurrency_limit}个\n\n"
            "注意：所有用户均受防商业算法管控，异常使用行为可能导致IP被临时限制。"
        )

    return limits


@router.get("/keys")
async def list_keys(request: Request):
    """列出用户API密钥"""
    user = await get_current_user(request)
    keys = await asyncio.to_thread(
        fetch_all,
        """SELECT id, key_prefix, label, status, created_at
           FROM user_api_keys WHERE user_id=%s ORDER BY created_at DESC""",
        (user["id"],),
    )
    return keys


@router.post("/keys")
async def create_key(req: CreateKeyRequest, request: Request):
    """创建API密钥"""
    user = await get_current_user(request)

    # 检查密钥数量限制(仅计算活跃密钥)
    existing = await asyncio.to_thread(
        fetch_all,
        "SELECT id FROM user_api_keys WHERE user_id=%s AND status='active'",
        (user["id"],),
    )
    if len(existing) >= 5:
        _error_response("最多只能创建5个API密钥", "forbidden", "key_limit_reached", 403)

    # 获取用户的gw_client_id（优先从users表获取，其次从密钥表获取）
    user_gw_client = user.get("gw_client_id", "") or await asyncio.to_thread(
        fetch_one,
        "SELECT gw_client_id FROM user_api_keys WHERE user_id=%s AND gw_client_id != '' LIMIT 1",
        (user["id"],),
    )
    existing_gw_client_id = ""
    if isinstance(user_gw_client, str) and user_gw_client:
        existing_gw_client_id = user_gw_client
    elif isinstance(user_gw_client, dict) and user_gw_client.get("gw_client_id"):
        existing_gw_client_id = user_gw_client["gw_client_id"]

    gw_client_id = None
    if existing_gw_client_id:
        # 尝试使用已有的客户端ID
        try:
            key_result = await _gw.create_client_key(existing_gw_client_id)
            gw_client_id = existing_gw_client_id
        except Exception:
            gw_client_id = None  # 客户端不存在，需要重新创建

    if not gw_client_id:
        # 创建新的网关客户端（统一命名格式：用户名(ID:纯数字ID)）
        try:
            client_name = f"{user['username']}(ID:{user['id']})"
            client_result = await _gw.create_client(client_name)
            gw_client_id = client_result.get("id") or client_result.get("client_id", "")
            if not gw_client_id:
                _error_response("创建网关客户端失败", "server_error", "gateway_client_failed", 500)
            # 保存到users表
            await asyncio.to_thread(execute, "UPDATE users SET gw_client_id=%s WHERE id=%s", (gw_client_id, user["id"]))
        except Exception as e:
            _error_response(f"创建网关客户端失败: {e}", "server_error", "gateway_client_failed", 500)

        # 通过新客户端创建密钥
        try:
            key_result = await _gw.create_client_key(gw_client_id)
        except Exception as e:
            _error_response(f"创建密钥失败: {e}", "server_error", "gateway_key_failed", 500)

    gw_key_id = key_result.get("id", "")
    api_key = key_result.get("key", "")
    key_prefix = key_result.get("key_prefix", api_key[:12] + "..." if api_key else "")

    # 强制命名规范：客户平台ID+平台ID号
    key_name = f"{gw_client_id[:8]}+{gw_key_id[:8]}"

    # 加密存储完整密钥
    from app.routes.chat import encrypt_api_key
    api_key_encrypted = encrypt_api_key(api_key)

    # 存入数据库（含补偿回滚逻辑）
    now = utcnow()
    record_id = generate_uuid()
    try:
        await asyncio.to_thread(
            execute,
            """INSERT INTO user_api_keys (id, user_id, gw_client_id, gw_key_id, key_prefix, label, status, created_at, api_key_encrypted)
               VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)""",
            (record_id, user["id"], gw_client_id, gw_key_id, key_prefix, req.label, now, api_key_encrypted),
        )
    except Exception as e:
        # 本地写入失败，回滚网关侧已创建的密钥
        logger.error(f"本地数据库写入失败，回滚网关密钥: {e}")
        try:
            await _gw.delete_client_key(gw_client_id, gw_key_id)
        except Exception as rollback_err:
            logger.error(f"网关密钥回滚失败: {rollback_err}")
        _error_response(f"创建密钥失败: {e}", "server_error", "db_write_failed", 500)

    return {
        "id": record_id,
        "key": api_key,
        "key_prefix": key_prefix,
        "key_name": key_name,
        "label": req.label,
        "message": "密钥已创建，请妥善保存",
    }


@router.get("/keys/{key_id}/reveal")
async def reveal_key(key_id: str, request: Request):
    """解密并返回完整API密钥（用户点击"小眼睛"时调用）"""
    user = await get_current_user(request)
    key_row = await asyncio.to_thread(
        fetch_one,
        "SELECT id, api_key_encrypted, key_prefix, user_id, status, gw_client_id, gw_key_id FROM user_api_keys WHERE id=%s AND user_id=%s",
        (key_id, user["id"]),
    )
    if not key_row:
        _error_response("密钥不存在", "not_found", "key_not_found", 404)

    full_key = None

    # 优先从本地加密数据解密
    if key_row["api_key_encrypted"]:
        try:
            from app.routes.chat import decrypt_api_key
            full_key = decrypt_api_key(key_row["api_key_encrypted"])
        except Exception as e:
            logger.warning(f"本地密钥解密失败，尝试网关回填: {e}")

    # 本地没有加密数据，从网关获取并回填
    if not full_key and key_row["gw_client_id"] and key_row["gw_key_id"]:
        try:
            gw_result = await _gw.reveal_client_key(key_row["gw_client_id"], key_row["gw_key_id"])
            full_key = gw_result.get("key", "")
            if full_key:
                # 回填本地加密存储
                from app.routes.chat import encrypt_api_key
                encrypted = encrypt_api_key(full_key)
                await asyncio.to_thread(execute, "UPDATE user_api_keys SET api_key_encrypted=%s WHERE id=%s", (encrypted, key_id))
                logger.info(f"密钥回填成功: key_id={key_id}")
        except Exception as e:
            logger.error(f"网关密钥回填失败: {e}")
            _error_response("获取密钥失败，网关不可用", "server_error", "gateway_unavailable", 502)

    if not full_key:
        _error_response("密钥数据不可用", "server_error", "key_data_missing", 500)

    return {"key": full_key, "key_prefix": key_row["key_prefix"], "status": key_row["status"]}


@router.put("/keys/{key_id}/toggle")
async def toggle_key_status(key_id: str, request: Request):
    """启用/禁用API密钥"""
    user = await get_current_user(request)
    key_row = await asyncio.to_thread(
        fetch_one,
        "SELECT id, gw_client_id, gw_key_id, status FROM user_api_keys WHERE id=%s AND user_id=%s",
        (key_id, user["id"]),
    )
    if not key_row:
        _error_response("密钥不存在", "not_found", "key_not_found", 404)

    current_status = key_row["status"]
    if current_status == "active":
        new_status = "revoked"
        # 同步到网关 - 停用密钥
        try:
            await _gw.update_client_key_status(key_row["gw_client_id"], key_row["gw_key_id"], "revoked")
        except Exception as e:
            logger.warning(f"网关停用密钥失败: {e}")
    elif current_status == "revoked":
        # 检查活跃密钥数量
        active_count = await asyncio.to_thread(
            fetch_one,
            "SELECT COUNT(*) as cnt FROM user_api_keys WHERE user_id=%s AND status='active'",
            (user["id"],),
        )
        if active_count["cnt"] >= 5:
            _error_response("活跃密钥已达上限(5个)，请先停用其他密钥", "forbidden", "key_limit_reached", 403)
        new_status = "active"
        # 同步到网关 - 启用密钥
        try:
            await _gw.update_client_key_status(key_row["gw_client_id"], key_row["gw_key_id"], "active")
        except Exception as e:
            logger.warning(f"网关启用密钥失败: {e}")
    else:
        _error_response(f"无法切换密钥状态: {current_status}", "invalid_request", "invalid_status", 400)

    now = utcnow()
    await asyncio.to_thread(execute, "UPDATE user_api_keys SET status=%s WHERE id=%s", (new_status, key_id))
    return {"id": key_id, "status": new_status, "message": f"密钥已{'启用' if new_status == 'active' else '停用'}"}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, request: Request):
    """删除API密钥"""
    user = await get_current_user(request)

    # 查找key记录，验证属于当前用户
    key_row = await asyncio.to_thread(
        fetch_one,
        "SELECT * FROM user_api_keys WHERE id=%s AND user_id=%s",
        (key_id, user["id"]),
    )
    if not key_row:
        _error_response("密钥不存在", "not_found", "key_not_found", 404)

    # 通过网关删除密钥
    try:
        await _gw.delete_client_key(key_row["gw_client_id"], key_row["gw_key_id"])
    except Exception:
        pass  # 即使网关删除失败，本地也删除

    # 删除本地记录
    await asyncio.to_thread(execute, "DELETE FROM user_api_keys WHERE id=%s", (key_id,))

    return {"message": "密钥已删除"}


@router.get("/stats")
async def get_stats(request: Request):
    """获取用户用量统计 - 从Gateway数据库实时读取"""
    user = await get_current_user(request)

    gw_client_id = user.get("gw_client_id", "")
    if not gw_client_id:
        # 没有网关客户端ID，从密钥表获取
        key_row = await asyncio.to_thread(
            fetch_one,
            "SELECT gw_client_id FROM user_api_keys WHERE user_id=%s AND gw_client_id != '' LIMIT 1",
            (user["id"],),
        )
        gw_client_id = key_row["gw_client_id"] if key_row else ""

    today_start = today_start_utc()
    seven_days_ago = days_ago_utc(7)

    if not gw_client_id:
        # 没有网关客户端，返回空数据
        return {
            "overview": {
                "total_requests": 0, "total_tokens": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "avg_latency": 0,
            },
            "model_distribution": [],
            "trend_7d": [],
        }

    # 今日统计（从Gateway数据库读取）
    today_stats = await asyncio.to_thread(
        _gw_fetch_one,
        """SELECT COUNT(*) as total_requests,
                  COALESCE(SUM(total_tokens), 0) as total_tokens,
                  COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                  COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                  AVG(latency_ms) as avg_latency
           FROM request_logs
           WHERE client_id=%s AND created_at>=%s""",
        (gw_client_id, today_start),
    )

    # 7天趋势
    trend_7d = await asyncio.to_thread(
        _gw_fetch_all,
        """SELECT (created_at::timestamptz AT TIME ZONE 'UTC' + INTERVAL '8 hours')::date as date, COUNT(*) as request_count,
                  COALESCE(SUM(total_tokens), 0) as token_count
           FROM request_logs
           WHERE client_id=%s AND created_at>=%s
           GROUP BY (created_at::timestamptz AT TIME ZONE 'UTC' + INTERVAL '8 hours')::date
           ORDER BY date""",
        (gw_client_id, seven_days_ago),
    )

    # 模型分布（今日）
    model_distribution = await asyncio.to_thread(
        _gw_fetch_all,
        """SELECT model, COUNT(*) as request_count,
                  COALESCE(SUM(total_tokens), 0) as token_count
           FROM request_logs
           WHERE client_id=%s AND created_at>=%s
           GROUP BY model
           ORDER BY request_count DESC
           LIMIT 10""",
        (gw_client_id, today_start),
    )

    overview = {
        "total_requests": today_stats["total_requests"] if today_stats else 0,
        "total_tokens": int(today_stats["total_tokens"]) if today_stats else 0,
        "prompt_tokens": int(today_stats["prompt_tokens"]) if today_stats else 0,
        "completion_tokens": int(today_stats["completion_tokens"]) if today_stats else 0,
        "avg_latency": round(today_stats["avg_latency"], 1) if today_stats and today_stats["avg_latency"] else 0,
    }

    # 日志：当用户有密钥但有0统计时记录警告（辅助排查）- 日志噪音过大，注释掉
    # if today_stats and int(today_stats["total_requests"] or 0) == 0:
    #     key_count = fetch_one("SELECT COUNT(*) as c FROM user_api_keys WHERE user_id=%s", (user["id"],))
    #     if key_count and key_count["c"] > 0:
    #         logger.warning(f"用户 {user['username']}(id={user['id']}) 有 {key_count['c']} 个密钥但今日统计为0, gw_client_id={gw_client_id}")

    return {
        "overview": overview,
        "model_distribution": model_distribution,
        "trend_7d": trend_7d,
    }


@router.get("/leaderboard")
async def get_leaderboard(request: Request, limit: int = 20):
    """获取今日全平台用户排行榜（从Gateway数据库实时读取）"""
    today_start = today_start_utc()

    rows = await asyncio.to_thread(
        _gw_fetch_all,
        """SELECT
            r.client_id,
            COALESCE(c.name, '未知用户') as client_name,
            COUNT(*) as total_requests,
            COALESCE(SUM(r.total_tokens), 0) as total_tokens,
            COALESCE(SUM(r.prompt_tokens), 0) as prompt_tokens,
            COALESCE(SUM(r.completion_tokens), 0) as completion_tokens,
            COALESCE(ROUND(AVG(r.latency_ms)::numeric, 1), 0) as avg_latency,
            COUNT(DISTINCT r.model) as model_count,
            SUM(CASE WHEN r.status_code >= 200 AND r.status_code < 300 THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN r.status_code < 200 OR r.status_code >= 300 THEN 1 ELSE 0 END) as error_count,
            COALESCE(SUM(CASE WHEN r.is_stream = 1 THEN 1 ELSE 0 END), 0) as stream_count
           FROM request_logs r
           LEFT JOIN clients c ON r.client_id = c.id
           WHERE r.created_at >= %s AND r.client_id IS NOT NULL AND r.client_id != ''
           GROUP BY r.client_id, c.name
           ORDER BY total_requests DESC
           LIMIT %s""",
        (today_start, limit),
    )

    # 计算今日全平台总量
    total_row = await asyncio.to_thread(
        _gw_fetch_one,
        """SELECT COUNT(*) as total_requests,
                  COALESCE(SUM(total_tokens), 0) as total_tokens,
                  COUNT(DISTINCT client_id) as active_users
           FROM request_logs WHERE created_at >= %s""",
        (today_start,),
    )

    return {
        "leaderboard": rows,
        "total": {
            "total_requests": total_row["total_requests"] if total_row else 0,
            "total_tokens": int(total_row["total_tokens"]) if total_row else 0,
            "active_users": total_row["active_users"] if total_row else 0,
        },
    }


@router.get("/models/status")
async def get_models_status(request: Request):
    """
    模型健康状态（用户控制台用）
    
    数据来源（优先级）:
    1. 网关调度器桶数据 + request_logs 聚合（最准确）
    2. 降级：仅从 Gateway DB 的 request_logs 读取
    
    返回字段说明（来自网关完整API）:
    - model, status, status_label, health_score
    - total_buckets, healthy_buckets, cooled_buckets  ← 实时桶状态（最真实）
    - avg_success_rate, recent_success_rate            ← 桶级成功率 + 日志成功率
    - avg_latency_ms, total_requests_1h, count_429_1h, count_5xx_1h
    - active_users_1h, today_requests, today_tokens
    - total_tokens_1h, success_count_1h
    """
    user = await get_current_user(request)
    degraded = None

    # 方案A：通过网关API获取完整数据（含调度器桶状态）
    try:
        gw_data = await _gw.get_models_status()
        models_data = gw_data.get("models")
        gw_error = gw_data.get("error")

        # 只有明确返回了模型列表（可为空）且无错误时才使用网关数据
        if models_data is not None and not gw_error:
            return {
                "models": models_data,
                "summary": gw_data.get("summary", {}),
                "degraded": None,
            }

        # 网关返回空列表+有错误 → 标记降级，走方案B
        if gw_error:
            degraded = f"网关数据异常: {gw_error}"
            logger.warning(f"网关模型状态API异常({gw_error})，降级到DB")

    except Exception as e:
        logger.warning(f"网关模型状态API调用失败: {e}")
        degraded = f"网关不可用: {e}"

    # 方案B（降级）：直接读取Gateway DB
    logger.info("模型状态 API 降级为 DB 直接读取")
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    # 从Gateway DB获取最近1小时的模型日志统计
    rows = await asyncio.to_thread(
        _gw_fetch_all,
        "SELECT model, COUNT(*) as total_requests, "
        "SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) as success_count, "
        "SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as error_count, "
        "SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as count_429, "
        "SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as count_5xx, "
        "COALESCE(AVG(CASE WHEN latency_ms > 0 THEN latency_ms END), 0) as avg_latency, "
        "COALESCE(SUM(total_tokens), 0) as total_tokens, "
        "COUNT(DISTINCT client_id) as active_users "
        "FROM request_logs WHERE created_at >= %s AND model != '' AND model IS NOT NULL "
        "GROUP BY model ORDER BY total_requests DESC",
        (one_hour_ago,),
    )

    # 今日统计
    today_rows = await asyncio.to_thread(
        _gw_fetch_all,
        "SELECT model, COUNT(*) as today_requests, "
        "COALESCE(SUM(total_tokens), 0) as today_tokens "
        "FROM request_logs WHERE created_at >= %s AND model != '' AND model IS NOT NULL "
        "GROUP BY model", (today_start,),
    )
    today_map = {r["model"]: r for r in today_rows}

    # 可用模型白名单（复用网关的 verified 列表）
    _VERIFIED_WORKING_MODELS = {
        "deepseek-ai/deepseek-v4-flash", "deepseek-ai/deepseek-v4-pro",
        "z-ai/glm-5.2",
        "qwen/qwen3.5-397b-a17b", "qwen/qwen3.5-122b-a10b", "qwen/qwen3-next-80b-a3b-instruct",
        "minimaxai/minimax-m3", "minimaxai/minimax-m2.7",
        "stepfun-ai/step-3.5-flash", "stepfun-ai/step-3.7-flash",
        "openai/gpt-oss-120b", "openai/gpt-oss-20b",
        "meta/llama-3.1-70b-instruct", "meta/llama-3.1-8b-instruct",
        "meta/llama-3.2-11b-vision-instruct", "meta/llama-3.2-1b-instruct",
        "meta/llama-3.2-3b-instruct", "meta/llama-3.2-90b-vision-instruct",
        "meta/llama-3.3-70b-instruct", "meta/llama-guard-4-12b",
        "mistralai/mistral-large-3-675b-instruct-2512", "mistralai/mistral-medium-3.5-128b",
        "mistralai/mistral-nemotron", "mistralai/mistral-small-4-119b-2603",
        "mistralai/mixtral-8x7b-instruct-v0.1",
        "google/gemma-2-2b-it", "google/gemma-3n-e2b-it", "google/gemma-3n-e4b-it",
        "google/gemma-4-31b-it", "google/diffusiongemma-26b-a4b-it",
        "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-nano-30b-a3b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5", "nvidia/llama-3.3-nemotron-super-49b-v1",
        "nvidia/llama-3.1-nemotron-nano-vl-8b-v1", "nvidia/nemotron-mini-4b-instruct",
        "nvidia/nemotron-nano-12b-v2-vl", "nvidia/nvidia-nemotron-nano-9b-v2",
        "nvidia/llama-3.1-nemoguard-8b-content-safety",
        "nvidia/llama-3.1-nemoguard-8b-topic-control",
        "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
        "nvidia/nemotron-3.5-content-safety", "nvidia/gliner-pii",
        "nvidia/ising-calibration-1-35b-a3b",
        "nvidia/riva-translate-4b-instruct-v1.1",
        "abacusai/dracarys-llama-3.1-70b-instruct",
        "bytedance/seed-oss-36b-instruct",
        "poolside/laguna-xs-2.1", "sarvamai/sarvam-m",
        "thinkingmachines/inkling", "upstage/solar-10.7b-instruct",
    }

    result = []
    seen_models = set()
    for r in rows:
        model = r["model"]
        if model not in _VERIFIED_WORKING_MODELS:
            continue
        seen_models.add(model)

        total = r["total_requests"] or 0
        success = r["success_count"] or 0
        sr = (success / total * 100) if total > 0 else 100.0
        latency = float(r["avg_latency"] or 0)
        tr = today_map.get(model, {})

        health = int(sr)
        if r["count_5xx"] > 0:
            health -= min(int(r["count_5xx"] * 5), 30)
        health = max(0, min(100, health))

        if health >= 80:
            status, label = "normal", "正常"
        elif health >= 50:
            status, label = "warning", "警告"
        else:
            status, label = "abnormal", "异常"

        result.append({
            "model": model, "status": status, "status_label": label,
            "health_score": health, "total_buckets": 0,
            "healthy_buckets": 0, "cooled_buckets": 0,
            "avg_success_rate": round(sr, 1),
            "recent_success_rate": round(sr, 1),
            "success_rate": round(sr, 1),
            "avg_latency_ms": round(latency, 1),
            "total_requests_1h": total,
            "error_count_1h": r["error_count"] or 0,
            "count_429_1h": r["count_429"] or 0,
            "count_5xx_1h": r["count_5xx"] or 0,
            "active_users_1h": r["active_users"] or 0,
            "today_requests": tr.get("today_requests", 0) or 0,
            "today_tokens": int(tr.get("today_tokens", 0) or 0),
            "total_tokens_1h": int(r.get("total_tokens", 0) or 0),
        })

    # 补充未出现的可用模型
    for model in sorted(_VERIFIED_WORKING_MODELS):
        if model not in seen_models:
            result.append({
                "model": model, "status": "normal", "status_label": "正常",
                "health_score": 100, "total_buckets": 0,
                "healthy_buckets": 0, "cooled_buckets": 0,
                "avg_success_rate": 100.0, "recent_success_rate": 100.0,
                "success_rate": 100.0,
                "avg_latency_ms": 0,
                "total_requests_1h": 0, "error_count_1h": 0,
                "count_429_1h": 0, "count_5xx_1h": 0,
                "active_users_1h": 0,
                "today_requests": 0, "today_tokens": 0, "total_tokens_1h": 0,
            })

    # 排序：正常优先，同组按健康分降序
    result.sort(key=lambda x: (0 if x["status"] == "normal" else 1 if x["status"] == "warning" else 2, -x["health_score"]))

    return {
        "models": result,
        "summary": {
            "total_models": len(result),
            "normal": sum(1 for m in result if m["status"] == "normal"),
            "warning": sum(1 for m in result if m["status"] == "warning"),
            "abnormal": sum(1 for m in result if m["status"] == "abnormal"),
            "total_requests_1h": sum(m["total_requests_1h"] for m in result),
            "total_active_users_1h": sum(m["active_users_1h"] for m in result),
            "total_tokens_1h": sum(m["total_tokens_1h"] for m in result),
        },
        "degraded": degraded or "网关数据不可用，使用日志统计降级显示",
    }


@router.get("/request-logs")
async def get_request_logs(request: Request, page: int = 1, page_size: int = 20):
    """获取请求日志 - 从Gateway数据库实时读取"""
    user = await get_current_user(request)

    gw_client_id = user.get("gw_client_id", "")
    if not gw_client_id:
        key_row = await asyncio.to_thread(
            fetch_one,
            "SELECT gw_client_id FROM user_api_keys WHERE user_id=%s AND gw_client_id != '' LIMIT 1",
            (user["id"],),
        )
        gw_client_id = key_row["gw_client_id"] if key_row else ""

    if not gw_client_id:
        return {"total": 0, "page": page, "page_size": page_size, "data": []}

    # 计算偏移量
    offset = (page - 1) * page_size

    # 查询总数
    total_row = await asyncio.to_thread(
        _gw_fetch_one,
        "SELECT COUNT(*) as total FROM request_logs WHERE client_id=%s",
        (gw_client_id,),
    )
    total = total_row["total"] if total_row else 0

    # 查询分页数据（Gateway的字段名与Platform不同）
    data = await asyncio.to_thread(
        _gw_fetch_all,
        """SELECT id, model, is_stream, prompt_tokens, completion_tokens,
                  total_tokens, latency_ms,
                  CASE WHEN status_code >= 200 AND status_code < 300 THEN 'success' ELSE 'error' END as status,
                  error_msg, created_at
           FROM request_logs
           WHERE client_id=%s
           ORDER BY created_at DESC
           LIMIT %s OFFSET %s""",
        (gw_client_id, page_size, offset),
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": data,
    }


@router.put("/settings")
async def update_settings(req: UpdateSettingsRequest, request: Request):
    """更新用户设置"""
    user = await get_current_user(request)

    now = utcnow()
    await asyncio.to_thread(
        execute,
        "UPDATE users SET display_name=%s, updated_at=%s WHERE id=%s",
        (req.display_name, now, user["id"]),
    )

    return {"message": "设置已更新"}


# ========== 反馈与问题上报 ==========

class FeedbackSubmitRequest(BaseModel):
    title: str
    content: str
    category: str = "其他"


@router.post("/feedback")
async def submit_feedback(req: FeedbackSubmitRequest, request: Request):
    """用户提交问题反馈"""
    user = await get_current_user(request)

    if not req.title.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="标题和内容不能为空")
    if len(req.title) > 200:
        raise HTTPException(status_code=400, detail="标题不能超过200字")
    if len(req.content) > 5000:
        raise HTTPException(status_code=400, detail="内容不能超过5000字")

    fid = generate_uuid()[:12]
    now = utcnow()
    await asyncio.to_thread(
        execute,
        "INSERT INTO feedback (id, user_id, username, email, title, content, category, status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)",
        (fid, user["id"], user.get("display_name", "") or user.get("username", ""),
         user.get("email", ""), req.title.strip(), req.content.strip(),
         req.category, now),
    )

    # 同步写入文本文件，方便AI直接读取
    try:
        feedback_dir = Path(__file__).resolve().parents[2] / "feedbacks"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        ts = now.replace("T", "_").replace(":", "-").replace("Z", "").split(".")[0]
        fname = f"feedback_{ts}_{fid}.txt"
        username = user.get("display_name", "") or user.get("username", "未知")
        email = user.get("email", "")
        content_text = (
            f"=== 用户问题反馈 ===\n"
            f"反馈ID: {fid}\n"
            f"用户: {username} ({email})\n"
            f"分类: {req.category}\n"
            f"时间: {now}\n"
            f"标题: {req.title.strip()}\n"
            f"内容:\n{req.content.strip()}\n"
            f"====================\n"
        )
        with open(str(feedback_dir / fname), "w", encoding="utf-8") as f:
            f.write(content_text)
    except Exception:
        pass  # 不影响主流程

    return {"id": fid, "message": "反馈提交成功，我们会尽快处理"}


@router.get("/feedback")
async def get_my_feedback(request: Request, page: int = 1, page_size: int = 20):
    """获取当前用户的反馈列表"""
    user = await get_current_user(request)
    offset = (page - 1) * page_size
    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT id, title, content, category, status, reply, created_at "
        "FROM feedback WHERE user_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (user["id"], page_size, offset),
    )
    total_row = await asyncio.to_thread(fetch_one, "SELECT COUNT(*) as total FROM feedback WHERE user_id=%s", (user["id"],))
    total = total_row["total"] if total_row else 0
    return {"total": total, "page": page, "page_size": page_size, "data": rows}


# ====================================================================
# v9.0: 系统监控与管理端点
# ====================================================================

@router.get("/system/concurrency")
async def get_concurrency_stats(request: Request):
    """并发控制器统计数据（平台管理员专用）"""
    await require_admin(request)  # 系统级数据，仅平台管理员可见
    try:
        from app.concurrency import get_concurrency_controller
        cc = get_concurrency_controller()
        active = cc.get_all_active()
        stats = cc.get_stats()
        # v11 并发控制器 get_stats 返回 {"limits": {...}}（旧版为 "limit" 标量），兼容两种结构并给默认值
        limit = stats.get("limit")
        if limit is None:
            limit = (stats.get("limits") or {}).get("all", 0)
        return {
            "limit": limit,
            "rejected_total": stats.get("rejected", 0),
            "peak_concurrent": stats.get("peak", 0),
            "active_users_count": stats.get("active_users", 0),
            "active_users": dict(list(active.items())[:50]),
        }
    except ImportError:
        return {"error": "concurrency module not available"}


@router.get("/system/ip-monitor")
async def get_ip_monitor_stats(request: Request):
    """IP 监测统计数据（平台管理员专用）"""
    await require_admin(request)  # 系统级数据，仅平台管理员可见
    try:
        from app.ip_monitor import get_ip_monitor
        monitor = get_ip_monitor()
        return monitor.get_stats()
    except ImportError:
        return {"error": "ip_monitor module not available"}


@router.get("/system/ip-monitor/blocked")
async def get_blocked_ips(request: Request):
    """获取被封禁 IP 列表（平台管理员专用）"""
    await require_admin(request)  # 系统级数据，仅平台管理员可见
    try:
        from app.ip_monitor import get_ip_monitor
        monitor = get_ip_monitor()
        return {"blocked": monitor.get_all_blocked_ips()}
    except ImportError:
        return {"blocked": []}


@router.get("/system/ip-monitor/anomalies")
async def get_anomaly_ips(request: Request, min_score: int = 30):
    """获取异常 IP 列表（平台管理员专用）"""
    await require_admin(request)  # 系统级数据，仅平台管理员可见
    try:
        from app.ip_monitor import get_ip_monitor
        monitor = get_ip_monitor()
        return {"anomalies": monitor.get_all_anomalies(min_score=min_score)}
    except ImportError:
        return {"anomalies": []}


@router.post("/system/ip-monitor/unblock")
async def unblock_ip(request: Request):
    """解封指定 IP（平台管理员专用）"""
    await require_admin(request)  # 敏感操作，仅平台管理员可执行
    body = await request.json()
    ip = body.get("ip", "")
    if not ip:
        # 修复：原来返回 (dict, 400) 元组会被 FastAPI 当作响应体，HTTP状态仍为200
        raise HTTPException(status_code=400, detail="missing ip")
    try:
        from app.ip_monitor import get_ip_monitor
        monitor = get_ip_monitor()
        ok = monitor.unblock_ip(ip)
        return {"unblocked": ok, "ip": ip}
    except ImportError:
        return {"error": "ip_monitor module not available"}


@router.get("/system/user-stats")
async def get_user_classification_stats(request: Request):
    """用户分类统计数据（平台管理员专用）"""
    await require_admin(request)  # 经营统计数据，仅平台管理员可见
    try:
        def _user_stats_sync():
            # 3条查询整体包一次 to_thread，避免逐条调度
            old_count = fetch_one("SELECT COUNT(*) as cnt FROM users WHERE user_type='old'")["cnt"]
            new_count = fetch_one("SELECT COUNT(*) as cnt FROM users WHERE user_type='new'")["cnt"]
            new_usage = fetch_one(
                "SELECT COUNT(*) as total, COALESCE(SUM(daily_used), 0) as daily_used_sum, "
                "AVG(daily_used) as avg_daily_used "
                "FROM users WHERE user_type='new' AND daily_used > 0"
            )
            return old_count, new_count, new_usage

        old_count, new_count, new_usage = await asyncio.to_thread(_user_stats_sync)
        total = (old_count or 0) + (new_count or 0)

        return {
            "total_users": total or 0,
            "old_users": old_count or 0,
            "new_users": new_count or 0,
            "new_users_with_usage": {
                "count": new_usage["total"] if new_usage else 0,
                "total_daily_used": int(new_usage["daily_used_sum"] if new_usage else 0),
                "avg_daily_used": round(float(new_usage["avg_daily_used"] if new_usage else 0), 1),
            } if new_usage else {"count": 0, "total_daily_used": 0, "avg_daily_used": 0},
            "daily_limit_new": 200,
            "concurrency_limit": 2,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/system/health")
async def get_system_health(request: Request):
    """系统整体健康状态（平台管理员专用）"""
    await require_admin(request)  # 系统级数据，仅平台管理员可见
    now_ts = __import__("time").time()
    health = {"info": {}, "checks": []}

    # 并发控制器状态
    try:
        from app.concurrency import get_concurrency_controller
        cc = get_concurrency_controller()
        cs = cc.get_stats()
        health["concurrency"] = {
            "status": "healthy",
            "active_users": cs["active_users"],
            "rejected": cs["rejected"],
            "peak": cs["peak"],
        }
        health["checks"].append({"name": "Concurrency Controller", "status": "ok"})
    except Exception as e:
        health["checks"].append({"name": "Concurrency Controller", "status": "error", "error": str(e)})

    # IP 监测状态
    try:
        from app.ip_monitor import get_ip_monitor
        monitor = get_ip_monitor()
        ms = monitor.get_stats()
        health["ip_monitor"] = {
            "status": "healthy",
            "blocked_ips": ms.get("active_blocked", 0),
            "anomaly_count": ms.get("anomaly_count", 0),
            "total_tracked": ms.get("total_ips_tracked", 0),
        }
        health["checks"].append({"name": "IP Monitor", "status": "ok"})
    except Exception as e:
        health["checks"].append({"name": "IP Monitor", "status": "error", "error": str(e)})

    # 软限速状态
    try:
        from app.soft_limiter import get_soft_limiter
        sls = get_soft_limiter().get_stats()
        health["soft_limiter"] = {
            "status": "healthy",
            "active_users_60s": sls.get("active_users_60s", 0),
            "thresholds": sls.get("thresholds", {}),
            "max_delays": sls.get("max_delays", {}),
        }
        health["checks"].append({"name": "Soft Rate Limiter", "status": "ok"})
    except Exception as e:
        health["checks"].append({"name": "Soft Rate Limiter", "status": "error", "error": str(e)})

    # 数据库状态
    try:
        user_count = await asyncio.to_thread(fetch_one, "SELECT COUNT(*) as cnt FROM users")["cnt"]
        health["database"] = {"status": "healthy", "users": user_count or 0}
        health["checks"].append({"name": "Database", "status": "ok"})
    except Exception as e:
        health["checks"].append({"name": "Database", "status": "error", "error": str(e)})

    health["timestamp"] = now_ts
    return health
