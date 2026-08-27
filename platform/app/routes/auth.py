"""认证路由 - 注册/登录/登出/密码重置/邮箱验证"""
import asyncio
import os
import re
import time
import logging
from collections import deque
from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from jose import jwt, JWTError

from app.database import execute, fetch_one, fetch_all, utcnow
from app.security import (
    hash_password,
    verify_password,
    generate_session_id,
    generate_csrf_token,
    generate_uuid,
)
from app.email_service import send_verification_code, generate_code

router = APIRouter(prefix="/api/auth", tags=["认证"])
logger = logging.getLogger("aqua.auth")


# ========== 轻量内存IP限流器 ==========
#
# 实现风格复刻 gateway middleware.IPRateLimiter（滑动窗口deque），
# 但独立实现，避免平台包跨目录 import 网关包。

class _IPWindowLimiter:
    """滑动窗口IP限流器：deque记录时间戳，O(1)过期清理，max_ips限制防内存溢出"""

    def __init__(self, max_requests: int, window_seconds: int, max_ips: int = 10000):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_ips = max_ips
        self._hits: dict[str, deque] = {}
        self._last_cleanup = time.time()

    def _prune(self, ip: str, now: float) -> deque:
        """剔除窗口外的时间戳，返回该IP的时间戳队列"""
        timestamps = self._hits.get(ip)
        if timestamps is None:
            return deque()
        cutoff = now - self.window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        return timestamps

    def count(self, ip: str) -> int:
        """当前窗口内已记录次数（只读，不记录）"""
        return len(self._prune(ip, time.time()))

    def hit(self, ip: str) -> None:
        """记录一次事件"""
        now = time.time()
        if now - self._last_cleanup > 300:
            self._cleanup(now)
            self._last_cleanup = now
        timestamps = self._hits.get(ip)
        if timestamps is None:
            if len(self._hits) >= self.max_ips:
                return  # IP数达上限，丢弃记录防止内存溢出
            timestamps = self._hits[ip] = deque(maxlen=200)
        timestamps.append(now)

    def is_limited(self, ip: str) -> bool:
        return self.count(ip) >= self.max_requests

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for ip in list(self._hits.keys()):
            timestamps = self._hits[ip]
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()
            if not timestamps:
                del self._hits[ip]


# 登录失败：5次/15分钟/IP → 429
_login_fail_limiter = _IPWindowLimiter(max_requests=5, window_seconds=900)
# 发送验证码：10次/小时/IP（另有每邮箱60秒限制）
_send_code_limiter = _IPWindowLimiter(max_requests=10, window_seconds=3600)
# 注册：5次/天/IP
_register_limiter = _IPWindowLimiter(max_requests=5, window_seconds=86400)


def _client_ip(request: Request) -> str:
    """取真实客户端IP（优先XFF首个，回退连接对端）"""
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() or (request.client.host if request.client else "unknown")


# 用户名字符集：字母/数字/下划线/连字符/中文，2-24位（拒绝引号、括号、反斜杠等危险字符）
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{2,24}$")


def _validate_username(username: str):
    """服务端用户名校验（长度+字符集）"""
    if not _USERNAME_RE.match(username):
        _error_response(
            "用户名只能包含中英文、数字、下划线和连字符，长度2-24位",
            "invalid_request", "invalid_username",
        )


def _validate_password_policy(password: str):
    """服务端密码策略：≥8 且 ≤72 字符（bcrypt仅处理前72字节，超长部分会被静默截断）"""
    if not isinstance(password, str) or len(password) < 8 or len(password) > 72:
        _error_response("密码长度须为8-72个字符", "invalid_request", "invalid_password")


# ========== JWT配置 ==========

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"

if not JWT_SECRET_KEY:
    raise RuntimeError(
        "[FATAL] 环境变量 JWT_SECRET_KEY 未设置！请设置后重新启动。"
    )


# ========== JWT辅助函数 ==========

def create_jwt_token(data: dict, expires_hours: int = 24) -> str:
    """生成JWT令牌"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> dict | None:
    """验证JWT令牌，有效返回payload，无效返回None"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ========== 请求模型 ==========

