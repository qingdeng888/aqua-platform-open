"""
管理后台API路由 - 完整管理功能

端点分组：
- 管理员认证: /gw/admin/login
- 上游密钥管理: /gw/admin/upstreams
- 代理池管理: /gw/admin/proxies
- 下游客户管理: /gw/admin/clients
- 复合桶监控: /gw/admin/buckets
- 算法引擎统计: /gw/admin/algorithm-stats
- 仪表盘: /gw/admin/dashboard
- 请求日志: /gw/admin/request-logs
- 审计日志: /gw/admin/audit-logs
- 网关策略: /gw/admin/settings
- 维护模式: /gw/admin/maintenance
- 商用识别: /gw/admin/commercial-detection
- 接口调试: /gw/admin/debug/test
"""
import asyncio
import os
import hmac
import re
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict

from app.database import (
    init_db, fetch_one, fetch_all, execute, get_setting, set_setting,
    insert_audit, insert_audit_many, utcnow, today_start_utc, days_ago_utc,
    cleanup_success_logs,
)
from app.security import (
    encrypt_upstream_key, decrypt_upstream_key, generate_client_key,
    encrypt_secret, decrypt_secret, hash_secret, mask_secret,
    create_admin_token, verify_admin_token, encrypt_proxy_secret,
)
from app.proxy_pool import (
    ALLOWED_MODES, ALLOWED_SCHEMES, MODE_BIND, build_proxy_url, proxy_pool,
)
from app.scheduler import get_scheduler, get_threshold_for_model
from app.public_api import _clear_settings_cache
from app.middleware import (
    is_maintenance_mode, set_maintenance_mode,
    _login_rate_limiter, _admin_rate_limiter, get_client_ip,
)
from app.commercial_detect import get_detector

logger = logging.getLogger("acu.admin")

router = APIRouter(prefix="/gw/admin")

# 管理员密码：只有 ACU_ADMIN_PASSWORD 一种配置方式——明文写进 .env 即可，无需哈希。
# 与 SQLAdmin 面板（admin_panel.py）读同一个变量，两个登录口行为天然一致。
# 不做 bcrypt 哈希是刻意取舍：.env 本就明文存着库密码与加密主密钥，且已 chmod 600
# + gitignore + dockerignore；.env 一旦泄露，攻击者拿库密码与主密钥可直接读库解密全部
# 上游密钥，管理员密码再哈希一层的边际收益很低。附带两个好处：不必为生成哈希装 bcrypt
# （Debian 上 pip 装包会被 PEP 668 的 externally-managed-environment 挡住），且明文
# 不含 $，绕开 docker compose 对 env_file 里 $xxx 做变量插值把值悄悄截断的坑。
ADMIN_PASSWORD = os.environ.get("ACU_ADMIN_PASSWORD") or ""
if not ADMIN_PASSWORD:
    raise RuntimeError(
        "[FATAL] 未配置管理员密码！请在 .env 中设置 ACU_ADMIN_PASSWORD=你的密码"
    )


def _verify_admin_password(password: str) -> bool:
    """恒定时间比较管理员密码。

    不做 strip / 大小写归一，也不按长度提前返回，避免通过响应差异反推密码。
    """
    # 模块级守卫已保证配置非空，这里再挡一次空值——否则 compare_digest(b"", b"") 为真，
    # 会退化成"空密码即可登录"（admin_panel v10.1 踩过这个坑）
    if not ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))


# ========== 批量添加常量（上游密钥 / 代理池共用） ==========

BULK_MAX_LINES = 200        # 单次批量上限：再多请分批，避免一次请求里做上千次 HKDF+Fernet
BULK_KEY_MIN_LEN = 8        # 低于此长度的行一律当粘贴残渣拒掉
BULK_KEY_MAX_LEN = 512
BULK_NAME_PREFIX = "nv"     # 上游密钥自动命名前缀默认值
BULK_NAME_PREFIX_MAX = 32
BULK_PROXY_NAME_PREFIX = "px"  # 代理自动命名前缀默认值


# ========== 请求模型 ==========

class LoginRequest(BaseModel):
    password: str
    model_config = ConfigDict(extra="ignore")

class UpstreamCreateRequest(BaseModel):
    name: str
    api_key: str
    provider: str = "nvidia"
    weight: int = 1
    rpm_limit: int = 40
    switch_threshold: int = 38
    proxy_mode: str = "direct"      # direct | bind | rotate
    proxy_id: Optional[str] = None  # proxy_mode='bind' 时必填

class UpstreamBulkCreateRequest(BaseModel):
    """批量添加：api_keys 为多行文本，每行一个密钥，名称由后端自动生成"""
    api_keys: str
    name_prefix: str = BULK_NAME_PREFIX  # 自动命名格式 {前缀}-{序号}
    provider: str = "nvidia"
    weight: int = 1
    rpm_limit: int = 40
    switch_threshold: int = 38
    proxy_mode: str = "direct"      # direct | bind | rotate
    proxy_id: Optional[str] = None  # proxy_mode='bind' 时必填

class UpstreamUpdateRequest(BaseModel):
    name: Optional[str] = None
    weight: Optional[int] = None
    rpm_limit: Optional[int] = None
    switch_threshold: Optional[int] = None
    status: Optional[str] = None
    proxy_mode: Optional[str] = None
    proxy_id: Optional[str] = None

class ProxyCreateRequest(BaseModel):
    name: str
    scheme: str = "socks5"          # socks5 | socks5h | http | https
    host: str
    port: int
    username: Optional[str] = None  # 留空表示无认证代理
    password: Optional[str] = None
    remark: Optional[str] = None

class ProxyBulkCreateRequest(BaseModel):
    """批量添加代理：proxy_urls 为多行文本，每行一个 scheme://[user:pass@]host:port"""
    proxy_urls: str
    name_prefix: str = BULK_PROXY_NAME_PREFIX  # 自动命名格式 {前缀}-{序号}
    remark: Optional[str] = None               # 整批共用备注

class ProxyUpdateRequest(BaseModel):
    name: Optional[str] = None
    scheme: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None  # 传空字符串表示清除认证信息
    status: Optional[str] = None
    remark: Optional[str] = None

class ClientCreateRequest(BaseModel):
    name: str
    user_type: str = "old"

class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    user_type: Optional[str] = None

class PolicyUpdateRequest(BaseModel):
    upstream_base_url: Optional[str] = None
    chat_path: Optional[str] = None
    models_path: Optional[str] = None
    cooldown_seconds: Optional[int] = None
    switch_threshold: Optional[int] = None

class DebugChatRequest(BaseModel):
    api_key: str
    model: str
    messages: list
    stream: Optional[bool] = False
    model_config = ConfigDict(extra="allow")


# ========== 认证依赖 ==========

async def require_admin(request: Request):
    """验证管理员Token"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.cookies.get("admin_token", "")

    if not token:
        raise HTTPException(status_code=401, detail={
            "message": "未登录", "type": "unauthorized", "code": "no_token"
        })

    secret = await asyncio.to_thread(get_setting, "gateway_secret")
    if not secret or not verify_admin_token(token, secret):
        raise HTTPException(status_code=401, detail={
            "message": "Token无效或已过期", "type": "unauthorized", "code": "invalid_token"
        })


# LIKE搜索通配符转义（防 %/_ 注入导致全表扫描或匹配逃逸）
def _like_escape(s: str) -> str:
    """转义LIKE模式中的 % 与 _（配合SQL里的 ESCAPE '\\' 使用）"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ========== 管理员登录 ==========

@router.post("/login", tags=["管理员"])
async def admin_login(req: LoginRequest, request: Request):
    """管理员登录"""
    ip = get_client_ip(request)

    # 登录频率限制
    if not _login_rate_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail={
            "message": "请求过于频繁，请稍后再试",
            "type": "rate_limit_error",
            "code": "rate_limited",
        })


    # 恒定时间比较为微秒级，直接同步执行（无需 to_thread，不会阻塞事件循环）
    if not _verify_admin_password(req.password):
        raise HTTPException(status_code=401, detail={
            "message": "密码错误", "type": "unauthorized", "code": "wrong_password"
        })

    secret = await asyncio.to_thread(get_setting, "gateway_secret")
    token = create_admin_token(secret)

    response = JSONResponse(content={
        "token": token,
        "message": "登录成功",
    })
    response.set_cookie(
        key="admin_token", value=token,
        httponly=True, max_age=86400, samesite="lax", secure=True,
    )
    await asyncio.to_thread(insert_audit, "login", "admin", "", "管理员登录")
    return response


# ========== 仪表盘 ==========

@router.get("/dashboard", tags=["管理员"])
async def dashboard(request: Request):
    """仪表盘汇总数据"""
    await require_admin(request)

    now_ts = time.time()
    # 修复：使用本地时区(CST+8)零点对应的UTC时间作为"今日"边界
    today_start = today_start_utc()
    seven_days_ago = days_ago_utc(7)

    # 仪表盘6条聚合查询打包为单个同步函数，整体一次 to_thread 执行（避免逐条调度开销）
    def _dashboard_queries():
        # 今日统计
        today_stats = fetch_one(
            "SELECT COUNT(*) as total_requests, "
            "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as success_count, "
            "SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as count_429, "
            "SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as count_5xx, "
            "SUM(CASE WHEN status_code = 401 THEN 1 ELSE 0 END) as count_401, "
            "SUM(CASE WHEN status_code = 403 THEN 1 ELSE 0 END) as count_403, "
            "SUM(CASE WHEN status_code = 400 THEN 1 ELSE 0 END) as count_400, "
            "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as count_200, "
            "SUM(prompt_tokens) as prompt_tokens, "
            "SUM(completion_tokens) as completion_tokens, "
            "SUM(total_tokens) as total_tokens, "
            "AVG(latency_ms) as avg_latency, "
            "SUM(CASE WHEN is_stream = 1 THEN 1 ELSE 0 END) as stream_count "
            "FROM request_logs WHERE created_at >= %s",
            (today_start,),
        )
        # 历史统计
        total_stats = fetch_one(
            "SELECT COUNT(*) as total_requests, "
            "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as success_count, "
            "SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as count_429, "
            "SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as count_5xx, "
            "SUM(CASE WHEN status_code = 401 THEN 1 ELSE 0 END) as count_401, "
            "SUM(CASE WHEN status_code = 403 THEN 1 ELSE 0 END) as count_403, "
            "SUM(CASE WHEN status_code = 400 THEN 1 ELSE 0 END) as count_400, "
            "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as count_200, "
            "SUM(prompt_tokens) as prompt_tokens, "
            "SUM(completion_tokens) as completion_tokens, "
            "SUM(total_tokens) as total_tokens, "
            "AVG(latency_ms) as avg_latency, "
            "SUM(CASE WHEN is_stream = 1 THEN 1 ELSE 0 END) as stream_count "
            "FROM request_logs"
        )
        # 活跃密钥和客户数
        active_keys = fetch_one("SELECT COUNT(*) as cnt FROM upstream_keys WHERE status='active'")["cnt"]
        active_clients = fetch_one("SELECT COUNT(*) as cnt FROM clients WHERE status='active'")["cnt"]
        # 7天趋势（PostgreSQL：将UTC时间转为CST+8日期分组）
        trend = fetch_all(
            "SELECT (created_at::timestamptz AT TIME ZONE 'Asia/Shanghai')::date as date, COUNT(*) as requests, "
            "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as success "
            "FROM request_logs WHERE created_at >= %s "
            "GROUP BY (created_at::timestamptz AT TIME ZONE 'Asia/Shanghai')::date ORDER BY date",
            (seven_days_ago,),
        )
        # 模型分布（今日，过滤空模型名）
        model_dist = fetch_all(
            "SELECT model, COUNT(*) as count FROM request_logs "
            "WHERE created_at >= %s AND model IS NOT NULL AND model != '' GROUP BY model ORDER BY count DESC LIMIT 10",
            (today_start,),
        )
        return today_stats, total_stats, active_keys, active_clients, trend, model_dist

    today_stats, total_stats, active_keys, active_clients, trend, model_dist = await asyncio.to_thread(_dashboard_queries)

    # 调度器全局状态（内部含DB查询，经线程池执行）
    scheduler = get_scheduler()
    global_status = await asyncio.to_thread(scheduler.get_global_status)
    # v10.0: 添加 display_name（友好名称）
    try:
        from app.nim_models import NIM_MODEL_CATALOG
        for md in model_dist:
            info = NIM_MODEL_CATALOG.get(md["model"])
            if info:
                md["display_name"] = info.display_name
            else:
                md["display_name"] = md["model"]
    except ImportError:
        for md in model_dist:
            md["display_name"] = md["model"]

    success_count = today_stats["success_count"] or 0
    total_today = today_stats["total_requests"] or 0
    success_rate = (success_count / total_today * 100) if total_today > 0 else 0

    hist_total = total_stats["total_requests"] or 0
    hist_success = total_stats["success_count"] or 0
    hist_success_rate = (hist_success / hist_total * 100) if hist_total > 0 else 0

    return {
        "today": {
            "total_requests": total_today,
            "success_rate": round(success_rate, 2),
            "total_tokens": today_stats["total_tokens"] or 0,
            "prompt_tokens": today_stats["prompt_tokens"] or 0,
            "completion_tokens": today_stats["completion_tokens"] or 0,
            "avg_latency_ms": round(today_stats["avg_latency"] or 0, 2),
            "count_200": today_stats["count_200"] or 0,
            "count_400": today_stats["count_400"] or 0,
            "count_401": today_stats["count_401"] or 0,
            "count_403": today_stats["count_403"] or 0,
            "count_429": today_stats["count_429"] or 0,
            "count_5xx": today_stats["count_5xx"] or 0,
            "stream_count": today_stats["stream_count"] or 0,
            "stream_ratio": round((today_stats["stream_count"] or 0) / total_today * 100, 2) if total_today > 0 else 0,
        },
        "history": {
            "total_requests": hist_total,
            "success_count": hist_success,
            "success_rate": round(hist_success_rate, 2),
            "total_tokens": int(total_stats["total_tokens"] or 0),
            "prompt_tokens": int(total_stats["prompt_tokens"] or 0),
            "completion_tokens": int(total_stats["completion_tokens"] or 0),
            "avg_latency_ms": int(round(total_stats["avg_latency"] or 0)),
            "count_200": total_stats["count_200"] or 0,
            "count_400": total_stats["count_400"] or 0,
            "count_401": total_stats["count_401"] or 0,
            "count_403": total_stats["count_403"] or 0,
            "count_429": total_stats["count_429"] or 0,
            "count_5xx": total_stats["count_5xx"] or 0,
            "stream_count": total_stats["stream_count"] or 0,
        },
        "active": {
            "upstream_keys": active_keys,
            "clients": active_clients,
        },
        "trend_7d": trend,
        "model_distribution": model_dist,
        "scheduler": global_status,
    }


