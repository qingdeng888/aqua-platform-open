"""
网关服务主入口 - FastAPI应用 v10.0

完整集成：
- 17算法互锁体系调度器（SurgeScheduler v10.0）
- OpenAI兼容公开API（/v1/chat/completions, /v1/models）
- 管理后台API（/gw/admin/*）
- 多协议转换（Anthropic/Gemini/Ollama）
- 商用行为识别
- 后台周期任务（算法3/10/12/14/16 + IP监控 + 商用检测）
- 维护模式热更新
"""
import asyncio
import os
import secrets
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("acu.gateway")

# 从项目根目录加载 .env 文件
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(str(_env_path))
    logger.info(f"[网关] 已加载环境变量: {_env_path}")

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.database import init_db, seed_defaults, utcnow_minus, warmup_pool, POOL_MAXCONN  # utcnow_minus 为契约函数（由 database.py 提供）
from app.middleware import setup_middleware, is_maintenance_mode, start_log_worker, stop_log_worker
from app.scheduler import get_scheduler
from app.public_api import router as public_router
from app.admin_api import router as admin_router, require_admin
from app.admin_panel import create_admin

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ========== 后台任务 ==========

_background_task = None


async def run_background_tasks():
    """后台周期任务：算法3/10/12/14 + IP监控 + 商用检测"""
    scheduler = get_scheduler()
    from app.commercial_detect import get_detector
    from app.ip_monitor import get_ip_monitor

    logger.info("[网关] 后台任务已启动（算法3阈值/算法10健康度/算法12预判/算法14自愈/算法16脱壳 + IP监控 + 商用检测）")

    _last_monitor_time = 0.0

    while True:
        try:
            await scheduler.run_background_tasks()
        except Exception as e:
            logger.error(f"[网关] 后台任务异常: {e}")

        # 每5分钟执行IP监控统计更新和商用检测分析
        now = time.time()
        if now - _last_monitor_time >= 300:
            _last_monitor_time = now
            try:
                # 从request_logs同步最近5分钟的数据到IP监控
                # 查询+逐条落库均为同步DB，整体打包进线程池执行，避免阻塞事件循环
                ip_monitor = get_ip_monitor()
                from app.database import fetch_all

                def _sync_ip_monitor():
                    recent_ips = fetch_all(
                        "SELECT DISTINCT client_ip, client_id, user_agent FROM request_logs "
                        "WHERE created_at >= %s AND client_ip != '' AND client_id != ''",
                        (utcnow_minus(300),),  # v10.1修复：最近5分钟（此前 utcnow() 误拉全部历史）
                    )
                    for row in recent_ips:
                        ip_monitor.record_request(
                            row.get("client_ip", ""),
                            row.get("client_id", ""),
                            row.get("user_agent", ""),
                        )

                await asyncio.to_thread(_sync_ip_monitor)
                logger.info(f"[网关] IP监控统计已更新")
            except Exception as e:
                logger.error(f"[网关] IP监控更新失败: {e}")

            try:
                # 运行商用检测周期分析（内部含同步DB读写，经线程池执行）
                detector = get_detector()
                results = await asyncio.to_thread(detector.run_periodic_analysis)
                if results:
                    logger.info(f"[网关] 商用检测周期分析完成: {len(results)} 个客户端")
            except Exception as e:
                logger.error(f"[网关] 商用检测周期分析失败: {e}")

        # 每30秒执行一次（各算法内部有独立的周期控制）
        await asyncio.sleep(30)


