"""
用户平台安全模块 - 密码哈希、会话令牌、CSRF

- bcrypt密码哈希（12 rounds）
- 32位hex会话ID
- 16位hex CSRF令牌
"""
import uuid
import secrets

import bcrypt


def hash_password(password: str) -> str:
    """使用bcrypt哈希密码（12 rounds）"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码与bcrypt哈希是否匹配"""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def generate_session_id() -> str:
    """生成32位随机hex会话ID"""
    return secrets.token_hex(32)


def generate_csrf_token() -> str:
    """生成16位随机hex CSRF令牌"""
    return secrets.token_hex(16)


def generate_uuid() -> str:
    """生成UUID4字符串"""
    return str(uuid.uuid4())
