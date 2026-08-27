"""
邮件服务 - 验证码发送

配置（从环境变量读取）：
- SMTP_HOST: 从环境变量 SMTP_HOST 读取
- SMTP_PORT: 465 (SSL)
- SMTP_USER: 从环境变量 SMTP_USER 读取
- SMTP_PASSWORD: 从环境变量读取
- From头部使用 formataddr(('AQUA', SMTP_USER)) 符合RFC5322
"""
import asyncio
import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

from app.database import utcnow

logger = logging.getLogger("aqua.email")


SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


def _build_verification_email(to_email: str, code: str, purpose: str) -> EmailMessage:
    """构建验证码邮件"""
    msg = EmailMessage()
    msg["From"] = formataddr(("AQUA", SMTP_USER))
    msg["To"] = to_email

    if purpose == "register":
        msg["Subject"] = "【AQUA平台】注册验证码"
        body_text = f"""您好！

您正在注册AQUA AI平台账户，验证码为：

    {code}

验证码有效期为10分钟，请尽快完成注册。

如果不是您本人操作，请忽略此邮件。

—— AQUA AI平台
"""
    elif purpose == "reset_password":
        msg["Subject"] = "【AQUA平台】重置密码验证码"
        body_text = f"""您好！

您正在重置AQUA平台账户密码，验证码为：

    {code}

验证码有效期为10分钟，请尽快完成密码重置。

如果不是您本人操作，请立即登录账户并修改密码。

—— AQUA AI平台
"""
    else:
        msg["Subject"] = "【AQUA平台】验证码"
        body_text = f"您的验证码为：{code}\n\n验证码有效期为10分钟。"

    # HTML版本（深色主题）
    html_content = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#1a1a2e;color:#e0e0e0;font-family:sans-serif;padding:20px;">
  <div style="max-width:500px;margin:0 auto;background:#16213e;padding:30px;border-radius:8px;">
    <h2 style="color:#00d4ff;margin-top:0;">AQUA AI平台</h2>
    <p>您好！</p>
    <p>您的验证码为：</p>
    <div style="text-align:center;margin:20px 0;">
      <span style="font-size:32px;font-weight:bold;color:#00d4ff;
                   background:#0f3460;padding:15px 30px;border-radius:6px;
                   letter-spacing:5px;">{code}</span>
    </div>
    <p style="color:#888;font-size:14px;">验证码有效期为10分钟，请尽快使用。</p>
    <hr style="border:none;border-top:1px solid #0f3460;margin:20px 0;">
    <p style="color:#666;font-size:12px;">如果不是您本人操作，请忽略此邮件。<br>—— AQUA AI平台</p>
  </div>
</body></html>
"""
    msg.set_content(body_text)
    msg.add_alternative(html_content, subtype="html")
    return msg


def send_verification_code_sync(to_email: str, code: str, purpose: str) -> bool:
    """同步发送验证码邮件"""
    try:
        msg = _build_verification_email(to_email, code, purpose)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP 认证失败，请检查用户名和密码")
        return False
    except smtplib.SMTPConnectError as e:
        logger.error(f"SMTP 连接失败: {e}")
        return False
    except smtplib.SMTPSenderRefused as e:
        logger.error(f"SMTP 发件人被拒: {e}")
        return False
    except smtplib.SMTPRecipientsRefused as e:
        logger.error(f"SMTP 收件人被拒: {e}")
        return False
    except TimeoutError:
        logger.error("SMTP 连接超时")
        return False
    except Exception as e:
        logger.error(f"邮件发送未知错误: {type(e).__name__}: {e}")
        return False


async def send_verification_code(to_email: str, code: str, purpose: str) -> bool:
    """异步发送验证码邮件（get_running_loop：3.12起协程内 get_event_loop 已废弃）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, send_verification_code_sync, to_email, code, purpose
    )


def generate_code() -> str:
    """生成6位数字验证码"""
    import secrets
    return str(secrets.randbelow(900000) + 100000)
