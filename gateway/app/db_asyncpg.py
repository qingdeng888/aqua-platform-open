"""
异步数据库连接池 - v10.0 基于 asyncpg

对标 litellm 的全异步数据库访问模式：
- 替代同步 psycopg2，消除事件循环阻塞
- 连接池复用（min_size=5, max_size=20）
- 自动健康检查与重连
- 兼容现有 fetch_one/fetch_all/execute 接口
"""
import os
import logging
from typing import Optional, Any

import asyncpg

logger = logging.getLogger("acu.db_asyncpg")

_pool: Optional[asyncpg.Pool] = None
_PG_DSN: Optional[str] = None


def _get_dsn() -> str:
    """获取数据库 DSN"""
    global _PG_DSN
    if _PG_DSN:
        return _PG_DSN
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DB", "aqua_gateway")
    user = os.environ.get("PG_USER", "aqua")
    password = os.environ.get("PG_PASSWORD")
    if not password:
        raise RuntimeError("[FATAL] PG_PASSWORD 环境变量未设置")
    _PG_DSN = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return _PG_DSN


async def get_pool() -> asyncpg.Pool:
    """获取或创建连接池（延迟初始化，类似 litellm 模式）"""
    global _pool
    if _pool is None or _pool._closed:
        dsn = _get_dsn()
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=5,
            max_size=20,
            command_timeout=30,
            max_inactive_connection_lifetime=300,
            # 自动重连：检测连接健康
            setup=__setup_connection,
        )
        logger.info(f"[v10.0] asyncpg 连接池已创建 (min=5, max=20)")
    return _pool


async def __setup_connection(conn: asyncpg.Connection):
    """连接初始化设置"""
    await conn.execute("SET TIME ZONE 'UTC'")
    await conn.execute("SET statement_timeout = '30s'")


async def close_pool():
    """关闭连接池"""
    global _pool
    if _pool and not _pool._closed:
        await _pool.close()
        _pool = None
        logger.info("[v10.0] asyncpg 连接池已关闭")


# ========== 兼容接口（替换同步 db_async.fetch_one/fetch_all/execute） ==========

async def fetch_one(sql: str, *args) -> Optional[dict]:
    """查询单条记录 - 异步非阻塞版"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetch_all(sql: str, *args) -> list:
    """查询多条记录 - 异步非阻塞版"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def execute(sql: str, *args) -> str:
    """执行写操作 - 异步非阻塞版"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def execute_many(sql: str, args_list: list):
    """批量执行 - 对标 executemany（用于日志批量写入）"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(sql, args_list)
