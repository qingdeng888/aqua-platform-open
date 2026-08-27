"""
数据迁移脚本: SQLite → PostgreSQL
将 gateway.db 和 platform.db 的数据迁移到 PostgreSQL
"""
import os
import sys
from datetime import datetime

# 添加项目路径
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "platform"))
sys.path.insert(0, os.path.join(BASE, "gateway"))

# 使用新的 PG database 模块创建表结构
os.environ["PG_PLATFORM_DB"] = "aqua_platform"
os.environ["PG_GATEWAY_DB"] = "aqua_gateway"


def migrate_platform():
    """迁移平台数据库"""
    print("=" * 60)
    print("迁移平台数据库 (platform.db → aqua_platform)")
    print("=" * 60)

    # 1. 直接用 SQL 创建 PG 表结构
    import psycopg2
    pg_conn = psycopg2.connect(
        host=os.environ.get("PG_PLATFORM_HOST", "localhost"),
        port=int(os.environ.get("PG_PLATFORM_PORT", "5432")),
        dbname=os.environ.get("PG_PLATFORM_DB", "aqua_platform"),
        user=os.environ.get("PG_PLATFORM_USER", "aqua"),
        password=os.environ.get("PG_PLATFORM_PASSWORD", ""),
    )
    pg_conn.autocommit = True

    # 读取 database.py 中的建表 SQL
    db_py_path = os.path.join(BASE, "platform", "app", "database.py")
    with open(db_py_path) as f:
        db_content = f.read()

    # 提取 init_db 函数中的 CREATE TABLE 语句
    import re
    create_statements = re.findall(r'CREATE TABLE IF NOT EXISTS.*?;', db_content, re.DOTALL)
    for stmt in create_statements:
        try:
            pg_conn.cursor().execute(stmt)
            print(f"[OK] 表已创建: {stmt.split()[3]}")
        except Exception as e:
            print(f"[WARN] 建表: {e}")

    pg_conn.autocommit = False
    pg_conn.close()
    print("[OK] PostgreSQL 平台表结构已创建")

    # 2. 从 SQLite 读取数据
    import sqlite3
    sqlite_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "platform", "platform.db")
    if not os.path.exists(sqlite_path):
        print(f"[SKIP] SQLite 文件不存在: {sqlite_path}")
        return

    sl_conn = sqlite3.connect(sqlite_path)
    sl_conn.row_factory = sqlite3.Row

    # 3. 写入 PG
    import psycopg2
    import psycopg2.extras
    pg_conn = psycopg2.connect(
        host=os.environ.get("PG_PLATFORM_HOST", "localhost"),
        port=int(os.environ.get("PG_PLATFORM_PORT", "5432")),
        dbname=os.environ.get("PG_PLATFORM_DB", "aqua_platform"),
        user=os.environ.get("PG_PLATFORM_USER", "aqua"),
        password=os.environ.get("PG_PLATFORM_PASSWORD", ""),
    )
    pg_conn.autocommit = False

    tables = [
        ("users", "id, uuid, username, email, password_hash, display_name, status, created_at, updated_at, gw_client_id"),
        ("sessions", "id, user_id, csrf_token, ip, user_agent, created_at, expires_at"),
        ("user_api_keys", "id, user_id, gw_client_id, gw_key_id, key_prefix, label, status, created_at, api_key_encrypted"),
        ("chat_history", "id, user_id, title, messages, model, created_at, updated_at"),
        ("email_verification", "id, email, code, purpose, expires_at, used, created_at"),
        ("platform_settings", "key, value"),
        ("platform_audit", "id, user_id, action, detail, ip, created_at"),
        ("feedback", "id, user_id, username, email, title, content, category, status, reply, replied_at, created_at"),
    ]

    for table, cols in tables:
        try:
            rows = sl_conn.execute(f"SELECT {cols} FROM {table}").fetchall()
            if not rows:
                print(f"[SKIP] {table}: 无数据")
                continue

            placeholders = ", ".join(["%s"] * len(cols.split(",")))
            insert_sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

            with pg_conn.cursor() as cur:
                for row in rows:
                    try:
                        cur.execute(insert_sql, tuple(row))
                    except Exception as e:
                        print(f"  [WARN] {table} 行跳过: {e}")

            pg_conn.commit()
            print(f"[OK] {table}: {len(rows)} 条记录已迁移")
        except Exception as e:
            pg_conn.rollback()
            print(f"[ERR] {table}: {e}")

    sl_conn.close()
    pg_conn.close()


def migrate_gateway():
    """迁移网关数据库"""
    print("\n" + "=" * 60)
    print("迁移网关数据库 (gateway.db → aqua_gateway)")
    print("=" * 60)

    # 1. 用新的 database 模块初始化 PG 表结构
    from gateway.app.database import init_db as pg_init
    pg_init()
    print("[OK] PostgreSQL 网关表结构已创建")

    # 2. 从 SQLite 读取数据
    import sqlite3
    sqlite_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gateway", "gateway.db")
    if not os.path.exists(sqlite_path):
        print(f"[SKIP] SQLite 文件不存在: {sqlite_path}")
        return

    sl_conn = sqlite3.connect(sqlite_path)
    sl_conn.row_factory = sqlite3.Row

    # 3. 写入 PG
    import psycopg2
    pg_conn = psycopg2.connect(
        host=os.environ.get("PG_GATEWAY_HOST", "localhost"),
        port=int(os.environ.get("PG_GATEWAY_PORT", "5432")),
        dbname=os.environ.get("PG_GATEWAY_DB", "aqua_gateway"),
        user=os.environ.get("PG_GATEWAY_USER", "aqua"),
        password=os.environ.get("PG_GATEWAY_PASSWORD", ""),
    )
    pg_conn.autocommit = False

    tables = [
        ("admin_settings", "key, value, updated_at"),
        ("upstream_keys", "id, name, api_key_ciphertext, key_prefix, provider, weight, rpm_limit, switch_threshold, status, created_at, updated_at"),
        ("clients", "id, name, status, created_at, updated_at"),
        ("client_api_keys", "id, client_id, key_ciphertext, key_prefix, status, created_at, last_used_at, label"),
        ("audit_logs", "id, action, target_type, target_id, detail, ip, created_at"),
        ("platform_tokens", "id, name, token_hash, scopes, status, created_at, expires_at, last_used_at"),
    ]

    for table, cols in tables:
        try:
            rows = sl_conn.execute(f"SELECT {cols} FROM {table}").fetchall()
            if not rows:
                print(f"[SKIP] {table}: 无数据")
                continue

            placeholders = ", ".join(["%s"] * len(cols.split(",")))
            insert_sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

            with pg_conn.cursor() as cur:
                for row in rows:
                    try:
                        cur.execute(insert_sql, tuple(row))
                    except Exception as e:
                        print(f"  [WARN] {table} 行跳过: {e}")

            pg_conn.commit()
            print(f"[OK] {table}: {len(rows)} 条记录已迁移")
        except Exception as e:
            pg_conn.rollback()
            print(f"[ERR] {table}: {e}")

    sl_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    migrate_platform()
    migrate_gateway()
    print("\n✅ 数据迁移完成!")