# ========== 代理池管理 ==========

async def _validate_proxy_binding(mode: Optional[str], proxy_id: Optional[str]) -> tuple:
    """校验上游密钥的出网模式，返回规范化后的 (proxy_mode, proxy_id)

    - direct/rotate：强制清空 proxy_id（避免残留脏绑定）
    - bind：proxy_id 必填且必须存在于代理池
    """
    mode = (mode or "direct").strip().lower()
    if mode not in ALLOWED_MODES:
        raise HTTPException(status_code=400, detail=f"proxy_mode 非法，可选: {', '.join(ALLOWED_MODES)}")
    if mode != MODE_BIND:
        return mode, None
    if not proxy_id:
        raise HTTPException(status_code=400, detail="proxy_mode=bind 时必须指定 proxy_id")
    row = await asyncio.to_thread(fetch_one, "SELECT id FROM proxies WHERE id = %s", (proxy_id,))
    if not row:
        raise HTTPException(status_code=404, detail="指定的代理不存在")
    return mode, proxy_id


def parse_bulk_proxies(raw: str) -> list:
    """多行文本 → 逐行代理解析结果，保持输入顺序

    每行一个代理 URL：`scheme://[user:pass@]host:port`，例如
    `http://user:pass@1.2.3.4:8080`；无认证代理写 `http://1.2.3.4:8080`。
    空行与 `#` 注释行直接忽略（不进结果），方便在粘贴的代理清单里写备注。

    解析交给 urlsplit 而非手写切分——它已正确处理三件容易写错的事：密码里含 `@`
    时从最右侧切 userinfo、密码里含 `:` 时只按首个 `:` 分割、以及 IPv6 的方括号写法。
    用户名/密码再过一遍 unquote，与 build_proxy_url() 的 quote 形成往返对称。

    跳过原因绝不回显原始行内容——行里带着密码明文，而原因是要进响应体的。
    """
    seen = {}
    out = []
    for lineno, line in enumerate((raw or "").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue

        def skip(reason: str) -> None:
            out.append({"line": lineno, "reason": reason})

        if "://" not in text:
            skip("缺少协议前缀，格式应为 scheme://[user:pass@]host:port")
            continue
        try:
            u = urlsplit(text)
            port = u.port          # 非数字或超出 0-65535 时在此抛 ValueError
            host = u.hostname or ""
        except ValueError:
            skip("端口非法，需为 1-65535 的数字")
            continue

        scheme = (u.scheme or "").lower()
        if scheme not in ALLOWED_SCHEMES:
            skip(f"协议 {scheme or '空'} 不支持，可选: {', '.join(ALLOWED_SCHEMES)}")
        elif not host:
            skip("缺少地址")
        elif ":" in host:
            # IPv6 字面量：build_proxy_url() 拼回 URL 时不会补方括号，入库即产生不可用记录，
            # 与其存坏数据不如当场拒掉（单个添加同样受此限制）
            skip("暂不支持 IPv6 字面量地址，请用域名或 IPv4")
        elif port is None:
            skip("缺少端口")
        elif not 1 <= port <= 65535:
            # urlsplit 放行 0，但 build_proxy_url 只接受 1-65535；此处对齐后者，
            # 否则 :0 这类行能入库却在实际取用时抛 ValueError
            skip("端口非法，需为 1-65535 的数字")
        elif u.path not in ("", "/") or u.query or u.fragment:
            skip("地址后不应带路径/参数，格式应为 scheme://[user:pass@]host:port")
        else:
            username = unquote(u.username or "")
            password = unquote(u.password or "")
            if password and not username:
                skip("有密码但缺用户名，如密码含 @ 或 : 请改用 %40 / %3A 转义")
                continue
            ident = (scheme, host, port, username)
            if ident in seen:
                skip(f"与本批第 {seen[ident]} 行重复")
                continue
            seen[ident] = lineno
            out.append({
                "line": lineno, "scheme": scheme, "host": host,
                "port": port, "username": username, "password": password,
            })
    return out


def _proxy_row_to_dict(row: dict) -> dict:
    """代理行 → 响应体：绝不返回密码密文/明文"""
    return {
        "id": row["id"],
        "name": row["name"],
        "scheme": row["scheme"],
        "host": row["host"],
        "port": row["port"],
        "username": row.get("username") or "",
        "has_auth": bool(row.get("password_ciphertext")),
        "status": row.get("status") or "active",
        "remark": row.get("remark") or "",
        "last_check_at": row.get("last_check_at"),
        "last_check_ok": bool(row.get("last_check_ok")),
        "last_check_msg": row.get("last_check_msg") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "bound_keys": row.get("bound_keys", 0),
    }


@router.get("/proxies", tags=["管理员"])
async def list_proxies(request: Request):
    """代理池列表（含绑定该代理的上游密钥数；不回显密码）"""
    await require_admin(request)
    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT p.*, (SELECT COUNT(*) FROM upstream_keys k "
        "WHERE k.proxy_id = p.id AND k.proxy_mode = 'bind') AS bound_keys "
        "FROM proxies p ORDER BY p.created_at"
    )
    rotate_keys = await asyncio.to_thread(
        fetch_one, "SELECT COUNT(*) AS c FROM upstream_keys WHERE proxy_mode = 'rotate'"
    )
    return {
        "proxies": [_proxy_row_to_dict(dict(r)) for r in rows],
        "rotate_keys": (rotate_keys or {}).get("c", 0),
        "runtime": proxy_pool.get_status(),
    }


@router.post("/proxies", tags=["管理员"])
async def create_proxy(req: ProxyCreateRequest, request: Request):
    """添加代理入池（socks5/socks5h/http/https，支持无认证或账号密码认证）"""
    await require_admin(request)

    scheme = (req.scheme or "socks5").strip().lower()
    if scheme not in ALLOWED_SCHEMES:
        raise HTTPException(status_code=400, detail=f"代理协议非法，可选: {', '.join(ALLOWED_SCHEMES)}")
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="代理名称不能为空")
    username = (req.username or "").strip()
    password = req.password or ""
    if password and not username:
        raise HTTPException(status_code=400, detail="填写密码时必须同时填写用户名")

    # 借 URL 拼装做地址/端口合法性校验（失败即 400，不落库）
    try:
        build_proxy_url(scheme, req.host, req.port, username, password)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    cipher = ""
    if password:
        master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
        if not master_key:
            raise HTTPException(status_code=500, detail="主密钥未配置")
        cipher = encrypt_proxy_secret(password, master_key)

    proxy_id = str(uuid.uuid4())
    now = utcnow()
    await asyncio.to_thread(
        execute,
        "INSERT INTO proxies (id, name, scheme, host, port, username, password_ciphertext, "
        "status, remark, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)",
        (proxy_id, name, scheme, req.host.strip(), int(req.port), username, cipher,
         (req.remark or "").strip(), now, now),
    )
    proxy_pool.invalidate()

    await asyncio.to_thread(
        insert_audit, "create", "proxy", proxy_id,
        f"添加代理: {name} {scheme}://{req.host}:{req.port} 认证={'有' if password else '无'}",
    )
    return {"id": proxy_id, "message": "创建成功"}


@router.post("/proxies/bulk", tags=["管理员"])
async def bulk_create_proxies(req: ProxyBulkCreateRequest, request: Request):
    """批量添加代理：每行一个 scheme://[user:pass@]host:port，名称自动生成

    单个添加走 POST /proxies，两条路径并存。逐行报告成功/跳过原因，跳过的行不阻断
    其余行入库；全部有效行走一条多值 INSERT，要么全进要么全不进。
    """
    await require_admin(request)

    items = parse_bulk_proxies(req.proxy_urls)
    if not items:
        raise HTTPException(status_code=400, detail="请至少粘贴一行代理（空行与 # 注释行会被忽略）")
    todo = [it for it in items if "host" in it]
    if len(todo) > BULK_MAX_LINES:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {BULK_MAX_LINES} 行，本次有效 {len(todo)} 行，请分批提交",
        )

    rows = await asyncio.to_thread(fetch_all, "SELECT name, scheme, host, port, username FROM proxies")
    # 代理的身份信息（协议/地址/端口/用户名）都是明文列，查重一条 SELECT 即可，
    # 不像上游密钥那样必须解密——同 IP 同端口不同账号是不同代理，故按四元组判重
    existing = {(r["scheme"], r["host"], int(r["port"]), r.get("username") or "") for r in rows}
    for it in items:
        if "host" not in it:
            continue
        if (it["scheme"], it["host"], it["port"], it["username"]) in existing:
            it["reason"] = "库中已存在相同代理（协议+地址+端口+用户名）"
            it.pop("password", None)
            it.pop("host")
    todo = [it for it in items if "host" in it]

    created = []
    if todo:
        master_key = ""
        if any(it["password"] for it in todo):
            master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
            if not master_key:
                raise HTTPException(status_code=500, detail="主密钥未配置")

        def _encrypt_all() -> list:
            """有密码的行逐条加密；每次加密都要重跑一遍 HKDF，故整批放进线程"""
            return [encrypt_proxy_secret(it["password"], master_key) if it["password"] else ""
                    for it in todo]

        ciphers = await asyncio.to_thread(_encrypt_all)
        names = gen_bulk_names(req.name_prefix, [r["name"] for r in rows], len(todo))
        remark = (req.remark or "").strip()
        now = utcnow()
        params = []
        for it, name, cipher in zip(todo, names, ciphers):
            it["id"] = str(uuid.uuid4())
            it["name"] = name
            it["has_auth"] = bool(cipher)
            params.extend([
                it["id"], name, it["scheme"], it["host"], it["port"],
                it["username"], cipher, remark, now, now,
            ])
        placeholders = ", ".join(["(" + ", ".join(["%s"] * 10) + ", 'active')"] * len(todo))
        await asyncio.to_thread(
            execute,
            "INSERT INTO proxies "
            "(id, name, scheme, host, port, username, password_ciphertext, remark, "
            "created_at, updated_at, status) VALUES " + placeholders,
            tuple(params),
        )
        proxy_pool.invalidate()

        await asyncio.to_thread(
            insert_audit_many,
            [("create", "proxy", it["id"],
              f"批量添加代理: {it['name']} {it['scheme']}://{it['host']}:{it['port']} "
              f"认证={'有' if it['has_auth'] else '无'}")
             for it in todo],
        )
        created = [{"line": it["line"], "id": it["id"], "name": it["name"], "scheme": it["scheme"],
                    "host": it["host"], "port": it["port"], "username": it["username"],
                    "has_auth": it["has_auth"]}
                   for it in todo]

    skipped = [{"line": it["line"], "reason": it["reason"]} for it in items if "reason" in it]
    return {
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "skipped": skipped,
        "message": f"成功 {len(created)} 个，跳过 {len(skipped)} 个",
    }


