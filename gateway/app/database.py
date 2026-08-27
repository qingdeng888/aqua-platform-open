"""
网关数据库模块 - PostgreSQL (psycopg2) 同步访问

v7.0 关键变更：
- upstream_keys 表移除所有冷却状态字段（cooling_until/current_minute_requests等）
  冷却状态完全由调度器的桶级字典管理，不持久化到DB
- 新增 commercial_detection 表用于商用行为识别
- request_logs 表包含完整token统计字段
"""
import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

# 异步连接池支持（可选，需要 asyncpg）
from app import db_async_pool

logger = logging.getLogger("acu.database")

# 连接池（全局单例）
_pool = None


# 连接池上限（可经环境变量 GW_DB_POOL_SIZE 调整，默认30）
POOL_MAXCONN = max(5, int(os.environ.get("GW_DB_POOL_SIZE", "30")))


def _init_pool():
    """初始化连接池"""
    global _pool
    if _pool is None:
        # 惰性校验：不在 import 时硬失败（纯函数/测试导入不再被迫先配环境变量），首次真正建池时才拦截
        if not PG_PASSWORD:
            raise RuntimeError("[FATAL] 环境变量 PG_PASSWORD 未设置！请设置后重新启动。")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=5, maxconn=POOL_MAXCONN,
            host=PG_HOST, port=PG_PORT, dbname=PG_DB,
            user=PG_USER, password=PG_PASSWORD,
        )


# 数据库连接配置（从环境变量读取）
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_DB = os.environ.get("PG_DB", "aqua_gateway")
PG_USER = os.environ.get("PG_USER", "aqua")
PG_PASSWORD = os.environ.get("PG_PASSWORD")

# 中国标准时区 UTC+8
CST = timezone(timedelta(hours=8))


def _get_conn():
    """从连接池获取 PostgreSQL 连接

    修复：设 autocommit=True（方案选择：autocommit 而非 except 中 rollback）。
    语句即时提交，SELECT 之后归还连接不残留事务；SQL 异常时连接上没有
    中止的事务，归还连接池不会"带毒"（原实现异常路径不 rollback，
    aborted transaction 会让后续复用该连接的请求全部报错）。
    """
    if _pool is None:
        _init_pool()
    conn = _pool.getconn()
    conn.autocommit = True  # 自动提交：无残留事务，异常不带毒回池
    # 使用 RealDictCursor 使返回行为 dict
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def _put_conn(conn):
    """归还连接到连接池"""
    if _pool and conn:
        _pool.putconn(conn)


def warmup_pool(count: int = 3) -> None:
    """预热连接池：取放 count 条连接，摊平首请求的建连开销（启动时调用一次）"""
    if _pool is None:
        _init_pool()
    conns = []
    try:
        for _ in range(count):
            conns.append(_get_conn())
    except Exception as e:
        logger.warning(f"连接池预热部分失败(不影响启动): {e}")
    finally:
        for conn in conns:
            _put_conn(conn)


def utcnow() -> str:
    """返回ISO格式UTC时间（毫秒精度，Z格式）。写库时间戳统一使用本函数"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def utcnow_minus(seconds: int) -> str:
    """返回N秒前的UTC时间（Z格式，毫秒精度）——契约函数，供时间窗口查询边界使用"""
    target = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return target.strftime("%Y-%m-%dT%H:%M:%S.") + f"{target.microsecond // 1000:03d}Z"


def localnow() -> str:
    """返回本地时间(CST+8)ISO格式（毫秒精度）"""
    now = datetime.now(CST)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}+08:00"


def localnow_ms() -> int:
    """返回当前本地时间的毫秒级时间戳"""
    return int(datetime.now(CST).timestamp() * 1000)


def today_start_utc() -> str:
    """返回今日本地零点(CST)对应的UTC时间（用于数据库查询边界）

    例如：本地时间 2026-07-16 14:30 CST
    返回：2026-07-15T16:00:00.000Z (即 CST 2026-07-16 00:00:00)
    """
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
    """执行写操作，返回受影响的行数（autocommit 模式下语句即时提交，无需显式 commit）"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.rowcount
    finally:
        _put_conn(conn)


def fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """查询单条记录"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        _put_conn(conn)


def fetch_all(sql: str, params: tuple = ()) -> list:
    """查询多条记录"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        _put_conn(conn)


# ─── 异步连接池桥接函数 ─────────────────────────────
async def async_get_pool():
    """返回 db_async_pool 模块，用于获取异步连接池及其函数

    用法：
        pool_mod = await async_get_pool()
        row = await pool_mod.fetch_one("SELECT * FROM ...")
        rows = await pool_mod.fetch_all("SELECT * FROM ...")
        result = await pool_mod.execute("INSERT INTO ...", val1, val2)

    首次调用时会自动初始化连接池。
    """
    if db_async_pool._pool is None:
        await db_async_pool.init_async_pool()
    return db_async_pool


def get_setting(key: str) -> Optional[str]:
    """获取配置项"""
    row = fetch_one("SELECT value FROM admin_settings WHERE key = %s", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    """设置配置项"""
    now = utcnow()
    execute(
        "INSERT INTO admin_settings (key, value, updated_at) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = %s",
        (key, value, now, value, now),
    )


def insert_audit(action: str, target_type: str, target_id: str, detail: str) -> None:
    """插入审计日志"""
    execute(
        "INSERT INTO audit_logs (id, operator, action, target_type, target_id, detail, created_at) "
        "VALUES (%s, 'admin', %s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), action, target_type, target_id, detail, utcnow()),
    )


def init_db() -> None:
    """创建所有表（v10.0 完整表结构，PostgreSQL 语法）"""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS upstream_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT DEFAULT 'nvidia',
                api_key_ciphertext TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                weight INT DEFAULT 1,
                rpm_limit INT DEFAULT 40,
                switch_threshold INT DEFAULT 38,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_api_keys (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                key_ciphertext TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS request_logs (
                id TEXT PRIMARY KEY,
                client_id TEXT,
                upstream_key_id TEXT,
                model TEXT,
                status_code INT,
                latency_ms INT,
                retried INT DEFAULT 0,
                prompt_tokens INT DEFAULT 0,
                completion_tokens INT DEFAULT 0,
                total_tokens INT DEFAULT 0,
                is_stream INT DEFAULT 0,
                error_msg TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_client_created ON request_logs(client_id, created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(model);")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                operator TEXT,
                action TEXT,
                target_type TEXT,
                target_id TEXT,
                detail TEXT,
                created_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_tokens (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                scopes TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                expires_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS key_usage_stats (
                key_id TEXT PRIMARY KEY,
                total_requests INT DEFAULT 0,
                total_success INT DEFAULT 0,
                total_failures INT DEFAULT 0,
                consecutive_failures INT DEFAULT 0,
                total_429 INT DEFAULT 0,
                total_5xx INT DEFAULT 0,
                total_timeout INT DEFAULT 0,
                daily_requests INT DEFAULT 0,
                daily_success INT DEFAULT 0,
                daily_failures INT DEFAULT 0,
                daily_date TEXT,
                weekly_requests INT DEFAULT 0,
                weekly_success INT DEFAULT 0,
                weekly_date TEXT,
                monthly_requests INT DEFAULT 0,
                monthly_success INT DEFAULT 0,
                monthly_date TEXT,
                avg_rt REAL DEFAULT 0,
                p95_rt REAL DEFAULT 0,
                last_success_at TEXT,
                last_failure_at TEXT,
                last_failure_type TEXT,
                updated_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS commercial_detection (
                client_id TEXT PRIMARY KEY,
                confidence_score INT DEFAULT 0,
                interval_stddev REAL DEFAULT 0,
                interval_cv REAL DEFAULT 0,
                model_switch_count INT DEFAULT 0,
                avg_concurrent REAL DEFAULT 0,
                template_ratio REAL DEFAULT 0,
                request_intervals TEXT DEFAULT '[]',
                last_updated TEXT,
                admin_confirmed INT DEFAULT 0,
                false_positive INT DEFAULT 0
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bucket_snapshots (
                id SERIAL PRIMARY KEY,
                key_id TEXT NOT NULL,
                model TEXT NOT NULL,
                rpm INT DEFAULT 0,
                threshold INT DEFAULT 38,
                success_rate REAL DEFAULT 100,
                avg_rt REAL DEFAULT 0,
                p95_rt REAL DEFAULT 0,
                cooldown_remaining INT DEFAULT 0,
                health_score INT DEFAULT 100,
                warmup_progress INT DEFAULT 30,
                soft_busy INT DEFAULT 0,
                isolated INT DEFAULT 0,
                captured_at TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ip_monitor (
                ip TEXT PRIMARY KEY,
                client_ids TEXT DEFAULT '[]',
                first_seen TEXT,
                last_seen TEXT,
                request_count INT DEFAULT 0,
                anomaly_score INT DEFAULT 0,
                anomaly_reasons TEXT DEFAULT '[]',
                blocked INT DEFAULT 0,
                block_reason TEXT DEFAULT '',
                blocked_at TEXT,
                unblocked_at TEXT,
                user_agents TEXT DEFAULT '[]'
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ip_blocked (
                ip TEXT PRIMARY KEY,
                reason TEXT DEFAULT '',
                blocked_at TEXT NOT NULL,
                unblocked_at TEXT
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ip_monitor_anomaly ON ip_monitor(anomaly_score);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ip_monitor_last_seen ON ip_monitor(last_seen);")
        conn.commit()

        # 迁移：为 request_logs 添加毫秒级精准统计字段
        _migrate_request_logs_precision(conn)
        #  迁移：为 request_logs 添加全量请求日志字段
        _migrate_request_logs_full(conn)
        # v9.2 迁移：为 clients 表添加 user_type 字段
        _migrate_clients_user_type(conn)
    finally:
        # v10.0 修复：归还连接到连接池而非关闭（防止连接池耗尽）
        _put_conn(conn)


def _get_column_names(conn, table_name: str) -> set:
    """查询指定表的现有列名（PostgreSQL information_schema）"""
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND table_schema = 'public'",
        (table_name,),
    )
    return {row["column_name"] for row in cur.fetchall()}


def _migrate_request_logs_precision(conn) -> None:
    """迁移：为 request_logs 添加 started_at/completed_at/latency_us 字段"""
    existing_cols = _get_column_names(conn, "request_logs")

    migrations = [
        ("started_at", "TEXT DEFAULT ''"),
        ("completed_at", "TEXT DEFAULT ''"),
        ("latency_us", "BIGINT DEFAULT 0"),  # 微秒级延迟（1ms=1000us）
    ]
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            cur = conn.cursor()
            cur.execute(f"ALTER TABLE request_logs ADD COLUMN {col_name} {col_def}")
    conn.commit()


def _migrate_request_logs_full(conn) -> None:
    """迁移：为 request_logs 添加全量请求日志字段"""
    existing_cols = _get_column_names(conn, "request_logs")

    migrations = [
        ("request_path", "TEXT DEFAULT ''"),
        ("http_method", "TEXT DEFAULT ''"),
        ("client_ip", "TEXT DEFAULT ''"),
        ("user_agent", "TEXT DEFAULT ''"),
        ("request_params", "TEXT DEFAULT ''"),
        ("request_body", "TEXT DEFAULT ''"),
        ("response_body", "TEXT DEFAULT ''"),
        ("error_type", "TEXT DEFAULT ''"),
        ("error_detail", "TEXT DEFAULT ''"),
        ("error_stack", "TEXT DEFAULT ''"),
        ("business_code", "TEXT DEFAULT ''"),
        ("log_category", "TEXT DEFAULT 'normal'"),
    ]
    cur = conn.cursor()
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE request_logs ADD COLUMN {col_name} {col_def}")

    # 为新字段添加索引
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_path ON request_logs(request_path)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_status ON request_logs(status_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_category ON request_logs(log_category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_client_ip ON request_logs(client_ip)")
    #  复合索引：优化统计查询性能
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_status_created ON request_logs(status_code, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_model_created ON request_logs(model, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_client_status_created ON request_logs(client_id, status_code, created_at)")
    conn.commit()


def seed_defaults() -> None:
    """插入默认配置"""
    defaults = {
        "upstream_base_url": "https://integrate.api.nvidia.com/v1",
        "chat_path": "/chat/completions",
        "models_path": "/models",
        "cooldown_seconds": "240",
        "switch_threshold": "38",
        "model_mappings": "{}",
        "maintenance_mode": "false",
    }
    for key, value in defaults.items():
        if get_setting(key) is None:
            set_setting(key, value)

    # 确保主密钥和网关密钥存在
    from app.security import generate_upstream_master_key, generate_platform_key, hash_secret
    if get_setting("upstream_master_key") is None:
        set_setting("upstream_master_key", generate_upstream_master_key())
    if get_setting("gateway_secret") is None:
        set_setting("gateway_secret", generate_platform_key())

    # 注册平台令牌（如果 AQUA_PLATFORM_TOKEN 环境变量存在且未入库）
    platform_token = os.environ.get("AQUA_PLATFORM_TOKEN", "")
    if platform_token:
        token_hash = hash_secret(platform_token)
        existing = fetch_one(
            "SELECT id FROM platform_tokens WHERE token_hash = %s",
            (token_hash,),
        )
        if not existing:
            token_id = str(uuid.uuid4())
            now = utcnow()
            execute(
                "INSERT INTO platform_tokens (id, name, token_hash, scopes, status, created_at) "
                "VALUES (%s, %s, %s, %s, 'active', %s)",
                (token_id, "auto_platform_token", token_hash, '["admin"]', now),
            )
            logger.info(f"平台令牌已自动注册: id={token_id}")


def cleanup_success_logs(keep_days: int = 3, keep_error_days: int = 90) -> dict:
    """
    清理请求日志：成功日志短期保留，错误日志长期保存

    Args:
        keep_days: 成功日志保留天数（默认3天）
        keep_error_days: 错误日志保留天数（默认90天）

    Returns:
        dict: {"success_deleted": 删除的成功日志数, "error_deleted": 删除的错误日志数}
    """
    import logging
    logger = logging.getLogger("acu.database")

    result = {"success_deleted": 0, "error_deleted": 0}

    try:
        # 删除过期的成功日志（status_code=200且超过keep_days天）
        success_cutoff = days_ago_utc(keep_days)
        row = fetch_one(
            "SELECT COUNT(*) as cnt FROM request_logs WHERE status_code = 200 AND created_at < %s",
            (success_cutoff,),
        )
        success_count = row["cnt"] if row else 0
        if success_count > 0:
            execute(
                "DELETE FROM request_logs WHERE status_code = 200 AND created_at < %s",
                (success_cutoff,),
            )
            result["success_deleted"] = success_count

        # 删除过期的错误日志（status_code!=200且超过keep_error_days天）
        error_cutoff = days_ago_utc(keep_error_days)
        row = fetch_one(
            "SELECT COUNT(*) as cnt FROM request_logs WHERE status_code != 200 AND created_at < %s",
            (error_cutoff,),
        )
        error_count = row["cnt"] if row else 0
        if error_count > 0:
            execute(
                "DELETE FROM request_logs WHERE status_code != 200 AND created_at < %s",
                (error_cutoff,),
            )
            result["error_deleted"] = error_count

        if success_count > 0 or error_count > 0:
            logger.info(f"日志清理: 删除{success_count}条成功日志(>{keep_days}天), {error_count}条错误日志(>{keep_error_days}天)")

        # PostgreSQL VACUUM（需要单独连接且 autocommit=True）
        try:
            conn = psycopg2.connect(
                host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                user=PG_USER, password=PG_PASSWORD,
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("VACUUM request_logs")
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"VACUUM 失败(可忽略): {e}")

    except Exception as e:
        logger.error(f"日志清理失败: {e}")

    return result


def _migrate_clients_user_type(conn):
    """v9.2 迁移：为 clients 表添加 user_type 字段（old=老用户4并发, new=新用户2并发）"""
    existing = _get_column_names(conn, "clients")
    if "user_type" not in existing:
        cur = conn.cursor()
        cur.execute("ALTER TABLE clients ADD COLUMN user_type TEXT DEFAULT 'old'")
        conn.commit()
        logger.info("数据库迁移: clients 表已添加 user_type 字段 (默认 'old')")
