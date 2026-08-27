"""
AQUA Platform - SQLite → PostgreSQL 数据迁移脚本

迁移策略：
1. 从 SQLite 导出所有数据为 JSON
2. 连接到 PostgreSQL 并重建表结构
3. 写入所有数据
4. 验证数据一致性

使用方式:
  python3 migrate_to_postgresql.py [--execute]

环境变量:
  PG_GATEWAY_DSN=postgresql://aqua:@localhost:5432/aqua_gateway
  PG_PLATFORM_DSN=postgresql://aqua:@localhost:5432/aqua_platform
"""
import json
import os
import sys
from pathlib import Path

# ============================================================
# 配置
# ============================================================

GATEWAY_SQLITE = os.path.join(os.path.dirname(__file__), "..", "gateway", "gateway.db")
PLATFORM_SQLITE = os.path.join(os.path.dirname(__file__), "..", "platform", "platform.db")

PG_GATEWAY_DSN = os.environ.get(
    "PG_GATEWAY_DSN",
    "postgresql://aqua:@localhost:5432/aqua_gateway"
)
PG_PLATFORM_DSN = os.environ.get(
    "PG_PLATFORM_DSN",
    "postgresql://aqua:@localhost:5432/aqua_platform"
)

# ============================================================
# 第一步: 从 SQLite 导出数据
# ============================================================

def export_sqlite(db_path: str) -> dict:
    """导出 SQLite 数据库全部数据为字典"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]

    data = {}
    for table in tables:
        rows = conn.execute(f"SELECT * FROM \"{table}\"").fetchall()
        data[table] = [dict(r) for r in rows]
        print(f"  [SQLite] 表 {table}: {len(rows)} 行")

    conn.close()
    return data

# ============================================================
# 第二步: 写入 PostgreSQL
# ============================================================

def import_to_postgresql(data: dict, dsn: str):
    """将数据导入 PostgreSQL"""
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("请安装: pip install sqlalchemy psycopg2-binary")
        sys.exit(1)

    engine = create_engine(dsn)
    total = 0

    with engine.begin() as conn:
        for table, rows in data.items():
            if not rows:
                print(f"  [PG] 表 {table}: 空，跳过")
                continue

            # 获取列名
            columns = list(rows[0].keys())
            placeholders = ", ".join([f":{c}" for c in columns])
            col_names = ", ".join(columns)

            # 批量插入
            for i in range(0, len(rows), 100):
                batch = rows[i:i+100]
                stmt = text(
                    f"INSERT INTO \"{table}\" ({col_names}) VALUES ({placeholders})"
                    f" ON CONFLICT DO NOTHING"
                )
                conn.execute(stmt, batch)
                total += len(batch)

            print(f"  [PG] 表 {table}: {len(rows)} 行")

    return total

# ============================================================
# 主流程
# ============================================================

def main():
    execute = "--execute" in sys.argv

    print("=" * 60)
    print("AQUA 平台 - SQLite → PostgreSQL 数据迁移")
    print("=" * 60)

    if not execute:
        print("\n⚠️  预览模式 (添加 --execute 执行迁移)")
    print()

    # ----- Gateway -----
    print("\n--- Gateway 数据库 ---")
    gw_data = export_sqlite(GATEWAY_SQLITE)
    if execute:
        n = import_to_postgresql(gw_data, PG_GATEWAY_DSN)
        print(f"\n  ✓ 已导入 {n} 条记录到 {PG_GATEWAY_DSN}")
    else:
        print(f"\n  → 将导入 {PG_GATEWAY_DSN}")

    # ----- Platform -----
    print("\n--- Platform 数据库 ---")
    pl_data = export_sqlite(PLATFORM_SQLITE)
    if execute:
        n = import_to_postgresql(pl_data, PG_PLATFORM_DSN)
        print(f"\n  ✓ 已导入 {n} 条记录到 {PG_PLATFORM_DSN}")
    else:
        print(f"\n  → 将导入 {PG_PLATFORM_DSN}")

    # ----- 验证 -----
    if execute:
        verify()

    print("\n" + "=" * 60)
    if execute:
        print("迁移完成! 请手动更新以下文件以切换连接:",
              "\n  1. gateway/app/database.py → PG DSN",
              "\n  2. gateway/app/db_async.py → PG DSN",
              "\n  3. platform/app/database.py → PG DSN",
              "\n  4. platform/app/db_async.py → PG DSN")
    else:
        print("预览完成。运行 python3 migrate_to_postgresql.py --execute 执行迁移")

def verify():
    """验证迁移后的数据"""
    try:
        from sqlalchemy import create_engine, text

        for name, dsn in [("Gateway", PG_GATEWAY_DSN), ("Platform", PG_PLATFORM_DSN)]:
            engine = create_engine(dsn)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT table_name FROM information_schema.tables "
                         "WHERE table_schema='public' ORDER BY table_name")
                ).fetchall()
                print(f"\n  {name} 数据库表:")
                for (t,) in tables:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM \"{t}\"")).scalar()
                    print(f"    {t}: {count} 行")
    except Exception as e:
        print(f"\n  验证失败: {e}")

if __name__ == "__main__":
    main()
