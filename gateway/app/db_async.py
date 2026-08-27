"""
网关异步数据库引擎与会话工厂

使用 SQLAlchemy 2.0 async 模式 + asyncpg 驱动（PostgreSQL）。
与现有 database.py 共存，不修改原有同步代码。
"""
import os

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 数据库连接配置（从环境变量读取）
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_DB = os.environ.get("PG_DB", "aqua_gateway")
PG_USER = os.environ.get("PG_USER", "aqua")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")

# v10.1修复：改用 URL.create 参数式构造。
# 原先 f-string 直接拼接 DSN，密码含 @ : / # ? 等特殊字符时会破坏 URL 结构导致连接失败。
DATABASE_URL = URL.create(
    "postgresql+asyncpg",
    username=PG_USER,
    password=PG_PASSWORD,
    host=PG_HOST,
    port=int(PG_PORT),
    database=PG_DB,
)

async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_session():
    """FastAPI 依赖：获取异步数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
