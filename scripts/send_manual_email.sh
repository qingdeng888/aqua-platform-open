#!/bin/bash
# AQUA 平台维护通知邮件 - 备用发送脚本
# 使用方法：
#   1. 登录 https://mail.qq.com → 设置 → 账户 → 生成授权码
#   2. 执行: bash send_manual_email.sh <新的SMTP授权码>
#
# 邮件内容已经生成在 emails.txt 中，也可手动复制发送

SMTP_USER="${SMTP_USER}"
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PASSWORD="${1:-}"
BATCH_SIZE=30
BATCH_INTERVAL=30

if [ -z "$SMTP_PASSWORD" ]; then
    echo "错误：请提供 SMTP 授权码作为参数"
    echo "用法: $0 <SMTP授权码>"
    exit 1
fi

if [ -z "$SMTP_HOST" ]; then
    echo "错误：请设置 SMTP_HOST 环境变量"
    exit 1
fi

if [ -z "$SMTP_USER" ]; then
    echo "错误：请设置 SMTP_USER 环境变量"
    exit 1
fi

# 生成邮件列表
echo "正在获取用户列表..."
PGPASSWORD=${PG_PASSWORD} psql -h localhost -U aqua -d aqua_platform \
    -t -A -F ',' \
    -c "SELECT id, username, email FROM users ORDER BY id" \
    > /tmp/email_list.csv 2>/dev/null

TOTAL=$(wc -l < /tmp/email_list.csv)
echo "共 $TOTAL 名用户"

# 生成邮件内容模板
cat > /tmp/email_body.txt << 'EMAILEOF'
您好！

为了提升服务质量和安全性，AQUA平台正在进行系统升级维护。

维护期间，平台所有服务将暂停约1小时，届时您将无法访问平台页面及API服务。

维护完成后，平台将恢复正常使用，带来更稳定、更安全的服务体验。

给您带来的不便敬请谅解，感谢您的理解与支持！

—— AQUA AI平台
your-domain.com
EMAILEOF

echo "===== 邮件内容 ====="
echo "主题：【AQUA平台】系统维护通知"
cat /tmp/email_body.txt
echo "===================="

# 创建保存目录
mkdir -p ./backups/email_records
EMAIL_RECORD="./backups/email_records/sent_$(date +%Y%m%d_%H%M%S).csv"
echo "user_id,username,email,status,time" > "$EMAIL_RECORD"

COUNT=0
SUCCESS=0
FAIL=0

while IFS=',' read -r user_id username email; do
    COUNT=$((COUNT + 1))
    
    echo "[$COUNT/$TOTAL] 发送至 $email ..."
    
    # 使用 Python 发送单封邮件
    python3 -c "
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
import sys

try:
    msg = EmailMessage()
    msg['From'] = formataddr(('AQUA AI平台', '$SMTP_USER'))
    msg['To'] = '$email'
    msg['Subject'] = '【AQUA平台】系统维护通知'
    
    with open('/tmp/email_body.txt') as f:
        body = f.read()
    msg.set_content(body)
    
    server = smtplib.SMTP_SSL('$SMTP_HOST', 465, timeout=30)
    server.login('$SMTP_USER', '$SMTP_PASSWORD')
    server.send_message(msg)
    server.quit()
    sys.exit(0)
except Exception as e:
    print(f'FAIL: {e}', file=sys.stderr)
    sys.exit(1)
" 2>>/tmp/email_error.log

    if [ $? -eq 0 ]; then
        echo "  ✓ 成功"
        echo "$user_id,$username,$email,OK,$(date +%H:%M:%S)" >> "$EMAIL_RECORD"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "  ✗ 失败"
        echo "$user_id,$username,$email,FAIL,$(date +%H:%M:%S)" >> "$EMAIL_RECORD"
        FAIL=$((FAIL + 1))
    fi
    
    # 每批间隔
    if [ $((COUNT % BATCH_SIZE)) -eq 0 ] && [ $COUNT -lt $TOTAL ]; then
        echo "  等待 ${BATCH_INTERVAL} 秒..."
        sleep $BATCH_INTERVAL
    fi
    
    # 每封邮件间隔 2 秒
    sleep 2
done < /tmp/email_list.csv

echo ""
echo "===== 发送完成 ====="
echo "总计: $TOTAL, 成功: $SUCCESS, 失败: $FAIL"
echo "记录文件: $EMAIL_RECORD"