@router.put("/proxies/{proxy_id}", tags=["管理员"])
async def update_proxy(proxy_id: str, req: ProxyUpdateRequest, request: Request):
    """编辑代理（password 传空字符串表示清除认证信息）"""
    await require_admin(request)

    existing = await asyncio.to_thread(fetch_one, "SELECT * FROM proxies WHERE id = %s", (proxy_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="代理不存在")

    merged = {
        "scheme": (req.scheme or existing["scheme"]).strip().lower(),
        "host": (req.host or existing["host"]).strip(),
        "port": int(req.port if req.port is not None else existing["port"]),
        "username": (req.username if req.username is not None else (existing.get("username") or "")).strip(),
    }
    if merged["scheme"] not in ALLOWED_SCHEMES:
        raise HTTPException(status_code=400, detail=f"代理协议非法，可选: {', '.join(ALLOWED_SCHEMES)}")
    try:
        build_proxy_url(merged["scheme"], merged["host"], merged["port"], merged["username"], "")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.status is not None and req.status not in ("active", "inactive"):
        raise HTTPException(status_code=400, detail="status 仅支持 active / inactive")

    updates = ["scheme = %s", "host = %s", "port = %s", "username = %s"]
    params = [merged["scheme"], merged["host"], merged["port"], merged["username"]]
    if req.name is not None:
        if not req.name.strip():
            raise HTTPException(status_code=400, detail="代理名称不能为空")
        updates.append("name = %s")
        params.append(req.name.strip())
    if req.status is not None:
        updates.append("status = %s")
        params.append(req.status)
    if req.remark is not None:
        updates.append("remark = %s")
        params.append(req.remark.strip())
    if req.password is not None:
        if req.password == "":
            updates.append("password_ciphertext = %s")
            params.append("")
        else:
            if not merged["username"]:
                raise HTTPException(status_code=400, detail="填写密码时必须同时填写用户名")
            master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
            if not master_key:
                raise HTTPException(status_code=500, detail="主密钥未配置")
            updates.append("password_ciphertext = %s")
            params.append(encrypt_proxy_secret(req.password, master_key))
    # 清空用户名时同步清除密码，避免残留半套凭据
    if req.username is not None and not merged["username"] and req.password is None:
        updates.append("password_ciphertext = %s")
        params.append("")

    updates.append("updated_at = %s")
    params.append(utcnow())
    params.append(proxy_id)
    await asyncio.to_thread(execute, f"UPDATE proxies SET {', '.join(updates)} WHERE id = %s", tuple(params))
    proxy_pool.invalidate()

    await asyncio.to_thread(insert_audit, "update", "proxy", proxy_id, f"更新代理: {existing['name']}")
    return {"message": "更新成功"}


@router.delete("/proxies/{proxy_id}", tags=["管理员"])
async def delete_proxy(proxy_id: str, request: Request):
    """删除代理（同时把绑定该代理的上游密钥回退为直连）"""
    await require_admin(request)

    existing = await asyncio.to_thread(fetch_one, "SELECT name FROM proxies WHERE id = %s", (proxy_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="代理不存在")

    bound = await asyncio.to_thread(
        fetch_one, "SELECT COUNT(*) AS c FROM upstream_keys WHERE proxy_id = %s", (proxy_id,)
    )
    bound_count = (bound or {}).get("c", 0)
    if bound_count:
        await asyncio.to_thread(
            execute,
            "UPDATE upstream_keys SET proxy_mode = 'direct', proxy_id = NULL, updated_at = %s "
            "WHERE proxy_id = %s",
            (utcnow(), proxy_id),
        )
    await asyncio.to_thread(execute, "DELETE FROM proxies WHERE id = %s", (proxy_id,))
    proxy_pool.invalidate()

    await asyncio.to_thread(
        insert_audit, "delete", "proxy", proxy_id,
        f"删除代理: {existing['name']}，{bound_count} 个上游密钥已回退直连",
    )
    return {"message": f"删除成功，{bound_count} 个上游密钥已回退直连", "unbound_keys": bound_count}


@router.post("/proxies/{proxy_id}/test", tags=["管理员"])
async def test_proxy(proxy_id: str, request: Request):
    """连通性测试：经该代理访问上游 /models（拿到任意 HTTP 状态即视为通道可用）"""
    await require_admin(request)

    row = await asyncio.to_thread(fetch_one, "SELECT * FROM proxies WHERE id = %s", (proxy_id,))
    if not row:
        raise HTTPException(status_code=404, detail="代理不存在")

    password = ""
    if row.get("password_ciphertext"):
        master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
        if not master_key:
            raise HTTPException(status_code=500, detail="主密钥未配置")
        try:
            from app.security import decrypt_proxy_secret
            password = decrypt_proxy_secret(row["password_ciphertext"], master_key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"代理密码解密失败: {e}")

    try:
        proxy_url = build_proxy_url(
            row["scheme"], row["host"], row["port"], row.get("username") or "", password
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base_url = await asyncio.to_thread(get_setting, "upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    ok, msg = await proxy_pool.probe(proxy_url, base_url.rstrip("/") + "/models")

    await asyncio.to_thread(
        execute,
        "UPDATE proxies SET last_check_at = %s, last_check_ok = %s, last_check_msg = %s WHERE id = %s",
        (utcnow(), 1 if ok else 0, msg[:300], proxy_id),
    )
    await asyncio.to_thread(
        insert_audit, "test", "proxy", proxy_id, f"代理连通性测试: {'成功' if ok else '失败'} {msg[:120]}"
    )
    return {"ok": ok, "message": msg}


# ========== 上游密钥管理 ==========

def parse_bulk_keys(raw: str) -> list:
    """多行文本 → 逐行解析结果，保持输入顺序

    每项形如 {"line": 行号, "api_key": 明文} 或 {"line": 行号, "reason": 跳过原因}。
    空行与 `#` 注释行直接忽略（不进结果），方便管理员在粘贴的密钥清单里写备注。
    明文只在进程内存中流转：不写日志、不进响应体（响应只回 mask_secret 后的前缀）。
    """
    seen = {}
    out = []
    for lineno, line in enumerate((raw or "").splitlines(), start=1):
        key = line.strip()
        if not key or key.startswith("#"):
            continue
        if len(key.split()) > 1:
            # 中间带空格通常是复制时把两个密钥连在了一行，或掺进了说明文字
            out.append({"line": lineno, "reason": "含空格，疑似串行或掺入说明文字"})
        elif len(key) < BULK_KEY_MIN_LEN:
            out.append({"line": lineno, "reason": f"长度不足 {BULK_KEY_MIN_LEN} 字符"})
        elif len(key) > BULK_KEY_MAX_LEN:
            out.append({"line": lineno, "reason": f"长度超过 {BULK_KEY_MAX_LEN} 字符"})
        elif key in seen:
            out.append({"line": lineno, "reason": f"与本批第 {seen[key]} 行重复"})
        else:
            seen[key] = lineno
            out.append({"line": lineno, "api_key": key})
    return out


def gen_bulk_names(prefix: str, existing: list, count: int) -> list:
    """生成 count 个 {前缀}-{序号} 形式的密钥名，序号从库内同前缀最大值续排

    表结构没给 name 建唯一索引，重名不会报错但会让运维看列表时分不清，因此这里
    既续排也跳过已被占用的名字（例如有人手工建过 nv-07）。
    """
    prefix = (prefix or "").strip()[:BULK_NAME_PREFIX_MAX] or BULK_NAME_PREFIX
    taken = set(existing or [])
    pattern = re.compile(r"^" + re.escape(prefix) + r"-(\d+)$")
    seq = 0
    for name in taken:
        m = pattern.match(name or "")
        if m:
            seq = max(seq, int(m.group(1)))
    names = []
    while len(names) < count:
        seq += 1
        candidate = f"{prefix}-{seq:02d}"
        if candidate in taken:
            continue
        taken.add(candidate)
        names.append(candidate)
    return names


@router.get("/upstreams", tags=["管理员"])
async def list_upstreams(request: Request):
    """上游密钥列表（含调度器实时状态）"""
    await require_admin(request)
    # 显式列名：排除 api_key_ciphertext（密文不出列表接口，明文仅走 /reveal 端点）
    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT k.id, k.name, k.provider, k.key_prefix, k.weight, k.rpm_limit, "
        "k.switch_threshold, k.status, k.created_at, k.updated_at, "
        "COALESCE(k.proxy_mode, 'direct') AS proxy_mode, k.proxy_id, p.name AS proxy_name "
        "FROM upstream_keys k LEFT JOIN proxies p ON p.id = k.proxy_id "
        "ORDER BY k.created_at"
    )
    scheduler = get_scheduler()
    all_buckets = scheduler.get_bucket_stats()

    # 按 key_id 聚合桶数据
    buckets_by_key = {}
    for b in all_buckets:
        buckets_by_key.setdefault(b["key_id"], []).append(b)

    # 全局在途请求数按key_id聚合
    inflight_by_key = {}
    try:
        for cid, m in scheduler._client_metrics.items():
            # 客户端级粒度，无法直接按key_id聚合，保留为0
            pass
    except Exception:
        pass

    result = []
    for row in rows:
        r = dict(row)
        key_buckets = buckets_by_key.get(r["id"], [])
        r["bucket_count"] = len(key_buckets)
        r["cooled_buckets"] = sum(1 for b in key_buckets if b["cooldown_remaining"] > 0)
        r["soft_busy_buckets"] = sum(1 for b in key_buckets if b["soft_busy"])
        r["isolated_buckets"] = sum(1 for b in key_buckets if b["isolation_remaining"] > 0)
        r["avg_health"] = round(sum(b["health_score"] for b in key_buckets) / len(key_buckets), 1) if key_buckets else 100
        # 当前RPM（该密钥所有桶RPM之和）
        r["current_rpm"] = sum(b.get("rpm", 0) for b in key_buckets)
        # 在途请求数（调度器暂未按key_id统计，保留0）
        r["inflight_requests"] = inflight_by_key.get(r["id"], 0)
        # 成功率（聚合）
        total_req = sum(b.get("total_requests", 0) for b in key_buckets)
        total_success = sum(b.get("total_success", 0) for b in key_buckets)
        r["success_rate"] = round(total_success / total_req * 100, 2) if total_req > 0 else 100.0
        r["total_requests"] = total_req
        r["total_429"] = sum(b.get("total_429", 0) for b in key_buckets)
        r["total_5xx"] = sum(b.get("total_5xx", 0) for b in key_buckets)
        result.append(r)
    return result


@router.post("/upstreams", tags=["管理员"])
async def create_upstream(req: UpstreamCreateRequest, request: Request):
    """添加上游密钥（可指定出网代理模式）"""
    await require_admin(request)

    proxy_mode, proxy_id = await _validate_proxy_binding(req.proxy_mode, req.proxy_id)

    master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
    if not master_key:
        raise HTTPException(status_code=500, detail="主密钥未配置")

    ciphertext = encrypt_upstream_key(req.api_key, master_key)
    key_id = str(uuid.uuid4())
    now = utcnow()

    await asyncio.to_thread(
        execute,
        "INSERT INTO upstream_keys "
        "(id, name, provider, api_key_ciphertext, key_prefix, weight, rpm_limit, switch_threshold, "
        "status, created_at, updated_at, proxy_mode, proxy_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s)",
        (key_id, req.name, req.provider, ciphertext, mask_secret(req.api_key),
         req.weight, req.rpm_limit, req.switch_threshold, now, now, proxy_mode, proxy_id),
    )

    # 清除调度器缓存
    scheduler = get_scheduler()
    scheduler.invalidate_key_cache(key_id)
    proxy_pool.invalidate()

    await asyncio.to_thread(
        insert_audit, "create", "upstream_key", key_id,
        f"创建上游密钥: {req.name} 出网={proxy_mode}",
    )
    return {"id": key_id, "message": "创建成功"}


@router.post("/upstreams/bulk", tags=["管理员"])
async def bulk_create_upstreams(req: UpstreamBulkCreateRequest, request: Request):
    """批量添加上游密钥：每行一个密钥，名称自动生成（单个添加走 POST /upstreams，两条路径并存）

    逐行报告成功/跳过原因；跳过的行不阻断其余行入库。全部有效行走一条多值 INSERT，
    要么全进要么全不进，不会留下"导入一半"的中间态。
    """
    await require_admin(request)

    proxy_mode, proxy_id = await _validate_proxy_binding(req.proxy_mode, req.proxy_id)

    items = parse_bulk_keys(req.api_keys)
    if not items:
        raise HTTPException(status_code=400, detail="请至少粘贴一行密钥（空行与 # 注释行会被忽略）")
    todo = [it for it in items if "api_key" in it]
    if len(todo) > BULK_MAX_LINES:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {BULK_MAX_LINES} 行，本次有效 {len(todo)} 行，请分批提交",
        )

    master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
    if not master_key:
        raise HTTPException(status_code=500, detail="主密钥未配置")

    rows = await asyncio.to_thread(fetch_all, "SELECT name, api_key_ciphertext FROM upstream_keys")

    def _existing_plaintexts() -> set:
        """解出库内已有明文用于查重。单行密文损坏只丢掉该行的查重能力，不阻断本次导入。

        Fernet 密文带随机 IV，同一明文两次加密结果不同，无法靠比对密文查重，只能解密。
        放进线程是因为每次解密都要跑一遍 HKDF，密钥多时累计毫秒级，不该占着事件循环。
        """
        out = set()
        for r in rows:
            try:
                out.add(decrypt_upstream_key(r["api_key_ciphertext"], master_key))
            except Exception:
                continue
        return out

    existing_keys = await asyncio.to_thread(_existing_plaintexts)
    for it in items:
        if it.get("api_key") in existing_keys:
            it.pop("api_key")
            it["reason"] = "库中已存在相同密钥"
    todo = [it for it in items if "api_key" in it]

    created = []
    if todo:
        names = gen_bulk_names(req.name_prefix, [r["name"] for r in rows], len(todo))
        ciphertexts = await asyncio.to_thread(
            lambda: [encrypt_upstream_key(it["api_key"], master_key) for it in todo]
        )
        now = utcnow()
        params = []
        for it, name, ciphertext in zip(todo, names, ciphertexts):
            it["id"] = str(uuid.uuid4())
            it["name"] = name
            it["key_prefix"] = mask_secret(it["api_key"])
            params.extend([
                it["id"], name, req.provider, ciphertext, it["key_prefix"],
                req.weight, req.rpm_limit, req.switch_threshold, "active",
                now, now, proxy_mode, proxy_id,
            ])
        placeholders = ", ".join(["(" + ", ".join(["%s"] * 13) + ")"] * len(todo))
        await asyncio.to_thread(
            execute,
            "INSERT INTO upstream_keys "
            "(id, name, provider, api_key_ciphertext, key_prefix, weight, rpm_limit, switch_threshold, "
            "status, created_at, updated_at, proxy_mode, proxy_id) VALUES " + placeholders,
            tuple(params),
        )

        scheduler = get_scheduler()
        for it in todo:
            scheduler.invalidate_key_cache(it["id"])
        proxy_pool.invalidate()

        await asyncio.to_thread(
            insert_audit_many,
            [("create", "upstream_key", it["id"], f"批量创建上游密钥: {it['name']} 出网={proxy_mode}")
             for it in todo],
        )
        created = [{"line": it["line"], "id": it["id"], "name": it["name"], "key_prefix": it["key_prefix"]}
                   for it in todo]

    skipped = [{"line": it["line"], "reason": it["reason"]} for it in items if "reason" in it]
    return {
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "skipped": skipped,
        "message": f"成功 {len(created)} 个，跳过 {len(skipped)} 个",
    }


@router.put("/upstreams/{key_id}", tags=["管理员"])
async def update_upstream(key_id: str, req: UpstreamUpdateRequest, request: Request):
    """编辑上游密钥（含出网代理模式）"""
    await require_admin(request)

    existing = await asyncio.to_thread(fetch_one, "SELECT * FROM upstream_keys WHERE id = %s", (key_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="密钥不存在")

    updates = []
    params = []
    for field in ["name", "weight", "rpm_limit", "switch_threshold", "status"]:
        val = getattr(req, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    # 代理模式变更：direct/rotate 会清空 proxy_id，bind 必须给出有效 proxy_id
    if req.proxy_mode is not None:
        proxy_mode, proxy_id = await _validate_proxy_binding(
            req.proxy_mode,
            req.proxy_id if req.proxy_id is not None else existing.get("proxy_id"),
        )
        updates.append("proxy_mode = %s")
        params.append(proxy_mode)
        updates.append("proxy_id = %s")
        params.append(proxy_id)
    elif req.proxy_id is not None:
        # 仅换绑代理，沿用原模式
        proxy_mode, proxy_id = await _validate_proxy_binding(
            existing.get("proxy_mode") or "direct", req.proxy_id
        )
        updates.append("proxy_id = %s")
        params.append(proxy_id)

    if updates:
        updates.append("updated_at = %s")
        params.append(utcnow())
        params.append(key_id)
        await asyncio.to_thread(execute, f"UPDATE upstream_keys SET {', '.join(updates)} WHERE id = %s", tuple(params))

    scheduler = get_scheduler()
    scheduler.invalidate_key_cache(key_id)
    proxy_pool.invalidate()

    await asyncio.to_thread(insert_audit, "update", "upstream_key", key_id, f"更新上游密钥")
    return {"message": "更新成功"}


@router.delete("/upstreams/{key_id}", tags=["管理员"])
async def delete_upstream(key_id: str, request: Request):
    """删除上游密钥"""
    await require_admin(request)

    await asyncio.to_thread(execute, "DELETE FROM upstream_keys WHERE id = %s", (key_id,))

    scheduler = get_scheduler()
    scheduler.invalidate_key_cache(key_id)
    proxy_pool.invalidate()

    await asyncio.to_thread(insert_audit, "delete", "upstream_key", key_id, f"删除上游密钥")
    return {"message": "删除成功"}


@router.post("/upstreams/{key_id}/unfreeze", tags=["管理员"])
async def unfreeze_upstream(key_id: str, request: Request):
    """解冻密钥所有桶"""
    await require_admin(request)

    scheduler = get_scheduler()
    count = scheduler.unfreeze_key_all_buckets(key_id)

    await asyncio.to_thread(insert_audit, "unfreeze", "upstream_key", key_id, f"解冻{count}个桶")
    return {"message": f"已解冻{count}个桶", "count": count}


@router.get("/upstreams/{key_id}/reveal", tags=["管理员"])
async def reveal_upstream_key(key_id: str, request: Request):
    """解密并返回上游密钥明文（仅管理员可调用，会写入审计日志）"""
    await require_admin(request)

    row = await asyncio.to_thread(fetch_one, "SELECT name, api_key_ciphertext, key_prefix FROM upstream_keys WHERE id = %s", (key_id,))
    if not row:
        raise HTTPException(status_code=404, detail="密钥不存在")

    master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
    if not master_key:
        raise HTTPException(status_code=500, detail="主密钥未配置")

    try:
        plaintext = decrypt_upstream_key(row["api_key_ciphertext"], master_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"密钥解密失败: {e}")

    await asyncio.to_thread(insert_audit, "reveal", "upstream_key", key_id, f"查看上游密钥明文: {row['name']}")
    return {"key": plaintext, "prefix": row["key_prefix"], "name": row["name"]}


@router.post("/upstreams/health-check", tags=["管理员"])
async def check_upstream_keys_health(request: Request):
    """主动检查所有上游密钥的健康状态（测试NVIDIA API连通性）"""
    await require_admin(request)
    scheduler = get_scheduler()
    from app.database import fetch_all as db_fetch

    rows = await asyncio.to_thread(db_fetch, "SELECT id, name, key_prefix, status FROM upstream_keys WHERE status = 'active'")
    results = []
    import httpx

    # 循环内重复读取的配置提取到循环外（原先每个密钥各查一次DB）
    health_base_url = await asyncio.to_thread(get_setting, "upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    _master_key = await asyncio.to_thread(get_setting, "upstream_master_key")

    # 逐个密钥按其出网模式取客户端（代理专用密钥不能走直连，否则会被误判为不健康）
    for row in rows:
        key_id = row["id"]
        api_key = await asyncio.to_thread(scheduler._ensure_key_cached, key_id, None)
        if not api_key:
            # 尝试从数据库解密
            key_row = await asyncio.to_thread(db_fetch, "SELECT api_key_ciphertext FROM upstream_keys WHERE id = %s", (key_id,))
            if key_row and _master_key:
                from app.security import decrypt_upstream_key
                try:
                    api_key = decrypt_upstream_key(key_row[0]["api_key_ciphertext"], _master_key)
                except Exception:
                    pass
        if not api_key:
            results.append({"id": key_id, "name": row["name"], "status": "unknown", "error": "无法解密密钥"})
            continue

        try:
            client = await scheduler.get_http_pool(key_id)
            resp = await client.get(
                f"{health_base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=httpx.Timeout(8.0),
            )
            if resp.status_code == 200:
                results.append({"id": key_id, "name": row["name"], "status": "healthy"})
            elif resp.status_code in (401, 403):
                results.append({"id": key_id, "name": row["name"], "status": "invalid", "error": f"HTTP {resp.status_code}"})
            else:
                results.append({"id": key_id, "name": row["name"], "status": "unknown", "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"id": key_id, "name": row["name"], "status": "error", "error": str(e)[:100]})

    # 汇总
    healthy = sum(1 for r in results if r["status"] == "healthy")
    invalid = sum(1 for r in results if r["status"] == "invalid")
    errors = sum(1 for r in results if r["status"] in ("error", "unknown"))
    return {
        "total": len(results),
        "healthy": healthy,
        "invalid": invalid,
        "errors": errors,
        "results": results,
    }


# ========== 下游客户管理 ==========

@router.get("/clients", tags=["管理员"])
async def list_clients(request: Request):
    """下游客户列表"""
    await require_admin(request)
    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM client_api_keys WHERE client_id = c.id) as key_count "
        "FROM clients c ORDER BY c.created_at DESC"
    )
    result = []
    for row in rows:
        r = dict(row)
        # display_name 直接使用数据库中的 name（已包含名称和ID，格式如：用户名(ID:123)）
        r["display_name"] = r.get("name", r["id"])
        result.append(r)
    return result


@router.post("/clients", tags=["管理员"])
async def create_client(req: ClientCreateRequest, request: Request):
    """创建下游客户"""
    await require_admin(request)

    client_id = str(uuid.uuid4())
    now = utcnow()

    await asyncio.to_thread(
        execute,
        "INSERT INTO clients (id, name, user_type, status, created_at, updated_at) VALUES (%s, %s, %s, 'active', %s, %s)",
        (client_id, req.name, (req.user_type or "old"), now, now),
    )

    await asyncio.to_thread(insert_audit, "create", "client", client_id, f"创建客户: {req.name} (type={req.user_type or 'old'})")
    return {"id": client_id, "message": "创建成功"}


@router.get("/clients/{client_id}", tags=["管理员"])
async def get_client(client_id: str, request: Request):
    """客户详情"""
    await require_admin(request)
    row = await asyncio.to_thread(fetch_one, "SELECT * FROM clients WHERE id = %s", (client_id,))
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    return row


@router.put("/clients/{client_id}", tags=["管理员"])
async def update_client(client_id: str, req: ClientUpdateRequest, request: Request):
    """更新客户"""
    await require_admin(request)

    updates = []
    params = []
    for field in ["name", "status", "user_type"]:
        val = getattr(req, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    if updates:
        updates.append("updated_at = %s")
        params.append(utcnow())
        params.append(client_id)
        await asyncio.to_thread(execute, f"UPDATE clients SET {', '.join(updates)} WHERE id = %s", tuple(params))

    await asyncio.to_thread(insert_audit, "update", "client", client_id, f"更新客户")
    return {"message": "更新成功"}


@router.delete("/clients/{client_id}", tags=["管理员"])
async def delete_client(client_id: str, request: Request):
    """删除客户"""
    await require_admin(request)

    def _delete_client_sync():
        # 小循环（2条DELETE+1条审计）整体包一次 to_thread，避免逐条调度
        execute("DELETE FROM client_api_keys WHERE client_id = %s", (client_id,))
        execute("DELETE FROM clients WHERE id = %s", (client_id,))
        insert_audit("delete", "client", client_id, f"删除客户")

    await asyncio.to_thread(_delete_client_sync)
    return {"message": "删除成功"}


@router.get("/clients/{client_id}/keys", tags=["管理员"])
async def list_client_keys(client_id: str, request: Request):
    """客户密钥列表"""
    await require_admin(request)
    # 显式列名：排除 key_hash / key_ciphertext（密文与哈希不出列表接口，明文仅走 /reveal 端点）
    return await asyncio.to_thread(
        fetch_all,
        "SELECT id, client_id, key_prefix, status, created_at, last_used_at "
        "FROM client_api_keys WHERE client_id = %s ORDER BY created_at",
        (client_id,),
    )


@router.get("/clients/{client_id}/keys/{key_id}/reveal", tags=["管理员"])
async def reveal_client_key(client_id: str, key_id: str, request: Request):
    """解密并返回客户密钥明文（仅管理员可调用）"""
    await require_admin(request)
    key_row = await asyncio.to_thread(
        fetch_one,
        "SELECT id, key_ciphertext, key_prefix, status FROM client_api_keys WHERE id=%s AND client_id=%s",
        (key_id, client_id),
    )
    if not key_row:
        raise HTTPException(status_code=404, detail="密钥不存在")
    if not key_row["key_ciphertext"]:
        raise HTTPException(status_code=500, detail={
            "message": "密钥加密数据缺失（无法复原），建议删除后重新创建",
            "type": "decryption_error", "code": "missing_ciphertext",
        })
    try:
        master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
        if not master_key:
            raise HTTPException(status_code=500, detail="主密钥未配置")
        plaintext = decrypt_secret(key_row["key_ciphertext"], master_key)
        await asyncio.to_thread(insert_audit, "reveal", "client_key", key_id, f"查看客户密钥明文: {key_row['key_prefix']}")
        return {"key": plaintext, "prefix": key_row["key_prefix"], "status": key_row["status"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解密失败: {e}")


@router.post("/clients/{client_id}/keys", tags=["管理员"])
async def create_client_key(client_id: str, request: Request):
    """创建客户API密钥"""
    await require_admin(request)

    client = await asyncio.to_thread(fetch_one, "SELECT * FROM clients WHERE id = %s", (client_id,))
    if not client:
        raise HTTPException(status_code=404, detail="客户不存在")

    master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
    if not master_key:
        raise HTTPException(status_code=500, detail="主密钥未配置")

    # 生成下游客户端API密钥（hash_secret 为 SHA-256，微秒级，无需 to_thread）
    api_key = generate_client_key()
    key_hash = hash_secret(api_key)
    ciphertext = encrypt_secret(api_key, master_key)
    key_id = str(uuid.uuid4())
    now = utcnow()

    # 强制命名规范：客户平台ID+平台ID号
    key_name = f"{client['id'][:8]}+{key_id[:8]}"

    await asyncio.to_thread(
        execute,
        "INSERT INTO client_api_keys "
        "(id, client_id, key_hash, key_prefix, key_ciphertext, status, created_at, last_used_at) "
        "VALUES (%s, %s, %s, %s, %s, 'active', %s, NULL)",
        (key_id, client_id, key_hash, mask_secret(api_key), ciphertext, now),
    )

    await asyncio.to_thread(insert_audit, "create", "client_key", key_id, f"创建客户密钥: {key_name} (client={client['name']})")

    # 返回完整密钥（仅此一次显示）
    return {
        "id": key_id,
        "key": api_key,
        "key_prefix": mask_secret(api_key),
        "key_name": key_name,
        "message": "密钥已创建，请妥善保存（仅此一次显示完整密钥）",
    }


@router.delete("/clients/{client_id}/keys/{key_id}", tags=["管理员"])
async def delete_client_key(client_id: str, key_id: str, request: Request):
    """删除客户密钥"""
    await require_admin(request)
    await asyncio.to_thread(execute, "DELETE FROM client_api_keys WHERE id = %s AND client_id = %s", (key_id, client_id))
    # 立即失效该密钥的调度器认证缓存，避免已删密钥在TTL窗口内仍可通过认证
    get_scheduler().invalidate_client_key_cache()
    await asyncio.to_thread(insert_audit, "delete", "client_key", key_id, f"删除客户密钥")
    return {"message": "删除成功"}


@router.put("/clients/{client_id}/keys/{key_id}", tags=["管理员"])
async def update_client_key(client_id: str, key_id: str, request: Request):
    """更新客户密钥（启用/禁用）"""
    await require_admin(request)
    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in ("active", "revoked"):
        raise HTTPException(status_code=400, detail="状态只能是 active 或 revoked")
    key_row = await asyncio.to_thread(
        fetch_one,
        "SELECT id, status FROM client_api_keys WHERE id=%s AND client_id=%s",
        (key_id, client_id),
    )
    if not key_row:
        raise HTTPException(status_code=404, detail="密钥不存在")
    await asyncio.to_thread(
        execute,
        "UPDATE client_api_keys SET status = %s WHERE id = %s AND client_id = %s",
        (new_status, key_id, client_id),
    )
    from app.scheduler import get_scheduler
    get_scheduler().invalidate_active_keys_cache()
    # 立即失效认证缓存，避免被禁用密钥在TTL窗口内仍可通过认证
    get_scheduler().invalidate_client_key_cache()
    await asyncio.to_thread(insert_audit, "update", "client_key", key_id, f"密钥状态改为 {new_status}")
    return {"message": f"密钥状态已更新为 {new_status}", "status": new_status}


@router.get("/clients/{client_id}/usage", tags=["管理员"])
async def client_usage(client_id: str, request: Request):
    """客户用量统计"""
    await require_admin(request)
    today = utcnow()[:10]
    stats = await asyncio.to_thread(
        fetch_one,
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as success, "
        "SUM(total_tokens) as tokens "
        "FROM request_logs WHERE client_id = %s AND created_at >= %s",
        (client_id, today + "T00:00:00Z"),
    )
    return stats or {"total": 0, "success": 0, "tokens": 0}


# ========== 复合桶监控 ==========

@router.get("/buckets", tags=["管理员"])
async def list_buckets(
    request: Request,
    model: Optional[str] = Query(None),
    key_id: Optional[str] = Query(None),
):
    """复合桶监控数据"""
    await require_admin(request)
    scheduler = get_scheduler()
    buckets = scheduler.get_bucket_stats()

    if model:
        buckets = [b for b in buckets if model.lower() in b["model"].lower()]
    if key_id:
        buckets = [b for b in buckets if key_id in b["key_id"]]

    return buckets


@router.post("/buckets/{key_id}/{model}/unfreeze", tags=["管理员"])
async def unfreeze_bucket(key_id: str, model: str, request: Request):
    """解冻特定桶"""
    await require_admin(request)
    scheduler = get_scheduler()
    scheduler.unfreeze_bucket(key_id, model)
    await asyncio.to_thread(insert_audit, "unfreeze", "bucket", f"{key_id}:{model}", f"解冻桶")
    return {"message": "桶已解冻"}


# ========== 算法引擎统计 ==========

@router.get("/algorithm-stats", tags=["管理员"])
async def algorithm_stats(request: Request):
    """17算法统计详情"""
    await require_admin(request)
    scheduler = get_scheduler()
    raw = scheduler.get_algorithm_stats()

    # 提取 global_stats 作为参考
    gs = raw.get("global_stats", {})

    # 前端期望字段：name, total_buckets, soft_busy_count, value, note
    result = {}
    for algo_id in range(1, 18):
        key = f"algorithm_{algo_id}"
        algo = raw.get(key, {})
        name = algo.get("name", f"算法{algo_id}")
        base = {"name": name}

        if algo_id == 1:
            base["total_buckets"] = gs.get("total_buckets", 0)
            base["value"] = gs.get("total_buckets", 0)
            base["note"] = "所有17个算法的数据根基"
        elif algo_id == 2:
            base["soft_busy_count"] = algo.get("soft_busy_count", 0)
            base["value"] = algo.get("soft_busy_count", 0)
            base["note"] = "软繁忙与冷却完全分离"
        elif algo_id == 3:
            base["value"] = algo.get("update_count", 0)
            base["note"] = f"统一阈值38，最后更新于{algo.get('last_update', 'N/A')}"
        elif algo_id == 4:
            base["value"] = algo.get("cooled_buckets", 0)
            base["note"] = f"429→{algo.get('cooldown_429_seconds', 5)}s / 403→{algo.get('cooldown_403_seconds', 60)}s / 超时→{algo.get('cooldown_timeout_seconds', 15)}s"
        elif algo_id == 5:
            base["value"] = algo.get("high_concurrency_clients", 0)
            base["note"] = "只监测不拦截"
        elif algo_id == 6:
            base["value"] = algo.get("high_burst_clients", 0)
            base["note"] = "只监测不拦截"
        elif algo_id == 7:
            base["value"] = algo.get("today_total", 0)
            base["note"] = "只监测不拦截"
        elif algo_id == 8:
            base["value"] = algo.get("total_5xx", 0)
            base["note"] = f"退避桶数: {algo.get('buckets_with_5xx', 0)}"
        elif algo_id == 9:
            base["value"] = algo.get("isolated_buckets", 0)
            base["note"] = f"隔离时长: {algo.get('isolation_seconds', 1800)}秒"
        elif algo_id == 10:
            base["value"] = algo.get("avg_health", 0)
            base["note"] = f"健康密钥数: {algo.get('healthy_key_count', 0)}"
        elif algo_id == 11:
            base["value"] = algo.get("avg_multiplier", 1.0)
            base["note"] = f"范围: {algo.get('weight_range', '0.5~2.0')}"
        elif algo_id == 12:
            base["value"] = algo.get("predicted_busy_count", 0)
            base["note"] = f"预测准确率: {algo.get('accuracy', 100)}%"
        elif algo_id == 13:
            base["value"] = algo.get("warming_up_buckets", 0)
            base["note"] = f"预热目标: {algo.get('warmup_target', 30)}次"
        elif algo_id == 14:
            base["value"] = algo.get("healing_buckets", 0)
            base["note"] = f"降级模式: {algo.get('degraded_mode', False)}"
        elif algo_id == 15:
            base["value"] = algo.get("molting_keys_count", 0)
            base["note"] = f"乘数范围: {algo.get('multiplier_range', '0.6~1.3')}"
        elif algo_id == 16:
            base["value"] = algo.get("molting_keys_count", 0)
            base["note"] = f"脱壳时长: {algo.get('molt_duration', 15)}秒"
        elif algo_id == 17:
            base["value"] = algo.get("active_bucket_windows", 0)
            base["note"] = algo.get("note", "v10.0核心：滑动窗口计数 + 公平轮询")

        result[key] = base

    return result


@router.get("/algorithm/{num}", tags=["管理员"])
async def algorithm_detail(num: int, request: Request):
    """单个算法专属页面详细数据"""
    await require_admin(request)
    if num < 1 or num > 17:
        raise HTTPException(status_code=400, detail={"message": "算法编号必须在1-17之间", "type": "validation_error", "code": "invalid_algorithm_num"})
    scheduler = get_scheduler()
    return await asyncio.to_thread(scheduler.get_algorithm_detail, num)


# ========== 请求日志 ==========

@router.get("/request-logs", tags=["管理员"])
async def request_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    client_id: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None),
    request_path: Optional[str] = Query(None),
    http_method: Optional[str] = Query(None),
    client_ip: Optional[str] = Query(None),
    log_category: Optional[str] = Query(None),
    error_type: Optional[str] = Query(None),
    business_code: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None, description="起始时间(ISO格式,如2026-01-01T00:00:00)"),
    end_time: Optional[str] = Query(None, description="结束时间(ISO格式)"),
    search: Optional[str] = Query(None, description="全文搜索(error_msg/error_detail/request_body)"),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query("desc"),
):
    """请求日志（分页 + 多维度筛选 + 排序）增强"""
    await require_admin(request)

    where = []
    params = []

    # v10.0 修复：不再默认过滤空白日志，管理员可看到全部日志
    # 可通过参数 exclude_empty=true 来过滤无业务数据的日志
    if not client_id and not model and not status_code and not business_code:
        # 无筛选条件时，默认排除纯扫描请求（404等）
        where.append("(status_code != 404 OR client_id IS NOT NULL AND client_id != '')")
    if client_id:
        where.append("client_id = %s")
        params.append(client_id)
    # LIKE 搜索参数统一转义 % 与 _（防通配符注入），并在 SQL 中声明 ESCAPE '\'
    if model:
        where.append("model LIKE %s ESCAPE '\\'")
        params.append(f"%{_like_escape(model)}%")
    if status_code:
        where.append("status_code = %s")
        params.append(status_code)
    if request_path:
        where.append("request_path LIKE %s ESCAPE '\\'")
        params.append(f"%{_like_escape(request_path)}%")
    if http_method:
        where.append("http_method = %s")
        params.append(http_method)
    if client_ip:
        where.append("client_ip = %s")
        params.append(client_ip)
    if log_category:
        where.append("log_category = %s")
        params.append(log_category)
    if error_type:
        where.append("error_type LIKE %s ESCAPE '\\'")
        params.append(f"%{_like_escape(error_type)}%")
    if business_code:
        where.append("business_code = %s")
        params.append(business_code)
    if start_time:
        where.append("created_at >= %s")
        params.append(start_time)
    if end_time:
        where.append("created_at <= %s")
        params.append(end_time)
    if search:
        where.append("(error_msg LIKE %s ESCAPE '\\' OR error_detail LIKE %s ESCAPE '\\' OR request_body LIKE %s ESCAPE '\\')")
        _esc = f"%{_like_escape(search)}%"
        params.extend([_esc, _esc, _esc])

    where_clause = " WHERE " + " AND ".join(where) if where else ""

    # 排序（白名单字段，防注入）
    allowed_sort = {
        "created_at": "created_at",
        "latency_ms": "latency_ms",
        "latency_us": "latency_us",
        "total_tokens": "total_tokens",
        "prompt_tokens": "prompt_tokens",
        "completion_tokens": "completion_tokens",
        "status_code": "status_code",
        "request_path": "request_path",
        "client_ip": "client_ip",
    }
    order_field = allowed_sort.get(sort_by, "created_at")
    order_dir = "ASC" if (sort_order or "desc").lower() == "asc" else "DESC"
    order_clause = f" ORDER BY {order_field} {order_dir}"

    offset = (page - 1) * page_size

    total = (await asyncio.to_thread(fetch_one, f"SELECT COUNT(*) as cnt FROM request_logs{where_clause}", tuple(params)))["cnt"]
    rows = await asyncio.to_thread(
        fetch_all,
        f"SELECT r.*, COALESCE(c.name, '') as client_name FROM request_logs r LEFT JOIN clients c ON r.client_id = c.id{where_clause}{order_clause} LIMIT %s OFFSET %s",
        tuple(params) + (page_size, offset),
    )

    # 确保关键字段非空
    for row in rows:
        if "client_name" not in row or not row["client_name"]:
            row["client_name"] = ""
        for field in ("request_path", "http_method", "client_ip"):
            if field not in row or row[field] is None:
                row[field] = ""

    return {"total": total, "page": page, "page_size": page_size, "data": rows}


@router.get("/request-logs/{log_id}", tags=["管理员"])
async def request_log_detail(log_id: str, request: Request):
    """单条请求日志详情"""
    await require_admin(request)
    row = await asyncio.to_thread(
        fetch_one,
        "SELECT r.*, COALESCE(c.name, '') as client_name FROM request_logs r LEFT JOIN clients c ON r.client_id = c.id WHERE r.id = %s",
        (log_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail={"message": "日志不存在", "type": "not_found", "code": "not_found"})
    return row


@router.get("/request-logs-stats/summary", tags=["管理员"])
async def request_logs_stats_summary(request: Request):
    """请求日志统计概览  - 实时数据，含错误追踪"""
    await require_admin(request)

    # 总数统计
    total = await asyncio.to_thread(fetch_one, "SELECT COUNT(*) as cnt FROM request_logs")["cnt"]

    # 按日志分类统计
    category_stats = await asyncio.to_thread(fetch_all, 
        "SELECT log_category, COUNT(*) as cnt FROM request_logs GROUP BY log_category"
    )

    # 按状态码统计
    status_stats = await asyncio.to_thread(fetch_all, 
        "SELECT status_code, COUNT(*) as cnt FROM request_logs GROUP BY status_code ORDER BY cnt DESC"
    )

    # 按HTTP方法统计
    method_stats = await asyncio.to_thread(fetch_all, 
        "SELECT http_method, COUNT(*) as cnt FROM request_logs WHERE http_method != '' GROUP BY http_method"
    )

    # 最近24小时请求量趋势（按小时分组）
    hourly_stats = await asyncio.to_thread(fetch_all, 
        """SELECT substr(created_at, 12, 2) as hour, COUNT(*) as cnt
           FROM request_logs
           WHERE created_at::timestamptz >= NOW() - INTERVAL '24 hours'
           GROUP BY hour ORDER BY hour"""
    )

    # Top 10 请求路径
    top_paths = await asyncio.to_thread(fetch_all, 
        """SELECT request_path, COUNT(*) as cnt FROM request_logs
           WHERE request_path != '' GROUP BY request_path ORDER BY cnt DESC LIMIT 10"""
    )

    # Top 10 客户端（含client_name）
    top_clients = await asyncio.to_thread(fetch_all, 
        """SELECT COALESCE(MAX(c.name), r.client_id) as client_display, COUNT(*) as cnt,
                  SUM(r.total_tokens) as total_tokens, AVG(r.latency_ms) as avg_latency
           FROM request_logs r
           LEFT JOIN clients c ON r.client_id = c.id
           WHERE r.client_id != '' AND r.client_id IS NOT NULL
           GROUP BY r.client_id ORDER BY cnt DESC LIMIT 10"""
    )

    # Top 10 客户端IP
    top_ips = await asyncio.to_thread(fetch_all, 
        """SELECT client_ip, COUNT(*) as cnt FROM request_logs
           WHERE client_ip != '' GROUP BY client_ip ORDER BY cnt DESC LIMIT 10"""
    )

    # 按日期统计（最近7天每日请求量）
    daily_stats = await asyncio.to_thread(fetch_all, 
        """SELECT (created_at::timestamptz AT TIME ZONE 'Asia/Shanghai')::date as date,
                  COUNT(*) as cnt,
                  SUM(CASE WHEN status_code=200 THEN 1 ELSE 0 END) as success_cnt,
                  SUM(CASE WHEN status_code>=400 AND status_code<500 THEN 1 ELSE 0 END) as client_err_cnt,
                  SUM(CASE WHEN status_code>=500 THEN 1 ELSE 0 END) as server_err_cnt,
                  SUM(total_tokens) as total_tokens,
                  AVG(latency_ms)::int as avg_latency
           FROM request_logs
           WHERE created_at::timestamptz >= NOW() - INTERVAL '7 days'
           GROUP BY date ORDER BY date"""
    )

    # 按客户端ID统计（最近7天并发分布）
    concurrency_stats = await asyncio.to_thread(fetch_all, 
        """SELECT client_id, COUNT(*) as cnt,
                  AVG(latency_ms)::int as avg_latency,
                  SUM(total_tokens) as total_tokens,
                  COUNT(DISTINCT model) as model_count
           FROM request_logs
           WHERE client_id != '' AND created_at::timestamptz >= NOW() - INTERVAL '7 days'
           GROUP BY client_id ORDER BY cnt DESC LIMIT 10"""
    )

    # Top 10 错误类型（含错误详情） - 修复：GROUP_CONCAT -> PostgreSQL string_agg
    top_errors = await asyncio.to_thread(fetch_all, 
        """SELECT error_type, business_code, COUNT(*) as cnt,
                  string_agg(DISTINCT error_detail, ', ') as details
           FROM request_logs
           WHERE log_category IN ('error', 'auth_fail') AND error_type != ''
           GROUP BY error_type, business_code ORDER BY cnt DESC LIMIT 10"""
    )

    # Top 10 模型（含token统计）
    top_models = await asyncio.to_thread(fetch_all, 
        """SELECT model, COUNT(*) as cnt,
                  SUM(total_tokens) as total_tokens,
                  AVG(latency_ms) as avg_latency
           FROM request_logs
           WHERE model != '' AND model IS NOT NULL
           GROUP BY model ORDER BY cnt DESC LIMIT 10"""
    )

    # 延迟统计
    latency_stats = await asyncio.to_thread(fetch_one, 
        """SELECT AVG(latency_ms) as avg_latency,
                  MAX(latency_ms) as max_latency,
                  SUM(CASE WHEN status_code=200 THEN 1 ELSE 0 END) as success_count,
                  SUM(CASE WHEN status_code>=400 AND status_code<500 THEN 1 ELSE 0 END) as client_error_count,
                  SUM(CASE WHEN status_code>=500 OR status_code=0 THEN 1 ELSE 0 END) as server_error_count,
                  SUM(CASE WHEN status_code=401 THEN 1 ELSE 0 END) as auth_fail_count,
                  SUM(CASE WHEN log_category='error' THEN 1 ELSE 0 END) as error_count,
                  SUM(total_tokens) as total_tokens,
                  SUM(prompt_tokens) as total_prompt_tokens,
                  SUM(completion_tokens) as total_completion_tokens
           FROM request_logs"""
    )

    # P95延迟（PostgreSQL 兼容：使用 percentile_cont）
    p95_row = await asyncio.to_thread(fetch_one, 
        """SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_latency
           FROM request_logs WHERE latency_ms > 0"""
    )

    # 最近10条错误日志（实时追踪）
    recent_errors = await asyncio.to_thread(fetch_all, 
        """SELECT r.id, r.status_code, r.error_type, r.business_code, r.error_msg,
                  r.error_detail, r.model, r.request_path,
                  COALESCE(c.name, r.client_id) as client_display,
                  r.created_at
           FROM request_logs r
           LEFT JOIN clients c ON r.client_id = c.id
           WHERE r.log_category IN ('error', 'auth_fail')
           ORDER BY r.created_at DESC LIMIT 10"""
    )

    # 成功率计算
    success_rate = round((latency_stats["success_count"] or 0) / total * 100, 2) if total > 0 else 0

    return {
        "total": total,
        "category_stats": category_stats,
        "status_stats": status_stats,
        "method_stats": method_stats,
        "hourly_stats": hourly_stats,
        "top_paths": top_paths,
        "top_clients": top_clients,
        "top_ips": top_ips,
        "top_errors": top_errors,
        "top_models": top_models,
        "recent_errors": recent_errors,
        "daily_stats": daily_stats,
        "concurrency_stats": concurrency_stats,
        "latency": {
            "avg_ms": round(latency_stats["avg_latency"] or 0, 2),
            "p95_ms": round((p95_row["p95_latency"] or 0), 2),
            "max_ms": latency_stats["max_latency"] or 0,
        },
        "success_count": latency_stats["success_count"] or 0,
        "error_count": latency_stats["error_count"] or 0,
        "auth_fail_count": latency_stats["auth_fail_count"] or 0,
        "success_rate": success_rate,
        "total_tokens": latency_stats["total_tokens"] or 0,
        "total_prompt_tokens": latency_stats["total_prompt_tokens"] or 0,
        "total_completion_tokens": latency_stats["total_completion_tokens"] or 0,
    }


@router.delete("/request-logs/cleanup", tags=["管理员"])
async def cleanup_request_logs(
    request: Request,
    days: int = Query(30, ge=1, description="保留最近N天的日志"),
):
    """清理历史请求日志 """
    await require_admin(request)

    # 时间戳统一走 database.py 的 utcnow/days_ago_utc 家族（Z格式），消除第三种时间格式变体
    cutoff = days_ago_utc(days)
    result = await asyncio.to_thread(execute, "DELETE FROM request_logs WHERE created_at < %s", (cutoff,))
    return {"message": f"已清理 {cutoff} 之前的日志", "cutoff": cutoff}


# ========== 审计日志 ==========

@router.get("/audit-logs", tags=["管理员"])
async def audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """审计日志"""
    await require_admin(request)
    offset = (page - 1) * page_size

    def _audit_logs_sync():
        total = fetch_one("SELECT COUNT(*) as cnt FROM audit_logs")["cnt"]
        rows = fetch_all(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (page_size, offset),
        )
        return total, rows

    total, rows = await asyncio.to_thread(_audit_logs_sync)
    return {"total": total, "page": page, "page_size": page_size, "data": rows}


# ========== 网关策略配置 ==========

@router.get("/settings", tags=["管理员"])
async def get_settings(request: Request):
    """获取网关策略配置"""
    await require_admin(request)
    keys = [
        "upstream_base_url", "chat_path", "models_path",
        "cooldown_seconds", "switch_threshold", "maintenance_mode",
    ]
    # 小循环（6个key）整体包一次 to_thread，避免逐条调度
    result = await asyncio.to_thread(lambda: {key: get_setting(key) for key in keys})
    return result


@router.post("/settings", tags=["管理员"])
async def save_settings(req: PolicyUpdateRequest, request: Request):
    """保存网关策略"""
    await require_admin(request)

    fields = ["upstream_base_url", "chat_path", "models_path", "cooldown_seconds", "switch_threshold"]

    def _save_settings_sync():
        # 小循环（≤5条写入）整体包一次 to_thread
        for field in fields:
            val = getattr(req, field, None)
            if val is not None:
                set_setting(field, str(val))

    await asyncio.to_thread(_save_settings_sync)

    # 更新后清空 public_api 的设置读取缓存，保证配置立即生效
    _clear_settings_cache()
    await asyncio.to_thread(insert_audit, "update", "settings", "", "更新网关策略")
    return {"message": "保存成功"}


# ========== 维护模式 ==========

@router.post("/maintenance", tags=["管理员"])
async def toggle_maintenance(request: Request):
    """切换维护模式"""
    await require_admin(request)

    current = is_maintenance_mode()
    # 热更新：先切换调度器，再切换维护标志
    set_maintenance_mode(not current)

    def _maintenance_sync():
        set_setting("maintenance_mode", "true" if not current else "false")
        insert_audit("maintenance", "system", "", f"维护模式: {'开启' if not current else '关闭'}")

    await asyncio.to_thread(_maintenance_sync)
    return {"maintenance_mode": not current, "message": f"维护模式已{'开启' if not current else '关闭'}"}


@router.get("/global-status", tags=["管理员"])
async def global_status(request: Request):
    """全局状态总览"""
    await require_admin(request)
    scheduler = get_scheduler()
    # get_global_status 内部含DB查询（活跃密钥数），与两条COUNT一起经线程池执行
    status = await asyncio.to_thread(scheduler.get_global_status)
    status["maintenance_mode"] = is_maintenance_mode()
    status["upstream_keys"] = (await asyncio.to_thread(fetch_one, "SELECT COUNT(*) as cnt FROM upstream_keys WHERE status='active'"))["cnt"]
    status["clients"] = (await asyncio.to_thread(fetch_one, "SELECT COUNT(*) as cnt FROM clients WHERE status='active'"))["cnt"]
    # v10.0: 熔断器状态
    try:
        from app.circuit_breaker import get_circuit_breaker
        cb = get_circuit_breaker()
        cb_all = cb.get_all_status()
        status["circuit_breakers"] = {
            "total": len(cb_all),
            "open": sum(1 for v in cb_all.values() if v.get("status") == "open"),
            "details": cb_all,
        }
    except Exception:
        status["circuit_breakers"] = {"total": 0, "open": 0, "details": {}}
    return status


# ========== v10.0 熔断器管理 ==========

@router.get("/circuit-breakers", tags=["管理员"])
async def get_circuit_breakers(request: Request):
    """获取所有熔断器状态"""
    await require_admin(request)
    from app.circuit_breaker import get_circuit_breaker
    cb = get_circuit_breaker()
    return cb.get_all_status()

@router.post("/circuit-breakers/reset", tags=["管理员"])
async def reset_circuit_breaker(request: Request, key: str = None):
    """重置熔断器（可指定key，不指定则重置全部）"""
    await require_admin(request)
    from app.circuit_breaker import get_circuit_breaker
    cb = get_circuit_breaker()
    cb.reset(key)
    return {"message": f"熔断器已重置: {key or 'all'}"}


# ========== 模型ID验证机制 ==========

@router.get("/validate-models", tags=["管理员"])
async def validate_models(request: Request):
    """
    模型ID自动化验证：对比本地模型目录与上游有效模型列表
    
    检查项：
    1. 本地目录中存在但上游不存在的模型（需移除）
    2. 上游存在但本地目录缺失的模型（需添加）
    3. /v1/models API 返回的模型与上游的一致性
    """
    await require_admin(request)
    
    from app.nim_models import NIM_MODEL_CATALOG
    from app.security import decrypt_upstream_key
    
    # 获取上游有效模型列表
    upstream_ids = set()
    upstream_models = {}
    try:
        active_keys = await asyncio.to_thread(
            fetch_all,
            "SELECT id, api_key_ciphertext FROM upstream_keys WHERE status = 'active' LIMIT 1"
        )
        if active_keys:
            master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
            if master_key:
                api_key = decrypt_upstream_key(active_keys[0]["api_key_ciphertext"], master_key)
                import httpx
                # 修复：原为同步 httpx.get，会阻塞事件循环；端点本身是async，改用AsyncClient+await
                # 上游地址从网关配置读取（默认NVIDIA NIM）
                _base_url = await asyncio.to_thread(get_setting, "upstream_base_url") or "https://integrate.api.nvidia.com/v1"
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        f"{_base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        upstream_ids.add(m["id"])
                        upstream_models[m["id"]] = m.get("owned_by", "")
    except Exception as e:
        return {"error": f"获取上游模型列表失败: {str(e)}", "upstream_count": 0}
    
    # 本地目录模型ID
    catalog_ids = set(NIM_MODEL_CATALOG.keys())
    
    # 对比
    in_catalog_not_upstream = sorted(catalog_ids - upstream_ids)
    in_upstream_not_catalog = sorted(upstream_ids - catalog_ids)
    
    # 检查 /v1/models API 返回的模型（需要从public_api缓存获取）
    # 由于API是异步的，这里直接用上游数据
    
    return {
        "upstream_count": len(upstream_ids),
        "catalog_count": len(catalog_ids),
        "missing_from_catalog": [
            {"id": mid, "owned_by": upstream_models.get(mid, "")}
            for mid in in_upstream_not_catalog
        ],
        "invalid_in_catalog": [
            {"id": mid, "display_name": NIM_MODEL_CATALOG[mid].display_name}
            for mid in in_catalog_not_upstream
        ],
        "is_synced": len(in_upstream_not_catalog) == 0 and len(in_catalog_not_upstream) == 0,
        "last_check": utcnow(),  # 统一 UTC Z 格式（原为按本机时间硬贴 +08:00 后缀）
    }


# ========== 商用行为识别 ==========

@router.get("/commercial-detection", tags=["管理员"])
async def commercial_detection(request: Request):
    """商用行为识别列表"""
    await require_admin(request)
    detector = get_detector()
    return await asyncio.to_thread(detector.get_all_detections)


@router.put("/commercial-detection/{client_id}", tags=["管理员"])
async def update_detection(client_id: str, request: Request):
    """更新商用标记"""
    await require_admin(request)
    body = await request.json()
    admin_confirmed = body.get("admin_confirmed", False)
    false_positive = body.get("false_positive", False)

    detector = get_detector()
    await asyncio.to_thread(detector.update_detection, client_id, admin_confirmed, false_positive)

    await asyncio.to_thread(insert_audit, "update", "commercial_detection", client_id,
                            f"商用标记: confirmed={admin_confirmed}, false_positive={false_positive}")
    return {"message": "更新成功"}


@router.post("/commercial-detection/{client_id}/block", tags=["管理员"])
async def block_commercial_client(client_id: str, request: Request):
    """封禁商用客户端（拉黑账户与网关密钥）"""
    await require_admin(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    reason = body.get("reason", "商用行为封禁")

    detector = get_detector()
    # block_client 内部多条同步DB写入（禁用客户/吊销密钥/更新检测记录/审计），整体经线程池执行
    await asyncio.to_thread(detector.block_client, client_id, reason)
    return {"message": "客户端已封禁", "client_id": client_id}


@router.post("/commercial-detection/{client_id}/unblock", tags=["管理员"])
async def unblock_commercial_client(client_id: str, request: Request):
    """解封客户端"""
    await require_admin(request)

    detector = get_detector()
    await asyncio.to_thread(detector.unblock_client, client_id)
    return {"message": "客户端已解封", "client_id": client_id}


# ========== 接口调试 ==========

@router.post("/debug/test", tags=["管理员"])
async def debug_test(req: DebugChatRequest, request: Request):
    """接口调试（直接使用指定密钥测试）
    支持两种模式：
    1. 直接传 api_key 字符串
    2. 传 "__use_key_id__:<key_id>" 由后端自动解密
    """
    await require_admin(request)

    import httpx
    base_url = await asyncio.to_thread(get_setting, "upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    chat_path = await asyncio.to_thread(get_setting, "chat_path") or "/chat/completions"
    url = f"{base_url}{chat_path}"

    # 处理 api_key：如果是 key_id 标记则自动解密
    api_key = req.api_key
    if api_key.startswith("__use_key_id__:"):
        key_id = api_key.split(":", 1)[1]
        row = await asyncio.to_thread(fetch_one, "SELECT api_key_ciphertext FROM upstream_keys WHERE id = %s", (key_id,))
        if not row:
            return {"status_code": 0, "error": "指定的密钥不存在"}
        master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
        if not master_key:
            return {"status_code": 0, "error": "主密钥未配置"}
        try:
            api_key = decrypt_upstream_key(row["api_key_ciphertext"], master_key)
        except Exception as e:
            return {"status_code": 0, "error": f"密钥解密失败: {e}"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    body = {"model": req.model, "messages": req.messages, "stream": req.stream}
    # 附加额外参数（temperature/max_tokens等）
    for k, v in req.model_dump().items():
        if k not in ("api_key", "model", "messages", "stream") and v is not None:
            body[k] = v

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            return {"status_code": resp.status_code, "data": data}
    except Exception as e:
        return {"status_code": 0, "error": str(e)}


# ========== 实时流量监控 ==========

@router.get("/realtime-traffic", tags=["管理员"])
async def realtime_traffic(request: Request):
    """实时流量监控"""
    await require_admin(request)
    now = time.time()
    # 最近5分钟每分钟请求数
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    traffic = await asyncio.to_thread(
        fetch_all,
        "SELECT date_trunc('minute', created_at::timestamptz) as minute, "
        "COUNT(*) as requests, "
        "SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as success "
        "FROM request_logs WHERE created_at >= %s "
        "GROUP BY minute ORDER BY minute",
        (five_min_ago,),
    )
    scheduler = get_scheduler()
    return {
        "traffic": traffic,
        "scheduler": await asyncio.to_thread(scheduler.get_global_status),
    }


# ========== 商用检测控制 ==========

@router.get("/commercial/settings", tags=["管理员"])
async def get_commercial_settings(request: Request):
    """获取商用检测设置"""
    await require_admin(request)
    # 修复：原 from app.commercial_detect import detector 模块内不存在该名，ImportError必500
    detector = get_detector()
    return {
        "detection_enabled": detector.detection_enabled,
        "confidence_threshold": detector.confidence_threshold,
        "whitelist_count": len(detector.whitelist),
        "whitelist": list(detector.whitelist),
    }

@router.post("/commercial/toggle", tags=["管理员"])
async def toggle_commercial_detection(request: Request, enabled: bool = True):
    """开关商用检测"""
    await require_admin(request)
    # 修复：原局部导入 detector 不存在（模块级单例获取函数为 get_detector），ImportError必500
    detector = get_detector()
    if enabled:
        detector.enable_detection()
    else:
        detector.disable_detection()
    await asyncio.to_thread(insert_audit, "update", "commercial_detection", "toggle", f"商用检测已{'启用' if enabled else '禁用'}")
    return {"message": f"商用检测已{'启用' if enabled else '禁用'}"}

@router.post("/commercial/threshold", tags=["管理员"])
async def set_commercial_threshold(request: Request, threshold: int = 70):
    """设置商用检测置信度阈值"""
    await require_admin(request)
    # 修复：原局部导入 detector 不存在，ImportError必500
    detector = get_detector()
    detector.set_confidence_threshold(max(0, min(100, threshold)))
    await asyncio.to_thread(insert_audit, "update", "commercial_detection", "threshold", f"阈值设为{detector.confidence_threshold}")
    return {"threshold": detector.confidence_threshold}

@router.post("/commercial/whitelist/{client_id}", tags=["管理员"])
async def add_commercial_whitelist(client_id: str, request: Request):
    """添加商用检测白名单"""
    await require_admin(request)
    # 修复：原局部导入 detector 不存在，ImportError必500
    detector = get_detector()
    detector.add_to_whitelist(client_id)
    await asyncio.to_thread(insert_audit, "update", "commercial_detection", "whitelist_add", f"白名单添加: {client_id}")
    return {"message": f"已添加白名单: {client_id}"}

@router.delete("/commercial/whitelist/{client_id}", tags=["管理员"])
async def remove_commercial_whitelist(client_id: str, request: Request):
    """移除商用检测白名单"""
    await require_admin(request)
    # 修复：原局部导入 detector 不存在，ImportError必500
    detector = get_detector()
    detector.remove_from_whitelist(client_id)
    await asyncio.to_thread(insert_audit, "update", "commercial_detection", "whitelist_remove", f"白名单移除: {client_id}")
    return {"message": f"已移除白名单: {client_id}"}


# ========== 慷慨型网关状态 ==========

@router.get("/generous/status", tags=["管理员"])
async def get_generous_gateway_status(request: Request):
    """获取慷慨型网关状态"""
    await require_admin(request)
    from app.generous_gateway import load_balancer
    return load_balancer.get_status()


# ========== NVIDIA NIM模型目录 ==========

@router.get("/nim/models", tags=["管理员"])
async def get_nim_models(request: Request, publisher: str = None, family: str = None):
    """获取NVIDIA NIM模型目录"""
    await require_admin(request)
    from app.nim_models import list_models
    models = list_models(publisher=publisher, family=family)
    return {
        "models": [
            {
                "model_id": m.model_id,
                "display_name": m.display_name,
                "publisher": m.publisher,
                "context_length": m.context_length,
                "max_output_tokens": m.max_output_tokens,
                "supports_streaming": m.supports_streaming,
                "supports_tools": m.supports_tools,
                "supports_images": m.supports_images,
                "model_family": m.model_family,
                "description": m.description,
                "tags": m.tags,
            }
            for m in models
        ],
        "total": len(models),
    }


# ========== 算法可视化 ==========

@router.get("/algorithms/realtime", tags=["管理员"])
async def get_algorithms_realtime(request: Request):
    """获取所有算法的实时状态(用于可视化面板)"""
    await require_admin(request)
    # 修复：原 from app.scheduler import surge_scheduler 模块内不存在该名（单例获取函数为get_scheduler），ImportError必500
    scheduler = get_scheduler()
    stats = scheduler.get_algorithm_stats()
    buckets = scheduler.get_bucket_stats()

    # Per-algorithm breakdown for visualization（get_algorithm_detail 内部可能查DB，经线程池执行）
    algorithm_visualization = {}
    for algo_id in range(1, 17):
        detail = await asyncio.to_thread(scheduler.get_algorithm_detail, algo_id)
        algorithm_visualization[f"algorithm_{algo_id}"] = detail

    return {
        "timestamp": time.time(),
        "global_status": await asyncio.to_thread(scheduler.get_global_status),
        "algorithm_stats": stats,
        "algorithm_details": algorithm_visualization,
        "bucket_count": len(buckets),
        "buckets": buckets,
    }


# ========== 时间段对比统计 ==========

@router.get("/dashboard/comparison", tags=["管理员"])
async def get_dashboard_comparison(
    request: Request,
    period_a_start: str = None,  # ISO timestamp
    period_a_end: str = None,
    period_b_start: str = None,
    period_b_end: str = None,
):
    """时间段对比统计"""
    await require_admin(request)

    # Default: today vs yesterday
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    if not period_a_start:
        period_a_start = today_start.isoformat()
        period_a_end = now.isoformat()
    if not period_b_start:
        period_b_start = yesterday_start.isoformat()
        period_b_end = today_start.isoformat()

    # Query both periods
    def query_period(start, end):
        row = fetch_one(
            "SELECT COUNT(*) as total_requests, "
            "SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) as success_count, "
            "SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as rate_limited, "
            "SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as server_errors, "
            "COALESCE(SUM(total_tokens), 0) as total_tokens, "
            "COALESCE(SUM(prompt_tokens), 0) as prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) as completion_tokens, "
            "COALESCE(AVG(latency_ms), 0) as avg_latency_ms "
            "FROM request_logs WHERE created_at >= %s AND created_at < %s",
            (start, end),
        )
        return row

    period_a = await asyncio.to_thread(query_period, period_a_start, period_a_end)
    period_b = await asyncio.to_thread(query_period, period_b_start, period_b_end)

    def to_dict(row, label):
        if not row:
            return {"label": label, "total_requests": 0, "success_count": 0}
        return {
            "label": label,
            "total_requests": int(row["total_requests"] or 0),
            "success_count": int(row["success_count"] or 0),
            "rate_limited": int(row["rate_limited"] or 0),
            "server_errors": int(row["server_errors"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "avg_latency_ms": int(round(row["avg_latency_ms"] or 0)),
        }

    return {
        "period_a": to_dict(period_a, "period_a"),
        "period_b": to_dict(period_b, "period_b"),
        "comparison": {
            "requests_change": _calc_change(period_a, period_b, "total_requests"),
            "tokens_change": _calc_change(period_a, period_b, "total_tokens"),
            "latency_change": _calc_change(period_a, period_b, "avg_latency_ms"),
            "errors_change": _calc_change(period_a, period_b, "server_errors"),
        },
    }


def _calc_change(a, b, field):
    """计算变化百分比"""
    if not a or not b:
        return 0
    va = a[field] or 0
    vb = b[field] or 0
    if vb == 0:
        return 100 if va > 0 else 0
    return round((va - vb) / vb * 100, 1)


# ========== 错误码定义已迁移至 app.errors_v2 ==========


@router.get("/error-codes", tags=["管理员"])
async def get_error_code_definitions(request: Request):
    """获取所有错误码定义和说明"""
    await require_admin(request)
    from app.errors_v2 import ERROR_CODE_DEFINITIONS
    return {"error_codes": list(ERROR_CODE_DEFINITIONS.values())}


@router.post("/logs/cleanup", tags=["管理员"])
async def cleanup_logs(request: Request, keep_days: int = Query(3), keep_error_days: int = Query(90)):
    """清理请求日志：成功日志短期保留，错误日志长期保存"""
    await require_admin(request)
    result = await asyncio.to_thread(cleanup_success_logs, keep_days=keep_days, keep_error_days=keep_error_days)
    await asyncio.to_thread(insert_audit, "cleanup", "request_logs", "", f"日志清理: 删除{result['success_deleted']}条成功日志(>{keep_days}天), {result['error_deleted']}条错误日志(>{keep_error_days}天)")
    return {"message": "清理完成", **result}


@router.get("/stats/latency-distribution", tags=["统计"])
async def get_latency_distribution(request: Request, model: str = Query(None)):
    """耗时分布统计：P50/P90/P99指标，可按模型分组"""
    await require_admin(request)
    model_filter = "WHERE model = %s" if model else ""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    params = (cutoff, model) if model else (cutoff,)
    
    # 各模型P50/P90/P99
    query = f"""
        SELECT model,
               COUNT(*) as request_count,
               AVG(latency_ms) as avg_latency,
               ROUND(AVG(CASE WHEN row_num <= cnt * 0.50 THEN latency_ms END), 1) as p50,
               ROUND(AVG(CASE WHEN row_num <= cnt * 0.90 THEN latency_ms END), 1) as p90,
               ROUND(AVG(CASE WHEN row_num <= cnt * 0.99 THEN latency_ms END), 1) as p99,
               MAX(latency_ms) as max_latency
        FROM (
            SELECT model, latency_ms,
                   ROW_NUMBER() OVER (PARTITION BY model ORDER BY latency_ms) as row_num,
                   COUNT(*) OVER (PARTITION BY model) as cnt
            FROM request_logs
            WHERE created_at >= %s
            AND latency_ms > 0
            { 'AND model = %s' if model else '' }
        )
        GROUP BY model
        ORDER BY request_count DESC
        LIMIT 20
    """
    try:
        rows = await asyncio.to_thread(fetch_all, query, params)
        return {
            "data": [dict(r) for r in rows] if rows else [],
            "total_models": len(rows) if rows else 0,
        }
    except Exception as e:
        return {"error": str(e), "data": []}


@router.get("/stats/error-analysis", tags=["统计"])
async def get_error_analysis(request: Request, period: str = Query("day", pattern="^(day|week|month)$")):
    """错误类型分析：按429/403/5xx/timeout分组统计"""
    await require_admin(request)
    days = {"day": 1, "week": 7, "month": 30}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days.get(period, 1))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    hour_format = '%Y-%m-%dT%H:00:00'
    
    # 按状态码分类统计
    rows = await asyncio.to_thread(fetch_all, f"""
        SELECT 
            CASE 
                WHEN status_code = 429 THEN '429限流'
                WHEN status_code = 403 THEN '403权限拒绝'
                WHEN status_code = 502 THEN '502网关错误'
                WHEN status_code = 503 THEN '503服务不可用'
                WHEN status_code >= 500 THEN '5xx服务端错误'
                WHEN status_code >= 400 THEN '4xx客户端错误'
                ELSE CAST(status_code AS TEXT)
            END as error_category,
            COUNT(*) as count,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            ROUND(AVG(latency_ms), 1) as avg_latency
        FROM request_logs 
        WHERE created_at >= %s
        AND status_code >= 400
        GROUP BY error_category
        ORDER BY count DESC
    """, (cutoff,))
    
    # 时间趋势（按小时）
    trend_rows = await asyncio.to_thread(fetch_all, f"""
        SELECT 
            to_char(created_at::timestamptz, %s) as time_bucket,
            SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as count_429,
            SUM(CASE WHEN status_code = 403 THEN 1 ELSE 0 END) as count_403,
            SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as count_5xx,
            COUNT(*) as total_errors
        FROM request_logs 
        WHERE created_at >= %s
        AND status_code >= 400
        GROUP BY time_bucket
        ORDER BY time_bucket
        LIMIT 168
    """, (hour_format, cutoff))
    
    total = sum((r["count"] for r in rows), 0) if rows else 0
    return {
        "total_errors": total,
        "data": [dict(r) for r in rows] if rows else [],
        "trend": [dict(r) for r in trend_rows] if trend_rows else [],
        "period": period,
    }


@router.get("/stats/request-trend", tags=["统计"])
async def get_request_trend(request: Request, period: str = Query("day", pattern="^(day|week|month)$")):
    """请求量趋势分析，支持日/周/月视图"""
    await require_admin(request)
    
    group_format = {
        "day": "%Y-%m-%dT%H:00:00",
        "week": "%Y-%m-%d",
        "month": "%Y-%m-%d",
    }
    days_map = {"day": 1, "week": 7, "month": 30}
    fmt = group_format.get(period, "%Y-%m-%d")
    d = days_map.get(period, 1)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    rows = await asyncio.to_thread(fetch_all, f"""
        SELECT 
            to_char(created_at::timestamptz, %s) as time_bucket,
            COUNT(*) as total_requests,
            SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as error_count,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) as completion_tokens
        FROM request_logs 
        WHERE created_at >= %s
        GROUP BY time_bucket
        ORDER BY time_bucket
    """, (fmt, cutoff))
    
    return {
        "period": period,
        "data": [dict(r) for r in rows] if rows else [],
        "total": sum((r["total_requests"] for r in rows), 0) if rows else 0,
    }


@router.get("/models/status", tags=["监控"])
async def get_models_status(request: Request):
    """模型健康状态与性能指标监控（实时聚合桶数据+请求日志）"""
    await require_admin(request)
    from collections import defaultdict
    import time

    now = time.time()
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    # 1. 从调度器获取桶级数据
    scheduler = get_scheduler()
    all_buckets = scheduler.get_bucket_stats()
    buckets_by_model = defaultdict(list)
    for b in all_buckets:
        buckets_by_model[b["model"]].append(b)

    # 2. 从request_logs获取统计
    model_log_stats = {}
    try:
        rows = await asyncio.to_thread(
            fetch_all,
            "SELECT model, COUNT(*) as total_requests, "
            "SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) as success_count, "
            "SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) as count_429, "
            "SUM(CASE WHEN status_code = 403 THEN 1 ELSE 0 END) as count_403, "
            "SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as count_5xx, "
            "COALESCE(AVG(CASE WHEN latency_ms > 0 THEN latency_ms END), 0) as avg_latency, "
            "COALESCE(SUM(total_tokens), 0) as total_tokens, "
            "COUNT(DISTINCT client_id) as active_users "
            "FROM request_logs WHERE created_at >= %s AND model != '' AND model IS NOT NULL "
            "GROUP BY model ORDER BY total_requests DESC",
            (one_hour_ago,),
        )
        for r in rows:
            model_log_stats[r["model"]] = dict(r)
    except Exception as e:
        logger.warning(f"查询模型日志统计失败: {e}")

    # 今日统计
    today_model_stats = {}
    try:
        today_rows = await asyncio.to_thread(
            fetch_all,
            "SELECT model, COUNT(*) as today_requests, "
            "COALESCE(SUM(total_tokens), 0) as today_tokens "
            "FROM request_logs WHERE created_at >= %s AND model != '' AND model IS NOT NULL "
            "GROUP BY model", (today_start,),
        )
        for r in today_rows:
            today_model_stats[r["model"]] = dict(r)
    except Exception as e:
        logger.warning(f"查询今日模型统计失败: {e}")

    # 聚合 — 只显示有真实数据的模型（调度器桶数据 或 实际请求日志）
    all_models = set(buckets_by_model.keys()) | set(model_log_stats.keys()) | set(today_model_stats.keys())
    result = []
    for model in sorted(all_models):
        buckets = buckets_by_model.get(model, [])
        log_stat = model_log_stats.get(model, {})
        today_stat = today_model_stats.get(model, {})

        total_buckets = len(buckets)
        cooled_buckets = sum(1 for b in buckets if b["cooldown_remaining"] > 0)
        healthy_buckets = sum(1 for b in buckets if b["health_score"] >= 60)
        bucket_rates = [b["success_rate"] for b in buckets if b["total_requests"] > 0]
        avg_sr = sum(bucket_rates) / len(bucket_rates) if bucket_rates else 100.0
        avg_health = sum(b["health_score"] for b in buckets) / total_buckets if total_buckets else 100.0

        total_req = log_stat.get("total_requests", 0) or 0
        success_count = log_stat.get("success_count", 0) or 0
        logs_sr = (success_count / total_req * 100) if total_req > 0 else 100.0
        health = int(avg_health * 0.7 + logs_sr * 0.3)
        health = max(0, min(100, health))

        # 状态判定：全部冷却→异常，大部分冷却→警告，健康→正常
        if total_buckets > 0 and cooled_buckets == total_buckets:
            status, label = "abnormal", "异常"
        elif total_buckets > 0 and cooled_buckets > total_buckets * 0.5:
            status, label = "warning", "警告"
        elif health >= 80:
            status, label = "normal", "正常"
        elif health >= 50:
            status, label = "warning", "警告"
        else:
            status, label = "abnormal", "异常"

        avg_rt_val = sum(b["avg_rt"] for b in buckets if b["avg_rt"] > 0)
        avg_rt_cnt = sum(1 for b in buckets if b["avg_rt"] > 0)
        latency = (avg_rt_val / avg_rt_cnt * 1000) if avg_rt_cnt > 0 else float(log_stat.get("avg_latency", 0) or 0)

        # v10.0: 添加 display_name（友好名称）
        model_display_name = model
        try:
            from app.nim_models import NIM_MODEL_CATALOG
            if model in NIM_MODEL_CATALOG:
                model_display_name = NIM_MODEL_CATALOG[model].display_name
        except ImportError:
            pass

        result.append({
            "model": model,
            "display_name": model_display_name,
            "status": status, "status_label": label,
            "health_score": health, "total_buckets": total_buckets,
            "healthy_buckets": healthy_buckets, "cooled_buckets": cooled_buckets,
            "avg_success_rate": round(avg_sr, 1),
            "recent_success_rate": round(logs_sr, 1),
            "success_rate": round(logs_sr, 1),  # 前端兼容字段
            "avg_latency_ms": round(latency, 1),
            "total_requests_1h": total_req,
            "success_count_1h": success_count,
            "count_429_1h": log_stat.get("count_429", 0) or 0,
            "count_5xx_1h": log_stat.get("count_5xx", 0) or 0,
            "active_users_1h": log_stat.get("active_users", 0) or 0,
            "today_requests": today_stat.get("today_requests", 0) or 0,
            "today_tokens": int(today_stat.get("today_tokens", 0) or 0),
            "total_tokens_1h": int(log_stat.get("total_tokens", 0) or 0),
        })

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
    }


# ========== 错误追踪 ==========

@router.get("/error-stats", tags=["管理员"])
async def get_error_stats(request: Request):
    """获取错误统计仪表盘"""
    await require_admin(request)
    from app.error_tracker import get_error_tracker
    return get_error_tracker().get_stats()


@router.get("/active-errors", tags=["管理员"])
async def get_active_errors(request: Request, max_age: int = 3600):
    """获取活跃错误列表"""
    await require_admin(request)
    from app.error_tracker import get_error_tracker
    return {"active_errors": get_error_tracker().get_active_errors(max_age)}


# ========== 系统监控 ==========


@router.get("/system/concurrency", tags=["v10.0系统"])
async def system_concurrency(request: Request):
    """并发控制器统计 - 使用调度器本地数据"""
    await require_admin(request)
    scheduler = get_scheduler()
    summary = scheduler.get_all_clients_concurrency_summary()
    # 补充详细并发数据
    client_details = []
    for cid, metrics in scheduler._client_metrics.items():
        stats = scheduler.get_client_concurrency_stats(cid)
        client_details.append({
            "client_id": cid,
            "current": stats["current"],
            "limit": stats["limit"],
            "peak": stats["peak"],
            "rejected": stats["rejected"],
            "limit_label": stats["limit_label"],
        })
    return {
        **summary,
        "client_details": client_details,
    }


@router.get("/system/ip-monitor", tags=["v10.0系统"])
async def system_ip_monitor(request: Request):
    """IP 监测统计 - 使用本地IP监控模块 + request_logs 实时数据"""
    await require_admin(request)
    from app.ip_monitor import get_ip_monitor
    ip_monitor = get_ip_monitor()
    stats = await asyncio.to_thread(ip_monitor.get_stats)
    stats["anomalies"] = await asyncio.to_thread(ip_monitor.get_anomalies, 30)

    # 补充 request_logs 实时数据
    try:
        total_unique_ips = await asyncio.to_thread(fetch_one, "SELECT COUNT(DISTINCT client_ip) as cnt FROM request_logs WHERE client_ip != ''")
        if total_unique_ips:
            stats["total_unique_ips_from_logs"] = total_unique_ips["cnt"]
    except Exception:
        stats["total_unique_ips_from_logs"] = 0

    return stats


@router.get("/system/ip-monitor/blocked", tags=["v10.0系统"])
async def system_ip_blocked(request: Request):
    """被封禁 IP 列表"""
    await require_admin(request)
    from app.ip_monitor import get_ip_monitor
    return {"blocked": await asyncio.to_thread(get_ip_monitor().get_blocked_ips)}


@router.get("/system/ip-monitor/anomalies", tags=["v10.0系统"])
async def system_ip_anomalies(request: Request, min_score: int = 30):
    """异常 IP 列表"""
    await require_admin(request)
    from app.ip_monitor import get_ip_monitor
    return {"anomalies": await asyncio.to_thread(get_ip_monitor().get_anomalies, min_score)}


@router.post("/system/ip-monitor/unblock", tags=["v10.0系统"])
async def system_ip_unblock(request: Request):
    """解封 IP"""
    await require_admin(request)
    body = await request.json()
    ip = body.get("ip", "")
    if not ip:
        raise HTTPException(status_code=400, detail={"message": "缺少ip参数", "type": "validation_error", "code": "missing_ip"})
    from app.ip_monitor import get_ip_monitor
    await asyncio.to_thread(get_ip_monitor().unblock_ip, ip)
    return {"message": f"IP {ip} 已解封"}


# ========== 模型列表同步 ==========

@router.post("/sync-models", tags=["管理员"])
async def sync_upstream_models(request: Request):
    """
    从 NVIDIA 上游实时同步可用模型列表

    调用上游 /v1/models 接口，获取完整模型列表并更新缓存。
    返回同步结果。
    """
    await require_admin(request)

    from app.security import decrypt_upstream_key
    import httpx

    master_key = await asyncio.to_thread(get_setting, "upstream_master_key")
    if not master_key:
        return {"error": "未配置上游主密钥", "count": 0, "models": []}

    active_key = await asyncio.to_thread(fetch_one,
        "SELECT id, api_key_ciphertext FROM upstream_keys WHERE status = 'active' LIMIT 1"
    )
    if not active_key:
        return {"error": "无活跃上游密钥", "count": 0, "models": []}

    try:
        api_key = decrypt_upstream_key(active_key["api_key_ciphertext"], master_key)
    except Exception as e:
        return {"error": f"解密密钥失败: {e}", "count": 0, "models": []}

    base_url = await asyncio.to_thread(get_setting, "upstream_base_url") or "https://integrate.api.nvidia.com/v1"
    models_path = await asyncio.to_thread(get_setting, "models_path") or "/models"
    url = f"{base_url}{models_path}"

    try:
        from app.public_api import _models_cache, get_model_list
        # 清除缓存，强制重新获取
        _models_cache["expires"] = 0
        models = await get_model_list()
        return {"count": len(models), "models": [m.get("id","") for m in models[:50]],
                "note": "上游实时全量，已应用「模型管理」页的隐藏/手动补录"}
    except Exception as e:
        logger.error(f"上游模型同步失败: {e}")
        return {"error": str(e), "count": 0, "models": []}


# ========== 客户端特殊状态查询 ==========

@router.get("/clients/{client_id}/special-status", tags=["管理员"])
async def get_client_special_status(client_id: str, request: Request):
    """获取客户端的特殊状态（并发限制/标签等），用于管理控制台显示"""
    await require_admin(request)
    from app.scheduler import get_scheduler
    status = get_scheduler().get_client_special_status(client_id)
    return status


# ========== 客户端实时并发统计 ==========

@router.get("/clients/{client_id}/concurrency-stats", tags=["管理员"])
async def get_client_concurrency_stats(client_id: str, request: Request):
    """
    获取客户端实时并发统计（当前并发数/限制/峰值/标签）
    用于用户控制台统计面板和概览页面展示
    """
    await require_admin(request)
    from app.scheduler import get_scheduler
    scheduler = get_scheduler()
    stats = scheduler.get_client_concurrency_stats(client_id)
    special = scheduler.get_client_special_status(client_id)
    return {**stats, **special}


@router.get("/concurrency/summary", tags=["管理员"])
async def get_concurrency_summary(request: Request):
    """获取全部客户端的并发统计汇总（概览页面用）"""
    await require_admin(request)
    from app.scheduler import get_scheduler
    return get_scheduler().get_all_clients_concurrency_summary()

