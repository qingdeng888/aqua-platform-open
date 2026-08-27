"""公开页面路由 - 首页/模型列表/API文档/公共统计"""
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["公开页面"])
logger = logging.getLogger("aqua.public")

SPA_HTML_PATH = Path(__file__).resolve().parents[1] / "static" / "index.html"


def _serve_spa() -> HTMLResponse:
    """返回SPA HTML页面"""
    if SPA_HTML_PATH.exists():
        content = SPA_HTML_PATH.read_text(encoding="utf-8")
    else:
        # 降级：如果静态文件不存在，返回基础页面
        content = """<!DOCTYPE html>
<html><head><title>AQUA Platform</title></head>
<body style="background:#1a1a2e;color:#e0e0e0;display:flex;justify-content:center;
             align-items:center;height:100vh;margin:0;font-family:sans-serif;">
    <div style="text-align:center">
        <h1>AQUA AI平台</h1>
        <p>服务运行中</p>
        <p style="color:#888">前端资源加载中...</p>
    </div>
</body></html>"""
    return HTMLResponse(content=content)


@router.get("/", response_class=HTMLResponse)
async def index():
    """首页"""
    return _serve_spa()


@router.get("/models", response_class=HTMLResponse)
async def models_page():
    """模型列表页"""
    return _serve_spa()


@router.get("/docs", response_class=HTMLResponse)
async def docs_page():
    """API文档页"""
    return _serve_spa()


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    """登录页"""
    return _serve_spa()


@router.get("/register", response_class=HTMLResponse)
async def register_page():
    """注册页"""
    return _serve_spa()


@router.get("/console", response_class=HTMLResponse)
async def console_page():
    """控制台页"""
    return _serve_spa()


@router.get("/console/{path:path}", response_class=HTMLResponse)
async def console_sub_page(path: str):
    """控制台子页面"""
    return _serve_spa()


@router.get("/chat", response_class=HTMLResponse)
async def chat_page():
    """聊天页"""
    return _serve_spa()


@router.get("/chat/{path:path}", response_class=HTMLResponse)
async def chat_sub_page(path: str):
    """聊天子页面"""
    return _serve_spa()


@router.get("/sponsor", response_class=HTMLResponse)
async def sponsor_page():
    """赞助页"""
    return _serve_spa()


@router.get("/legal/disclaimer", response_class=HTMLResponse)
async def legal_disclaimer_page():
    """免责协议页"""
    return _serve_spa()


# 管理后台页面（独立HTML，非SPA）
ADMIN_HTML_PATH = Path(__file__).resolve().parents[1] / "static" / "admin.html"


@router.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """平台管理后台入口"""
    if ADMIN_HTML_PATH.exists():
        content = ADMIN_HTML_PATH.read_text(encoding="utf-8")
    else:
        content = "<html><body><h1>Admin panel not found</h1></body></html>"
    return HTMLResponse(content=content)


# ========== 公共统计 API ==========

@router.get("/api/public/stats", tags=["公共API"])
async def public_stats():
    """返回首页统计数据

    仅暴露非敏感状态字段；注册用户数、活跃上游密钥数等经营数据已移除（防竞品探测）。
    """
    # 可用模型数（来自网关缓存的已验证模型列表）
    models_count = 0
    try:
        from app.routes.chat import _get_http_pool
        pool = await _get_http_pool(timeout=5.0)
        r = await pool.get("http://127.0.0.1:8000/api/public/models", timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                models_count = len(data)
            elif isinstance(data, dict):
                models_count = len(data.get("data", data.get("models", [])))
    except Exception:
        pass

    # 调度算法数（固定值）
    algorithms = 17

    return {
        "models": models_count or 54,
        "algorithms": algorithms,
    }
