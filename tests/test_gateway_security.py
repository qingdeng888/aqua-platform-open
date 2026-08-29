"""
Gateway security 模块纯单元测试（不依赖数据库）

覆盖：gateway/app/security.py
- create_admin_token / verify_admin_token（有效、过期、篡改、角色错误、密钥错误、格式非法）
- encrypt_upstream_key / decrypt_upstream_key 往返
- encrypt_secret / decrypt_secret（客户端）往返
- encrypt_proxy_secret / decrypt_proxy_secret（代理凭据）往返
- 上游/客户端/代理三条 HKDF 派生路径互相隔离（不同 salt 派生不同 key）
"""
import base64
import hashlib
import hmac
import json
import time

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.security import (
    create_admin_token,
    decrypt_proxy_secret,
    decrypt_secret,
    decrypt_upstream_key,
    encrypt_proxy_secret,
    encrypt_secret,
    encrypt_upstream_key,
    generate_upstream_master_key,
    verify_admin_token,
)


def _forge_token(payload: dict, secret: str) -> str:
    """按 create_admin_token 的签名格式伪造任意 payload（用于负向用例）"""
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


class TestAdminToken:
    """管理员 Token（HMAC-SHA256，24h 有效）"""

    def test_valid_token_roundtrip(self):
        secret = "unit-test-secret"
        token = create_admin_token(secret)
        payload = verify_admin_token(token, secret)
        assert payload is not None
        assert payload["role"] == "admin"
        assert payload["exp"] - payload["iat"] == 86400  # 24 小时

    def test_expired_token_rejected(self):
        secret = "unit-test-secret"
        now = int(time.time())
        token = _forge_token(
            {"role": "admin", "iat": now - 90000, "exp": now - 3600}, secret
        )
        assert verify_admin_token(token, secret) is None

    def test_tampered_signature_rejected(self):
        secret = "unit-test-secret"
        token = create_admin_token(secret)
        payload_b64, sig = token.split(".")
        tampered = f"{payload_b64}.{'0' * len(sig)}"  # 签名替换为全 0
        assert verify_admin_token(tampered, secret) is None

    def test_tampered_payload_rejected(self):
        secret = "unit-test-secret"
        now = int(time.time())
        # 用自己的签名"合法地"包一个伪造 payload，但用错误密钥签名 → 验签失败
        token = _forge_token({"role": "admin", "iat": now, "exp": now + 86400}, "wrong-secret")
        assert verify_admin_token(token, secret) is None

    def test_wrong_role_rejected(self):
        secret = "unit-test-secret"
        now = int(time.time())
        token = _forge_token({"role": "user", "iat": now, "exp": now + 86400}, secret)
        # 角色不是 admin：即使签名正确也必须拒绝
        assert verify_admin_token(token, secret) is None

    def test_wrong_secret_rejected(self):
        token = create_admin_token("secret-a")
        assert verify_admin_token(token, "secret-b") is None

    def test_malformed_token_rejected(self):
        secret = "unit-test-secret"
        for bad in ("", "abc", "a.b.c", "not-base64.!!", "...."):
            assert verify_admin_token(bad, secret) is None


class TestUpstreamKeyEncryption:
    """上游密钥加密（HKDF salt=acu-upstream-key-derivation）"""

    def test_master_key_format(self):
        key = generate_upstream_master_key()
        raw = base64.b64decode(key)
        assert len(raw) == 32  # 256-bit

    def test_roundtrip(self):
        master = generate_upstream_master_key()
        plaintext = "nvapi-fake-upstream-key-12345"
        ct = encrypt_upstream_key(plaintext, master)
        assert ct != plaintext  # 确实经过加密
        assert decrypt_upstream_key(ct, master) == plaintext

    def test_wrong_master_key_fails(self):
        ct = encrypt_upstream_key("secret", generate_upstream_master_key())
        with pytest.raises(InvalidToken):
            decrypt_upstream_key(ct, generate_upstream_master_key())


class TestClientSecretEncryption:
    """客户端密钥加密（HKDF salt=acu-client-key-derivation）"""

    def test_roundtrip(self):
        master = generate_upstream_master_key()
        plaintext = "sk-testclientsecret000000000000000000"
        ct = encrypt_secret(plaintext, master)
        assert ct != plaintext
        assert decrypt_secret(ct, master) == plaintext


class TestProxySecretEncryption:
    """代理凭据加密（HKDF salt=acu-proxy-credential-derivation）"""

    def test_roundtrip(self):
        master = generate_upstream_master_key()
        plaintext = "p@ss w:rd/特殊字符"
        ct = encrypt_proxy_secret(plaintext, master)
        assert ct != plaintext
        assert decrypt_proxy_secret(ct, master) == plaintext

    def test_wrong_master_key_fails(self):
        ct = encrypt_proxy_secret("proxy-pass", generate_upstream_master_key())
        with pytest.raises(InvalidToken):
            decrypt_proxy_secret(ct, generate_upstream_master_key())


class TestDerivationIsolation:
    """上游/客户端/代理三条派生路径必须互相隔离：不同 salt → 不同 Fernet key → 互解必败"""

    def test_upstream_ciphertext_not_client_decryptable(self):
        master = generate_upstream_master_key()
        ct = encrypt_upstream_key("upstream-secret", master)
        with pytest.raises(InvalidToken):
            decrypt_secret(ct, master)  # 客户端路径解上游密文 → InvalidToken

    def test_client_ciphertext_not_upstream_decryptable(self):
        master = generate_upstream_master_key()
        ct = encrypt_secret("client-secret", master)
        with pytest.raises(InvalidToken):
            decrypt_upstream_key(ct, master)  # 上游路径解客户端密文 → InvalidToken

    def test_proxy_ciphertext_not_decryptable_by_others(self):
        master = generate_upstream_master_key()
        ct = encrypt_proxy_secret("proxy-secret", master)
        with pytest.raises(InvalidToken):
            decrypt_upstream_key(ct, master)
        with pytest.raises(InvalidToken):
            decrypt_secret(ct, master)

    def test_others_ciphertext_not_proxy_decryptable(self):
        master = generate_upstream_master_key()
        with pytest.raises(InvalidToken):
            decrypt_proxy_secret(encrypt_upstream_key("u", master), master)
        with pytest.raises(InvalidToken):
            decrypt_proxy_secret(encrypt_secret("c", master), master)