class SendCodeRequest(BaseModel):
    email: str
    purpose: str  # "register" | "reset_password"


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    code: str
    behavior: Optional[dict] = None


class LoginRequest(BaseModel):
    username: str
    password: str
    behavior: Optional[dict] = None


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


# ========== 辅助函数 ==========

def _validate_qq_email(email: str):
    """验证是否为合法的 QQ 邮箱（纯数字qq号@qq.com，不含任何字母/特殊字符）"""
    email = email.strip().lower()
    # 全量正则：只能由纯数字 + @qq.com 组成
    if not re.fullmatch(r'\d+@qq\.com', email):
        # 区分错误类型，给出精准提示
        if not email.endswith("@qq.com"):
            _error_response("仅支持 QQ 邮箱注册", "invalid_email", "not_qq_email")
        # 到这里说明以 @qq.com 结尾，但前缀不是纯数字
        _error_response("QQ 号前缀必须为纯数字，不含任何字母或特殊字符", "invalid_email", "qq_number_invalid")
    # QQ 号长度限制 5-11 位
    qq_number = email.replace("@qq.com", "")
    if len(qq_number) < 5 or len(qq_number) > 11:
        _error_response("QQ 号长度不合法（5-11 位数字）", "invalid_email", "qq_number_length")


def _error_response(message: str, error_type: str, code: str, status_code: int = 400):
    """生成OpenAI格式错误响应"""
    raise HTTPException(
        status_code=status_code,
        detail={"message": message, "type": error_type, "code": code},
    )


