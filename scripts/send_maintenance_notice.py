"""
向所有已注册用户发送系统维护通知邮件
分批发送（每批50封，间隔60秒），避免QQ邮箱SMTP限频
"""
import smtplib
import time
import os
import sys
from email.message import EmailMessage
from email.utils import formataddr

# ===== SMTP 配置 =====
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# ===== 数据库配置 =====
PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "aqua"
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")
PG_DB = "aqua_platform"

BATCH_SIZE = 50
BATCH_INTERVAL = 60  # 秒
MAX_RETRIES = 3

def send_email(to_email: str) -> bool:
    """发送单封维护通知邮件（每封独立连接，避免QQ SMTP限频）"""
    msg = EmailMessage()
    msg["From"] = formataddr(("AQUA AI平台", SMTP_USER))
    msg["To"] = to_email
    msg["Subject"] = "【AQUA平台】系统升级维护通知"

    body_text = f"""您好！

为了提升服务质量和安全性，AQUA平台将于近期进行系统升级维护。

维护时间：约 1 小时
维护期间，平台所有服务将暂停，届时您将无法访问平台页面及API服务。

维护内容：
• 系统安全加固（密码加密存储、配置安全校验）
• 性能优化（数据库异步化、连接池管理）
• 代码架构优化（模块拆分、错误处理完善）

维护完成后，平台将恢复正常使用，带来更稳定、更安全的服务体验。

给您带来的不便敬请谅解，感谢您的理解与 support！

—— AQUA AI平台
your-domain.com"""

    msg.set_content(body_text)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # 每封邮件独立创建连接，避免QQ SMTP单连接发送限制
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
            return True
        except smtplib.SMTPAuthenticationError:
            print(f"[ERROR] SMTP 认证失败！请检查 SMTP_PASSWORD 环境变量")
            if 'server' in locals():
                server.quit()
            return False
        except Exception as e:
            print(f"[WARN] 发送失败 (尝试 {attempt}/{MAX_RETRIES}): {to_email} - {e}")
            try:
                if 'server' in locals():
                    server.quit()
            except:
                pass
            if attempt < MAX_RETRIES:
                time.sleep(3)
    return False

def get_all_users():
    """从数据库获取所有用户邮箱"""
    import psycopg2
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=PG_DB
    )
    cur = conn.cursor()
    cur.execute("SELECT id, username, email FROM users ORDER BY id")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

def main():
    if not SMTP_PASSWORD:
        print("[FATAL] 环境变量 SMTP_PASSWORD 未设置！")
        print("请执行: export SMTP_PASSWORD='你的QQ邮箱SMTP密码'")
        sys.exit(1)

    print("=" * 60)
    print("  AQUA 平台 - 维护通知邮件发送工具")
    print("=" * 60)

    # 获取用户
    users = get_all_users()
    total = len(users)
    print(f"\n[INFO] 共 {total} 名用户")
    print(f"[INFO] 分批发送：每批 {BATCH_SIZE} 封，间隔 {BATCH_INTERVAL} 秒")
    print(f"[INFO] 预计总耗时：约 {total // BATCH_SIZE * BATCH_INTERVAL // 60} 分钟\n")

    # 统计
    success_count = 0
    fail_count = 0
    fail_list = []

    # 分批发送
    for i in range(0, total, BATCH_SIZE):
        batch = users[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n--- 第 {batch_num}/{total_batches} 批 ({len(batch)} 封) ---")

        for user_id, username, email in batch:
            ok = send_email(email)
            if ok:
                success_count += 1
                print(f"  [OK] ID={user_id} {username} <{email}>")
            else:
                fail_count += 1
                fail_list.append((user_id, username, email))
                print(f"  [FAIL] ID={user_id} {username} <{email}>")

        # 最后一批不用等待
        if i + BATCH_SIZE < total:
            print(f"  等待 {BATCH_INTERVAL} 秒后发送下一批...")
            time.sleep(BATCH_INTERVAL)

    # 结果汇总
    print("\n" + "=" * 60)
    print("  发送完成")
    print(f"  成功：{success_count} 封")
    print(f"  失败：{fail_count} 封")
    if fail_list:
        print("\n  失败列表：")
        for user_id, username, email in fail_list:
            print(f"    ID={user_id} {username} <{email}>")
    print("=" * 60)

if __name__ == "__main__":
    main()