# ========== 应用生命周期 ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    global _background_task

    # 启动：初始化数据库
    init_db()
    seed_defaults()
    logger.info("[网关] 数据库初始化完成")

    # 连接池预热：取放3条连接摊平首请求建连开销（maxconn 可经 GW_DB_POOL_SIZE 配置）
    try:
        warmup_pool(3)
    except Exception as e:
        logger.warning(f"[网关] 连接池预热失败(不影响启动): {e}")

    # 初始化调度器HTTP连接池
    scheduler = get_scheduler()
    await scheduler._ensure_pools()
    logger.info("[网关] HTTP连接池已创建")

    # 预热设置缓存：预填 public_api 热路径的关键配置key，避免首个请求缓存miss查库
    try:
        from app.public_api import get_setting_cached
        for _key in ("upstream_base_url", "chat_path", "models_path"):
            await get_setting_cached(_key)
        logger.info("[网关] 设置缓存已预热(upstream_base_url/chat_path/models_path, TTL 60s)")
    except Exception as e:
        logger.warning(f"[网关] 设置缓存预热失败(不影响启动): {e}")

    # 启动摘要：连接池规格 + 关键异步能力一览
    logger.info(
        f"[网关] 启动完成: DB池 maxconn={POOL_MAXCONN}(GW_DB_POOL_SIZE可调) "
        f"HTTP池 limits=100/keepalive20/60s http2=off | 设置缓存TTL=60s | "
        f"日志写入/认证查询均已 to_thread 化"
    )

    # 启动后台任务
    _background_task = asyncio.create_task(run_background_tasks())

    # === v10.0 平台适配器初始化 ===
    try:
        from app.platforms import register_all_adapters
        register_all_adapters()
        logger.info("[网关] v10.0 平台适配器已注册 (nvidia, openai)")
    except Exception as e:
        logger.info(f"[网关] 平台适配器注册失败(可降级运行): {e}")

    # === v10.0 多级缓存初始化 ===
    try:
        from app.cache.multilevel import get_cache
        cache = get_cache()
        logger.info(f"[网关] v10.0 多级缓存已初始化 (L1 maxsize={cache.l1._maxsize}, L2 ttl={cache.l2._default_ttl}s)")
    except Exception as e:
        logger.info(f"[网关] 多级缓存初始化失败(可降级运行): {e}")

    # === v10.0 健康探测预热 ===
    try:
        from app.routers.health import get_health_probe
        probe = get_health_probe()
        logger.info("[网关] v10.0 运行时健康追踪已初始化")
    except Exception as e:
        logger.info(f"[网关] 健康追踪初始化失败(可降级运行): {e}")

    # Initialize async database connection pool
    try:
        from app.database import async_get_pool
        await async_get_pool()
        logger.info("[网关] 异步数据库连接池已创建")
    except Exception as e:
        logger.info(f"[网关] 异步数据库连接池创建失败(可降级运行): {e}")

    # Start batch log worker
    start_log_worker()
    logger.info("[网关] 批量日志写入器已启动")

    yield

    # 关闭：清理资源
    if _background_task:
        _background_task.cancel()

    # Stop batch log worker
    await stop_log_worker()
    logger.info("[网关] 批量日志写入器已停止")

    # Close async database pool
    try:
        from app.db_async_pool import close_async_pool
        await close_async_pool()
        logger.info("[网关] 异步数据库连接池已关闭")
    except Exception:
        pass

    # === v10.0 关闭平台适配器连接池 ===
    try:
        from app.platforms.base import PlatformAdapterRegistry
        await PlatformAdapterRegistry.close_all()
        logger.info("[网关] v10.0 平台适配器连接池已关闭")
    except Exception:
        pass

    if scheduler._http_pool and not scheduler._http_pool.is_closed:
        await scheduler._http_pool.aclose()
    if scheduler._stream_pool and not scheduler._stream_pool.is_closed:
        await scheduler._stream_pool.aclose()
    logger.info("[网关] 服务关闭")


# ========== FastAPI 应用 ==========

app = FastAPI(
    title="AQUA AI Gateway",
    version="11.0.0",
    description="多平台AI API网关 | 17算法互锁调度 | 平台适配器 | 智能路由 | 协议转换 | 运行时健康追踪",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# 注册中间件
setup_middleware(app)

# v10.1修复：SQLAdmin 登录依赖 session，必须注册 SessionMiddleware（否则登录必 AssertionError）
_admin_session_secret = os.environ.get("ADMIN_SESSION_SECRET")
if not _admin_session_secret:
    _admin_session_secret = secrets.token_hex(32)
    logger.warning("[网关] 未设置 ADMIN_SESSION_SECRET，已临时生成会话密钥（重启后所有面板会话失效），建议配置该环境变量")
app.add_middleware(SessionMiddleware, secret_key=_admin_session_secret)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/gw/static", StaticFiles(directory=str(STATIC_DIR)), name="gw_static")

# 注册API路由
app.include_router(public_router)
app.include_router(admin_router)

# 挂载 SQLAdmin 数据库管理面板
admin_panel = create_admin(app)


# ========== OpenAI兼容错误处理器 ==========

@app.exception_handler(HTTPException)
async def openai_error_handler(request: Request, exc: HTTPException):
    """将HTTPException格式化为OpenAI标准错误格式"""
    path = request.url.path
    is_openai_route = path.startswith("/v1/") or path.startswith("/api/v1/")

    # v10.0: 使用统一的GatewayError系统
    if is_openai_route:
        error_detail = exc.detail
        if isinstance(error_detail, dict):
            message = error_detail.get("message", str(error_detail))
            error_type = error_detail.get("type", "invalid_request_error")
            code = error_detail.get("code", "unknown")
        else:
            message = str(error_detail)
            error_type = "invalid_request_error"
            code = "unknown"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": message,
                    "type": error_type,
                    "code": code,
                    "param": None,
                }
            },
        )

    # 管理后台等路由保持原有格式
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})

# v10.0: 捕获 GatewayError 并转换为 OpenAI 标准格式
from app.errors import GatewayError

