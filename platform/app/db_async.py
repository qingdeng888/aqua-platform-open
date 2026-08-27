"""
用户平台异步数据库引擎与会话工厂

提供：
- asyncpg 连接池（直接异步 SQL 访问）
- 异步版 execute / fetch_one / fetch_all
- SQLAlchemy async_engine（供 sqladmin 管理后台使用）
"""
import os

import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine

# ── SQLAlchemy async engine（供 sqladmin / admin_panel.py 使用）──
ASYNC_DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{os.getenv('PG_PLATFORM_USER', 'aqua')}:"
    f"{os.getenv('PG_PLATFORM_PASSWORD', '')}@"
    f"{os.getenv('PG_PLATFORM_HOST', 'localhost')}:"
    f"{os.getenv('PG_PLATFORM_PORT', '5432')}/"
    f"{os.getenv('PG_PLATFORM_DB', 'aqua_platform')}"
)

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

# ── asyncpg 连接池 ──
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.getenv("PG_PLATFORM_HOST", "localhost"),
            port=int(os.getenv("PG_PLATFORM_PORT", "5432")),
            database=os.getenv("PG_PLATFORM_DB", "aqua_platform"),
            user=os.getenv("PG_PLATFORM_USER", "aqua"),
            password=os.getenv("PG_PLATFORM_PASSWORD", ""),
            min_size=1,
            max_size=10,
        )
    return _pool


async def async_execute(sql: str, params: tuple = ()) -> int:
    """执行写操作，返回影响行数"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(sql, *params)
        return int(result.split()[-1]) if result else 0


async def async_fetch_one(sql: str, params: tuple = ()) -> dict | None:
    """查询单条记录，返回 dict"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)
        return dict(row) if row else None


async def async_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    """查询多条记录，返回 list[dict]"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]


async def get_async_session():
    """FastAPI 依赖：获取异步数据库连接"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        try:
            yield conn
        except Exception:
            raise
