"""
用户平台服务主入口 - FastAPI应用

v10.0 架构：
- main.py: 应用入口、中间件、基础路由
- routes/auth.py: 认证路由
- routes/console.py: 控制台路由
- routes/chat.py: AI对话路由
- routes/public.py: 公开页面路由
"""
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("acu.platform")

# 从项目根目录加载 .env 文件
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(str(_env_path))
    logger.info(f"[用户平台] 已加载环境变量: {_env_path}")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.database import init_db, seed_defaults
from app.admin_panel import create_admin


# ========== 应用生命周期 ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    init_db()
    seed_defaults()
    logger.info("[用户平台] 数据库初始化完成")

    # v10.0: 预热HTTP连接池（减少冷启动502）
    try:
        from app.routes.chat import _get_http_pool
        await _get_http_pool()
        logger.info("[用户平台] v10.0 HTTP连接池已预热")
    except Exception as e:
        logger.warning(f"[用户平台] HTTP连接池预热失败(可降级): {e}")

    yield

    # v10.0: 关闭HTTP连接池
    try:
        from app.routes.chat import _close_http_pool
        await _close_http_pool()
        logger.info("[用户平台] HTTP连接池已关闭")
    except Exception:
        pass
    logger.info("[用户平台] 服务关闭")


# ========== FastAPI 应用 ==========

app = FastAPI(
    title="AQUA Platform",
    version="11.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Session 中间件：SQLAdmin 管理面板（/platform/dbadmin）的 request.session 依赖
_admin_session_secret = os.environ.get("ADMIN_SESSION_SECRET", "")
if not _admin_session_secret:
    import secrets as _secrets
    _admin_session_secret = _secrets.token_hex(32)
    logger.warning(
        "[用户平台] ADMIN_SESSION_SECRET 未设置，SQLAdmin 会话密钥使用临时随机值（重启后已登录会话全部失效）"
    )
app.add_middleware(SessionMiddleware, secret_key=_admin_session_secret)

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


# ========== HTML页面缓存控制中间件 ==========

class NoCacheMiddleware(BaseHTTPMiddleware):
    """HTML页面和静态资源禁用浏览器缓存"""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # HTML页面、SPA路由、JS/CSS静态资源均禁用缓存，确保更新立即生效
        if (path.endswith(".html") or path.endswith(".js") or path.endswith(".css") or
            path == "/" or
            path.startswith("/console") or path.startswith("/chat") or
            path in ("/login", "/register", "/models", "/docs", "/sponsor", "/admin") or
            path.startswith("/legal") or
            path.startswith("/api/auth")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheMiddleware)


# ========== CSRF Origin 校验中间件 ==========
#
# 对 /api/* 的 POST/PUT/DELETE/PATCH 做同源校验：请求带 Origin 头时必须与 Host 同源，否则 403；
# 无 Origin 头的非浏览器客户端（curl/SDK 等）放行。
# 与前端 SameSite=lax cookie 构成双保险：即使 cookie 被跨站携带，Origin 不同也会被拒。

class OriginCheckMiddleware(BaseHTTPMiddleware):
    """CSRF 防护：校验写请求的 Origin 与 Host 同源"""

    async def dispatch(self, request: Request, call_next):
        if (
            request.method in ("POST", "PUT", "DELETE", "PATCH")
            and request.url.path.startswith("/api/")
        ):
            origin = request.headers.get("origin")
            if origin:
                # Origin 形如 https://host[:port]，与 Host 头（host[:port]）比对即可判定同源
                from urllib.parse import urlparse
                origin_netloc = urlparse(origin).netloc
                host = request.headers.get("host", "")
                if origin_netloc != host:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "跨站请求被拒绝（CSRF防护）"},
                    )
        return await call_next(request)


app.add_middleware(OriginCheckMiddleware)


# ========== 基础路由 ==========

@app.get("/healthz", tags=["公共"])
@app.get("/health", tags=["公共"])
async def healthz(request: Request = None):
    """健康检查（支持网关健康探测）"""
    # 数据库连通性检查（同步DB经线程池执行，避免健康探测阻塞事件循环）
    db_ok = False
    try:
        from app.database import fetch_one
        import asyncio
        result = await asyncio.to_thread(fetch_one, "SELECT 1 as ok")
        db_ok = result and result.get("ok") == 1
    except Exception:
        pass

    # 网关连通性检查
    gw_ok = False
    try:
        from app.routes.chat import _get_http_pool
        pool = await _get_http_pool()
        resp = await pool.get("http://127.0.0.1:8000/healthz", timeout=5.0)
        gw_ok = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "11.0.0",
        "database": "ok" if db_ok else "error",
        "gateway": "ok" if gw_ok else "error",
    }


@app.get("/robots.txt", tags=["公共"])
async def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /models\n"
        "Allow: /docs\n"
        "Disallow: /console/\n"
        "Disallow: /api/\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/favicon.ico", tags=["公共"])
async def favicon():
    """返回平台统一favicon图标"""
    ico_path = STATIC_DIR / "favicon.ico"
    if ico_path.exists():
        return FileResponse(str(ico_path), media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/sitemap.xml", tags=["公共"])
async def sitemap():
    """网站地图 - SEO"""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url><loc>https://your-domain.com/</loc><priority>1.0</priority></url>\n"
        "  <url><loc>https://your-domain.com/models</loc><priority>0.9</priority></url>\n"
        "  <url><loc>https://your-domain.com/docs</loc><priority>0.8</priority></url>\n"
        "  <url><loc>https://your-domain.com/sponsor</loc><priority>0.5</priority></url>\n"
        "</urlset>\n"
    )
    return Response(content=content, media_type="application/xml")


# ========== 静态文件 ==========

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ========== 路由注册 ==========

from app.routes.public import router as public_router
from app.routes.auth import router as auth_router
from app.routes.console import router as console_router
from app.routes.chat import router as chat_router
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(console_router)
app.include_router(chat_router)
from app.routes.platform_admin import router as platform_admin_router
app.include_router(platform_admin_router)

# 挂载 SQLAdmin 数据库管理面板
admin_panel = create_admin(app)


# ========== 启动配置 ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8001,
        reload=False,
        log_level="info",
    )
