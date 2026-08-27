"""
用户平台管理员后台API（精简版）
功能: 登录 / 查看用户 / 封禁/解封 / 删除用户
密码与网关同步: ACU_ADMIN_PASSWORD
"""
import os
import hmac
import hashlib
import time
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.database import execute, fetch_one, fetch_all, utcnow

router = APIRouter(prefix="/api/admin", tags=["平台管理"])
logger = logging.getLogger("aqua.admin")

ADMIN_PASSWORD = os.environ.get("ACU_ADMIN_PASSWORD", "")
_SESSION_SECRET = os.environ.get("PLATFORM_ADMIN_SESSION_SECRET", "")
_SESSION_MAX_AGE = 86400  # 24h


# ========== 认证 ==========

def _create_session() -> str:
    ts = str(int(time.time()))
    payload = f"{ts}:admin"
    sig = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ts}:{sig}"


def _verify_session(token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return False
        ts = int(parts[0])
        if time.time() - ts > _SESSION_MAX_AGE:
            return False
        payload = f"{ts}:admin"
        expected = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(parts[1], expected)
    except Exception:
        return False


async def require_admin(request: Request):
    token = request.cookies.get("admin_token") or ""
    if not token or not _verify_session(token):
        raise HTTPException(status_code=401, detail="未登录或会话过期")


# ========== 数据模型 ==========

class LoginReq(BaseModel):
    password: str


# ========== 端点 ==========

@router.post("/login")
async def admin_login(req: LoginReq):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")
    token = _create_session()
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"message": "登录成功"})
    resp.set_cookie(
        key="admin_token", value=token,
        max_age=_SESSION_MAX_AGE, httponly=True,
        samesite="lax", secure=True,
    )
    return resp


@router.post("/logout")
async def admin_logout():
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"message": "已登出"})
    resp.delete_cookie("admin_token")
    return resp


@router.get("/check")
async def admin_check(request: Request):
    try:
        await require_admin(request)
        return {"logged_in": True}
    except HTTPException:
        return {"logged_in": False}


# ========== 用户管理 ==========

@router.get("/users")
async def list_users(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    status: str = "",
):
    await require_admin(request)

    conditions = []
    params = []

    if search:
        conditions.append("(username LIKE %s OR email LIKE %s OR display_name LIKE %s)")
        kw = f"%{search}%"
        params.extend([kw, kw, kw])
    if status:
        conditions.append("status = %s")
        params.append(status)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * page_size

    total = fetch_one(f"SELECT COUNT(*) as cnt FROM users {where}", tuple(params))["cnt"]
    rows = fetch_all(
        f"SELECT id, uuid, username, email, display_name, status, created_at, updated_at "
        f"FROM users {where} ORDER BY id DESC LIMIT %s OFFSET %s",
        tuple(params) + (page_size, offset),
    )

    return {
        "users": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int):
    await require_admin(request)
    user = fetch_one("SELECT id, uuid, username, email, display_name, status, created_at, updated_at FROM users WHERE id=%s", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user": user}


@router.put("/users/{user_id}/ban")
async def ban_user(request: Request, user_id: int):
    await require_admin(request)
    user = fetch_one("SELECT id, username FROM users WHERE id=%s", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    execute("UPDATE users SET status='banned', updated_at=%s WHERE id=%s", (utcnow(), user_id))
    logger.info(f"管理员封禁用户 {user['username']}(id={user_id})")
    return {"message": f"用户 {user['username']} 已封禁"}


@router.put("/users/{user_id}/unban")
async def unban_user(request: Request, user_id: int):
    await require_admin(request)
    user = fetch_one("SELECT id, username FROM users WHERE id=%s", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    execute("UPDATE users SET status='active', updated_at=%s WHERE id=%s", (utcnow(), user_id))
    logger.info(f"管理员解封用户 {user['username']}(id={user_id})")
    return {"message": f"用户 {user['username']} 已解封"}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: int):
    await require_admin(request)
    user = fetch_one("SELECT id, username, gw_client_id FROM users WHERE id=%s", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 同步删除网关客户端及密钥
    gw_client_id = user.get("gw_client_id", "")
    if gw_client_id:
        try:
            from app.gateway_client import GatewayClient
            _gw = GatewayClient(
                base_url="http://127.0.0.1:8000",
                platform_token=os.environ.get("AQUA_PLATFORM_TOKEN", ""),
            )
            import asyncio
            # 获取客户端所有密钥并逐一删除
            keys = await _gw.list_client_keys(gw_client_id)
            for key in keys:
                await _gw.delete_client_key(gw_client_id, key.get("id", ""))
            # 删除客户端
            await _gw.delete_client(gw_client_id)
            logger.info(f"网关客户端已同步删除: gw_client_id={gw_client_id}")
        except Exception as e:
            logger.warning(f"网关客户端删除失败(不影响用户删除): {e}")

    # 删除关联数据
    execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
    execute("DELETE FROM user_api_keys WHERE user_id=%s", (user_id,))
    execute("DELETE FROM chat_history WHERE user_id=%s", (user_id,))
    execute("DELETE FROM request_logs WHERE user_id=%s", (user_id,))
    execute("DELETE FROM users WHERE id=%s", (user_id,))
    logger.info(f"管理员删除用户 {user['username']}(id={user_id}), 网关客户端已同步清除")
    return {"message": f"用户 {user['username']} 已删除"}
