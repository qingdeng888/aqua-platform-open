"""
用户平台数据库模块 - PostgreSQL

表结构按规划文档第3.3节实现：
- users / sessions / user_api_keys
- chat_history（新增对话历史表）
- request_logs / email_verification
- usage_cache / platform_settings / platform_audit
"""
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor


# 连接池
_pool: Optional[pool.ThreadedConnectionPool] = None


def _get_conn():
    """从连接池获取连接"""
    global _pool
    if _pool is None:
        _pg_password = os.environ.get("PG_PLATFORM_PASSWORD")
        if not _pg_password:
            raise RuntimeError("[FATAL] 环境变量 PG_PLATFORM_PASSWORD 未设置！")
        _pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            host=os.getenv("PG_PLATFORM_HOST", "localhost"),
            port=int(os.getenv("PG_PLATFORM_PORT", "5432")),
            dbname=os.getenv("PG_PLATFORM_DB", "aqua_platform"),
            user=os.getenv("PG_PLATFORM_USER", "aqua"),
            password=_pg_password,
        )
    return _pool.getconn()


def _put_conn(conn) -> None:
    """归还连接到连接池"""
    global _pool
    if _pool is not None:
        _pool.putconn(conn)


# 中国标准时区 UTC+8
CST = timezone(timedelta(hours=8))


def utcnow() -> str:
    """返回ISO格式UTC时间（毫秒精度）"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def localnow() -> str:
    """返回本地时间(CST+8)ISO格式（毫秒精度）"""
    now = datetime.now(CST)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}+08:00"


def localnow_ms() -> int:
    """返回当前本地时间的毫秒级时间戳"""
    return int(datetime.now(CST).timestamp() * 1000)


def today_start_utc() -> str:
    """返回今日本地零点(CST)对应的UTC时间（用于数据库查询边界）"""
    now_cst = datetime.now(CST)
    midnight_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_cst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def today_start_local() -> str:
    """返回今日本地零点的ISO格式（CST）"""
    now_cst = datetime.now(CST)
    midnight_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_cst.strftime("%Y-%m-%dT%H:%M:%S.000+08:00")


def days_ago_utc(days: int) -> str:
    """返回N天前本地零点(CST)对应的UTC时间"""
    now_cst = datetime.now(CST)
    target = (now_cst - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return target.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def execute(sql: str, params: tuple = ()) -> int:
    """执行写操作并提交，返回影响行数（异常时rollback，避免失败事务随连接归还连接池）"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)


def fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """查询单条记录（异常时rollback，与execute同契约）"""
    conn = _get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)


def fetch_all(sql: str, params: tuple = ()) -> list:
    """查询多条记录（异常时rollback，与execute同契约）"""
    conn = _get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)


def get_setting(key: str) -> Optional[str]:
    """获取平台设置"""
    row = fetch_one("SELECT value FROM platform_settings WHERE key = %s", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    """设置平台设置"""
    execute(
        "INSERT INTO platform_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


def init_db() -> None:
    """初始化数据库表结构"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                uuid TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                csrf_token TEXT NOT NULL,
                ip TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                gw_client_id TEXT NOT NULL,
                gw_key_id TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                label TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT '',
                messages TEXT NOT NULL DEFAULT '[]',
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS request_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                key_id TEXT NOT NULL,
                model TEXT NOT NULL,
                is_stream INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_msg TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_user_time ON request_logs(user_id, created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(model)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_verification (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                purpose TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_cache (
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                model TEXT NOT NULL,
                total_requests INTEGER DEFAULT 0,
                success_requests INTEGER DEFAULT 0,
                error_requests INTEGER DEFAULT 0,
                avg_latency_ms REAL DEFAULT 0,
                last_synced_at TEXT NOT NULL,
                PRIMARY KEY (user_id, date, model)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_audit (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                email TEXT DEFAULT '',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT '其他',
                status TEXT DEFAULT 'pending',
                reply TEXT DEFAULT '',
                replied_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at)")
        conn.commit()

        # 兼容旧表结构 - 添加新字段
        try:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'user_api_keys' AND table_schema = 'public'
            """)
            existing_cols = {row[0] for row in cur.fetchall()}
            if "api_key_encrypted" not in existing_cols:
                cur.execute("ALTER TABLE user_api_keys ADD COLUMN api_key_encrypted TEXT DEFAULT ''")
                conn.commit()
        except Exception:
            pass

        # 兼容旧表结构 - 添加 gw_client_id 到 users 表
        try:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'users' AND table_schema = 'public'
            """)
            existing_cols = {row[0] for row in cur.fetchall()}
            if "gw_client_id" not in existing_cols:
                cur.execute("ALTER TABLE users ADD COLUMN gw_client_id TEXT DEFAULT ''")
                conn.commit()
        except Exception:
            pass

        # v9.0: 用户分类字段 - user_type (old/new), daily_limit, daily_used
        try:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'users' AND table_schema = 'public'
            """)
            existing_cols = {row[0] for row in cur.fetchall()}
            if "user_type" not in existing_cols:
                cur.execute("ALTER TABLE users ADD COLUMN user_type TEXT DEFAULT 'old'")
                conn.commit()
            if "daily_limit" not in existing_cols:
                cur.execute("ALTER TABLE users ADD COLUMN daily_limit INTEGER DEFAULT -1")
                conn.commit()
            if "daily_used" not in existing_cols:
                cur.execute("ALTER TABLE users ADD COLUMN daily_used INTEGER DEFAULT 0")
                conn.commit()
            if "daily_reset_at" not in existing_cols:
                cur.execute("ALTER TABLE users ADD COLUMN daily_reset_at TEXT DEFAULT ''")
                conn.commit()
        except Exception:
            pass

        # v9.0: 创建 IP 监控表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ip_monitoring (
                id SERIAL PRIMARY KEY,
                ip TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL DEFAULT 'request',
                anomaly_score REAL DEFAULT 0,
                blocked INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ip_monitoring_ip ON ip_monitoring(ip)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ip_monitoring_user ON ip_monitoring(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ip_monitoring_created ON ip_monitoring(created_at)")
        conn.commit()

        # 迁移：为 request_logs 添加毫秒级精准统计字段
        _migrate_platform_request_logs(conn)
    finally:
        _put_conn(conn)


def _migrate_platform_request_logs(conn) -> None:
    """迁移：为 request_logs 添加 started_at/completed_at/latency_us 字段"""
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'request_logs' AND table_schema = 'public'
    """)
    existing_cols = {row[0] for row in cur.fetchall()}

    migrations = [
        ("started_at", "TEXT DEFAULT ''"),
        ("completed_at", "TEXT DEFAULT ''"),
        ("latency_us", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE request_logs ADD COLUMN {col_name} {col_def}")
                conn.commit()
            except Exception:
                pass


def seed_defaults() -> None:
    """初始化默认设置"""
    defaults = {
        "platform_token": "",
        "rate_limit_prefix": "rl_",
        # 注册开关：取环境变量 REGISTRATION_OPEN（默认开放）
        "registration_open": os.environ.get("REGISTRATION_OPEN", "1"),
    }
    for key, value in defaults.items():
        if get_setting(key) is None:
            set_setting(key, value)