@app.exception_handler(GatewayError)
async def gateway_error_handler(request: Request, exc: GatewayError):
    """将GatewayError转换为OpenAI标准错误格式"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_openai_error(),
        headers={"X-Error-Code": exc.code, "X-Error-Provider": exc.provider} if exc.provider else None,
    )


# ========== 基础路由 ==========

@app.get("/healthz", tags=["公共"])
async def healthz(request: Request):
    """健康检查（公开响应仅暴露基础状态；运营数据需 ?verbose=1 且带管理员凭据）"""
    scheduler = get_scheduler()
    # get_global_status 内部含DB查询（活跃密钥数），经线程池执行避免高频健康探测阻塞事件循环
    status = await asyncio.to_thread(scheduler.get_global_status)

    # v10.1修复：healthy_keys/resources/circuit_breakers 等运营数据仅管理员可见，防匿名探测
    verbose = request.query_params.get("verbose", "") in ("1", "true", "yes")
    if verbose:
        try:
            await require_admin(request)
        except HTTPException:
            verbose = False  # 未授权（401/403）时忽略 verbose，回落公开响应

    result = {
        "status": "ok",
        "version": "10.0.0",
        "maintenance_mode": is_maintenance_mode(),
        "degraded_mode": status["degraded_mode"],
    }
    if verbose:
        resource = scheduler.get_resource_status()  # New method
        # v10.0: 获取熔断器状态（v10.1修复：try前初始化，替代 'cb_status' in dir() hack）
        cb_status = {}
        open_circuits = {}
        try:
            from app.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker()
            cb_status = cb.get_all_status()
            open_circuits = {k: v for k, v in cb_status.items() if v.get("status") == "open"}
        except Exception:
            pass
        result.update({
            "healthy_keys": status["healthy_key_count"],
            "resources": resource,
            "circuit_breakers": {
                "total": len(cb_status),
                "open": len(open_circuits),
                "open_details": open_circuits if open_circuits else {},
            },
        })
    return result


@app.get("/robots.txt", tags=["公共"])
async def robots_txt():
    """搜索引擎爬虫规则"""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /gw/admin/\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/favicon.ico", tags=["公共"])
async def favicon():
    """返回统一favicon图标"""
    ico_path = STATIC_DIR / "favicon.ico"
    if ico_path.exists():
        return FileResponse(str(ico_path), media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse, tags=["公共"])
async def index_page():
    """服务运行页"""
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html><head><title>AQUA Gateway v10.0</title></head>
    <body style="background:#1a1a2e;color:#e0e0e0;display:flex;justify-content:center;
                 align-items:center;height:100vh;margin:0;font-family:sans-serif;">
        <div style="text-align:center">
            <h1>AQUA AI Gateway v10.0</h1>
            <p>多平台智能路由网关 | 服务运行中</p>
            <p style="color:#888">17算法互锁调度 | 平台适配器 | 智能路由 | 协议转换 | 运行时健康追踪 | 多级缓存</p>
        </div>
    </body></html>
    """)


@app.get("/admin", response_class=HTMLResponse, tags=["管理后台"])
async def admin_login_page():
    """管理员登录页"""
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html><head><title>管理员登录 - AQUA</title>
    </head>
    <body style="background:#1a1a2e;color:#e0e0e0;font-family:sans-serif;
                 display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
        <div style="text-align:center;background:#16213e;padding:40px;border-radius:8px;">
            <h1 style="color:#00d4ff;">AQUA 管理后台</h1>
            <p>v10.0 网关控制系统</p>
            <form id="loginForm" style="margin-top:20px;">
                <input type="password" id="password" placeholder="管理员密码"
                    style="padding:10px;width:250px;background:#0f3460;color:#fff;border:1px solid #00d4ff;border-radius:4px;">
                <br><br>
                <button type="submit" style="padding:10px 30px;background:#00d4ff;color:#1a1a2e;
                    border:none;border-radius:4px;cursor:pointer;font-weight:bold;">登录</button>
            </form>
            <p id="msg" style="color:#ff6b6b;margin-top:10px;"></p>
        </div>
        <script>
        document.getElementById('loginForm').onsubmit = async (e) => {{
            e.preventDefault();
            const pwd = document.getElementById('password').value;
            if (!pwd) {{ document.getElementById('msg').textContent = '请输入密码'; return; }}
            try {{
                const resp = await fetch('/gw/admin/login', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{password: pwd}})
                }});
                const data = await resp.json();
                if (resp.ok) {{
                    window.location.href = '/admin/console';
                }} else {{
                    var errMsg = data.detail && typeof data.detail === 'object' ? data.detail.message || '登录失败' : data.detail || data.message || '登录失败';
                    document.getElementById('msg').textContent = errMsg;
                }}
            }} catch(err) {{
                document.getElementById('msg').textContent = '网络错误，请检查连接';
            }}
        }};
        </script>
    </body></html>
    """)


@app.get("/admin/console", response_class=HTMLResponse, tags=["管理后台"])
async def admin_console_page():
    """管理控制台主页 - v10.0 完整12页面"""
    console_path = STATIC_DIR / "console.html"
    if console_path.exists():
        return HTMLResponse(content=console_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>控制台文件不存在</h1>", status_code=500)


# ========== 启动配置 ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
