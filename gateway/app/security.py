"""
安全模块 - 加密、认证、令牌管理

沿用v6的Fernet+HKDF-SHA256加密体系：
- 上游密钥加密：salt=acu-upstream-key-derivation
- 客户端密钥加密：salt=acu-client-key-derivation
- 代理凭据加密：salt=acu-proxy-credential-derivation
- 管理员Token：HMAC-SHA256，24小时有效
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import time
from typing import Optional

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.fernet import Fernet


# ========== 上游密钥加密 ==========

def generate_upstream_master_key() -> str:
    """生成上游密钥主密钥，返回base64编码"""
    raw = os.urandom(32)
    return base64.b64encode(raw).decode("utf-8")


def _derive_fernet_key(master_key_b64: str) -> bytes:
    """从主密钥派生上游Fernet密钥（HKDF-SHA256）"""
    raw = base64.b64decode(master_key_b64)
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"acu-upstream-key-derivation",
        info=b"acu-upstream-fernet-key",
    )
    return base64.urlsafe_b64encode(hkdf.derive(raw))


def encrypt_upstream_key(plaintext: str, master_key_b64: str) -> str:
    """加密上游API密钥"""
    f = Fernet(_derive_fernet_key(master_key_b64))
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_upstream_key(ciphertext: str, master_key_b64: str) -> str:
    """解密上游API密钥"""
    f = Fernet(_derive_fernet_key(master_key_b64))
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# ========== 客户端密钥加密 ==========

def generate_client_key() -> str:
    """生成下游客户端API Key，格式: sk- + 32随机字符（兼容OpenAI标准格式，旧acu_密钥仍可用）"""
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(32))
    return f"sk-{random_part}"


def _derive_client_fernet_key(master_key_b64: str) -> bytes:
    """从主密钥派生客户端Fernet密钥（不同salt隔离）"""
    raw = base64.b64decode(master_key_b64)
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"acu-client-key-derivation",
        info=b"acu-client-fernet-key",
    )
    return base64.urlsafe_b64encode(hkdf.derive(raw))


def encrypt_secret(plaintext: str, master_key_b64: str) -> str:
    """加密客户端密钥"""
    f = Fernet(_derive_client_fernet_key(master_key_b64))
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str, master_key_b64: str) -> str:
    """解密客户端密钥"""
    f = Fernet(_derive_client_fernet_key(master_key_b64))
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# ========== 代理凭据加密 ==========

def _derive_proxy_fernet_key(master_key_b64: str) -> bytes:
    """从主密钥派生代理凭据Fernet密钥（独立salt，与上游/客户端两条路径互不通解）"""
    raw = base64.b64decode(master_key_b64)
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"acu-proxy-credential-derivation",
        info=b"acu-proxy-fernet-key",
    )
    return base64.urlsafe_b64encode(hkdf.derive(raw))


def encrypt_proxy_secret(plaintext: str, master_key_b64: str) -> str:
    """加密代理密码"""
    f = Fernet(_derive_proxy_fernet_key(master_key_b64))
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_proxy_secret(ciphertext: str, master_key_b64: str) -> str:
    """解密代理密码"""
    f = Fernet(_derive_proxy_fernet_key(master_key_b64))
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# ========== 哈希与脱敏 ==========

def hash_secret(secret: str) -> str:
    """SHA-256哈希"""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mask_secret(secret: str) -> str:
    """脱敏显示：前4位 + *** + 后4位"""
    if len(secret) <= 8:
        return secret[:2] + "***" + secret[-2:]
    return secret[:4] + "***" + secret[-4:]


# ========== 管理员Token ==========

def create_admin_token(secret: str) -> str:
    """创建管理员Token（HMAC-SHA256签名，24小时有效）"""
    payload = {
        "role": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400,
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_admin_token(token: str, secret: str) -> Optional[dict]:
    """验证管理员Token，返回payload或None"""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, signature = parts
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None
        if payload.get("role") != "admin":
            return None
        return payload
    except Exception:
        return None