async def _create_session(user_id: int, request: Request) -> str:
    """创建用户会话，返回session_id（DB写入经线程池执行，不阻塞事件循环）"""
    session_id = generate_session_id()
    csrf_token = generate_csrf_token()
    now = utcnow()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    user_agent = request.headers.get("user-agent", "")

    await asyncio.to_thread(
        execute,
        """INSERT INTO sessions (id, user_id, csrf_token, ip, user_agent, created_at, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (session_id, user_id, csrf_token, ip, user_agent, now, expires_at),
    )
    return session_id


def _set_session_cookie(response, session_id: str):
    """设置session cookie（secure默认开启，可通过 SESSION_COOKIE_SECURE=0 在纯HTTP环境关闭）"""
    response.set_cookie(
        key="aqua_session",
        value=session_id,
        httponly=True,
        max_age=86400,
        samesite="lax",
        secure=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
        path="/",
    )


async def get_current_user(request: Request) -> dict:
    """从cookie读取session_id，查sessions表关联users表返回用户信息"""
    session_id = request.cookies.get("aqua_session")
    if not session_id:
        _error_response("未登录", "authentication_error", "not_authenticated", 401)

    session = await asyncio.to_thread(fetch_one, "SELECT * FROM sessions WHERE id=%s", (session_id,))
    if not session:
        _error_response("会话不存在", "authentication_error", "invalid_session", 401)

    # 检查session是否过期
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
    if now > expires_at:
        await asyncio.to_thread(execute, "DELETE FROM sessions WHERE id=%s", (session_id,))
        _error_response("会话已过期", "authentication_error", "session_expired", 401)

    user = await asyncio.to_thread(fetch_one, "SELECT * FROM users WHERE id=%s", (session["user_id"],))
    if not user:
        _error_response("用户不存在", "authentication_error", "user_not_found", 401)

    if user["status"] != "active":
        _error_response("账户已被禁用", "forbidden", "account_disabled", 403)

    return user


# ========== 端点 ==========


@router.post("/send-code")
async def send_code(req: SendCodeRequest, request: Request):
    """发送邮箱验证码"""
    # IP限流：10次/小时（防邮件轰炸）
    ip = _client_ip(request)
    if _send_code_limiter.is_limited(ip):
        _error_response("发送过于频繁，请稍后再试", "rate_limit", "ip_rate_limited", 429)
    _send_code_limiter.hit(ip)

    email = req.email.strip().lower()
    purpose = req.purpose.strip()

    if purpose not in ("register", "reset_password"):
        _error_response("无效的验证码用途", "invalid_request", "invalid_purpose")

    # 注册时仅允许 QQ 邮箱
    if purpose == "register":
        _validate_qq_email(email)

    # 检查60秒发送限制
    recent = await asyncio.to_thread(
        fetch_one,
        """SELECT created_at FROM email_verification
           WHERE email=%s ORDER BY created_at DESC LIMIT 1""",
        (email,),
    )
    if recent:
        try:
            created = datetime.fromisoformat(recent["created_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - created).total_seconds() < 60:
                _error_response("发送过于频繁，请60秒后重试", "rate_limit", "too_frequent")
        except (ValueError, KeyError):
            pass

    # 防邮箱枚举：注册要求邮箱未注册、重置要求邮箱已注册；
    # 不满足时静默跳过发送，两种情况返回完全一致的文案
    email_exists = await asyncio.to_thread(fetch_one, "SELECT id FROM users WHERE email=%s", (email,)) is not None
    should_send = (purpose == "register" and not email_exists) or (
        purpose == "reset_password" and email_exists
    )
    if not should_send:
        return {"message": "如果该邮箱存在，验证码已发送"}

    # 生成验证码
    code = generate_code()
    now = utcnow()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ver_id = generate_uuid()

    await asyncio.to_thread(
        execute,
        """INSERT INTO email_verification (id, email, code, purpose, expires_at, used, created_at)
           VALUES (%s, %s, %s, %s, %s, 0, %s)""",
        (ver_id, email, code, purpose, expires_at, now),
    )

    # 发送邮件（失败也不区分响应，避免通过错误差异探测邮箱注册状态）
    success = await send_verification_code(email, code, purpose)
    if not success:
        logger.error(f"验证码邮件发送失败: email={email}, purpose={purpose}")

    return {"message": "如果该邮箱存在，验证码已发送"}


@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    """用户注册"""
    # IP限流：5次/天
    ip = _client_ip(request)
    if _register_limiter.is_limited(ip):
        _error_response("注册请求过于频繁，请稍后再试", "rate_limit", "ip_rate_limited", 429)
    _register_limiter.hit(ip)

    # 注册开关检查
    from app.database import get_setting
    if await asyncio.to_thread(get_setting, "registration_open") != "1":
        _error_response("注册暂未开放，请稍后再试", "forbidden", "registration_closed")

    username = req.username.strip()
    email = req.email.strip().lower()
    password = req.password
    code = req.code.strip()

    # 服务端字符集校验（拒绝引号/括号/反斜杠等危险字符）
    _validate_username(username)
    # 服务端密码策略（8-72字符，bcrypt 72字节上限）
    _validate_password_policy(password)

    # 严格校验 QQ 邮箱（防御性双重检查）
    _validate_qq_email(email)

    # 验证验证码
    now = utcnow()
    ver = await asyncio.to_thread(
        fetch_one,
        """SELECT * FROM email_verification
           WHERE email=%s AND code=%s AND purpose='register' AND used=0 AND expires_at>%s
           ORDER BY created_at DESC LIMIT 1""",
        (email, code, now),
    )
    if not ver:
        _error_response("验证码无效或已过期", "invalid_request", "invalid_code")

    # 标记验证码已使用
    await asyncio.to_thread(execute, "UPDATE email_verification SET used=1 WHERE id=%s", (ver["id"],))

    # === v9.2: 行为检测（替代人机验证） ===
    if req.behavior:
        try:
            from app.behavior import analyze_behavior
            result = analyze_behavior(req.behavior)
            if result["is_bot"]:
                logger.warning(f"行为检测拦截注册: username={username} score={result['score']} reason={result['reason']}")
                _error_response("检测到异常行为，请稍后再试", "forbidden", "behavior_blocked", 403)
        except Exception as e:
            logger.error(f"行为检测异常: {e}")

    # 检查username和email唯一性
    existing_user = await asyncio.to_thread(fetch_one, "SELECT id FROM users WHERE username=%s OR email=%s", (username, email))
    if existing_user:
        _error_response("用户名或邮箱已被使用", "conflict", "user_exists")

    # 创建用户
    # bcrypt 12 rounds（单次100-300ms CPU），经线程池执行避免阻塞事件循环
    password_hash = await asyncio.to_thread(hash_password, password)
    user_uuid = generate_uuid()
    now_str = utcnow()

    # v9.0: 用户分类 - 2026-07-21 23:00 CST 后注册为 "new" 用户
    OLD_USER_DEADLINE = "2026-07-21T15:00:00.000Z"  # 2026-07-21 23:00 CST = 15:00 UTC
    user_type = "old" if now_str < OLD_USER_DEADLINE else "new"
    daily_limit = -1  # v10.0: 全部不限量

    await asyncio.to_thread(
        execute,
        """INSERT INTO users (uuid, username, email, password_hash, display_name, status,
           created_at, updated_at, user_type, daily_limit, daily_used)
           VALUES (%s, %s, %s, %s, '', 'active', %s, %s, %s, %s, 0)""",
        (user_uuid, username, email, password_hash, now_str, now_str, user_type, daily_limit),
    )

    user = await asyncio.to_thread(fetch_one, "SELECT * FROM users WHERE uuid=%s", (user_uuid,))
    if not user:
        _error_response("注册失败", "server_error", "registration_failed", 500)

    # 自动在网关创建client（命名格式：用户名(ID:纯数字ID)）
    try:
        from app.gateway_client import GatewayClient
        import os
        _gw = GatewayClient(
            base_url="http://127.0.0.1:8000",
            platform_token=os.environ.get("AQUA_PLATFORM_TOKEN", ""),
        )
        client_name = f"{username}(ID:{user['id']})"
        gw_client = await _gw.create_client(client_name, user_type=user_type)
        if gw_client and gw_client.get("id"):
            await asyncio.to_thread(
                execute,
                "UPDATE users SET gw_client_id=%s WHERE id=%s",
                (gw_client["id"], user["id"]),
            )
            logger.info(f"注册用户网关client创建成功: user_id={user['id']}, gw_client_id={gw_client['id']}, name={client_name}")
    except Exception as e:
        # 网关client创建失败不影响注册流程
        logger.warning(f"注册用户网关client创建失败: user_id={user['id']}, error={e}")

    # 自动登录 - 创建session
    session_id = await _create_session(user["id"], request)

    # 生成JWT令牌（绑定session_id，随会话吊销一起失效）
    jwt_token = create_jwt_token(
        {"user_id": user["id"], "username": user["username"], "session_id": session_id}
    )

    from fastapi.responses import JSONResponse
    response = JSONResponse(content={"message": "注册成功", "jwt_token": jwt_token})
    _set_session_cookie(response, session_id)
    return response


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    """用户登录"""
    # 登录失败限流：同IP 15分钟内失败5次即429
    ip = _client_ip(request)
    if _login_fail_limiter.is_limited(ip):
        _error_response("登录尝试过于频繁，请15分钟后再试", "rate_limit", "too_many_attempts", 429)

    username_input = req.username.strip()
    password = req.password

    # 查询用户（支持用户名或邮箱登录）
    user = await asyncio.to_thread(
        fetch_one,
        "SELECT * FROM users WHERE username=%s OR email=%s",
        (username_input, username_input),
    )
    if not user:
        _login_fail_limiter.hit(ip)  # 记录一次失败
        _error_response("用户名或密码错误", "authentication_error", "invalid_credentials", 401)

    # 验证密码
    # bcrypt校验经线程池执行，避免阻塞事件循环
    if not await asyncio.to_thread(verify_password, password, user["password_hash"]):
        _login_fail_limiter.hit(ip)  # 记录一次失败
        _error_response("用户名或密码错误", "authentication_error", "invalid_credentials", 401)

    # 检查状态
    if user["status"] != "active":
        _error_response("账户已被禁用", "forbidden", "account_disabled", 403)

    # ========== 登录时的行为检测 ==========
    if req.behavior:
        try:
            from app.behavior import analyze_behavior
            result = analyze_behavior(req.behavior)
            if result["is_bot"]:
                logger.warning(f"行为检测拦截登录: username={username_input} score={result['score']}")
                _error_response("检测到异常行为，请稍后再试", "forbidden", "behavior_blocked", 403)
        except Exception as e:
            logger.error(f"登录行为检测异常: {e}")

    # 创建session
    session_id = await _create_session(user["id"], request)

    # 生成JWT令牌（绑定session_id，随会话吊销一起失效）
    jwt_token = create_jwt_token(
        {"user_id": user["id"], "username": user["username"], "session_id": session_id}
    )

    from fastapi.responses import JSONResponse
    response = JSONResponse(content={"message": "登录成功", "jwt_token": jwt_token})
    _set_session_cookie(response, session_id)
    return response


@router.post("/logout")
async def logout(request: Request):
    """用户登出"""
    session_id = request.cookies.get("aqua_session")

    if session_id:
        # 删除session记录
        await asyncio.to_thread(execute, "DELETE FROM sessions WHERE id=%s", (session_id,))

    from fastapi.responses import JSONResponse
    response = JSONResponse(content={"message": "已登出"})
    response.delete_cookie(key="aqua_session", path="/")
    return response


@router.get("/verify")
async def verify_token(request: Request):
    """验证JWT令牌，返回用户信息"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        _error_response("缺少Bearer令牌", "authentication_error", "missing_token", 401)

    token = auth[7:]
    payload = verify_jwt_token(token)
    if not payload:
        _error_response("令牌无效或已过期", "authentication_error", "invalid_token", 401)

    user_id = payload.get("user_id")
    if not user_id:
        _error_response("令牌无效", "authentication_error", "invalid_token", 401)

    # JWT与session绑定：校验令牌中的session_id对应会话仍存在且未过期，
    # 堵住改密/登出/封禁后旧JWT依然有效的吊销缺口
    session_id = payload.get("session_id")
    if not session_id:
        _error_response("令牌缺少会话绑定，请重新登录", "authentication_error", "invalid_token", 401)
    session = await asyncio.to_thread(fetch_one, "SELECT * FROM sessions WHERE id=%s", (session_id,))
    if not session or session.get("user_id") != user_id:
        _error_response("会话已失效，请重新登录", "authentication_error", "session_revoked", 401)
    expires_at = datetime.fromisoformat(str(session["expires_at"]).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        _error_response("会话已过期，请重新登录", "authentication_error", "session_expired", 401)

    user = await asyncio.to_thread(fetch_one, "SELECT * FROM users WHERE id=%s", (user_id,))
    if not user or user["status"] != "active":
        _error_response("用户不存在或已被禁用", "authentication_error", "user_not_found", 401)

    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
    }


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
    """重置密码"""
    # 防滥用限流：复用发送验证码的IP小时级限流（重置入口本身低频）
    ip = _client_ip(request)
    if _send_code_limiter.is_limited(ip):
        _error_response("操作过于频繁，请稍后再试", "rate_limit", "ip_rate_limited", 429)

    email = req.email.strip().lower()
    code = req.code.strip()
    new_password = req.new_password

    # 服务端密码策略（8-72字符，bcrypt 72字节上限）
    _validate_password_policy(new_password)

    # 验证验证码
    now = utcnow()
    ver = await asyncio.to_thread(
        fetch_one,
        """SELECT * FROM email_verification
           WHERE email=%s AND code=%s AND purpose='reset_password' AND used=0 AND expires_at>%s
           ORDER BY created_at DESC LIMIT 1""",
        (email, code, now),
    )
    if not ver:
        _error_response("验证码无效或已过期", "invalid_request", "invalid_code")

    # 标记验证码已使用
    await asyncio.to_thread(execute, "UPDATE email_verification SET used=1 WHERE id=%s", (ver["id"],))

    # 更新密码（bcrypt经线程池执行）
    password_hash = await asyncio.to_thread(hash_password, new_password)
    now_str = utcnow()
    await asyncio.to_thread(
        execute,
        "UPDATE users SET password_hash=%s, updated_at=%s WHERE email=%s",
        (password_hash, now_str, email),
    )

    # 重置成功后吊销该用户全部会话（绑定session_id的JWT随之失效）
    user_row = await asyncio.to_thread(fetch_one, "SELECT id FROM users WHERE email=%s", (email,))
    if user_row:
        deleted = await asyncio.to_thread(execute, "DELETE FROM sessions WHERE user_id=%s", (user_row["id"],))
        logger.info(f"密码重置成功，已吊销用户会话: user_id={user_row['id']}, sessions={deleted}")

    return {"message": "密码重置成功"}
