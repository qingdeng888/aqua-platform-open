"""管理员密码校验单元测试（gateway/app/admin_api.py 的 _verify_admin_password，不连库不联网）

只有一种配置方式：ACU_ADMIN_PASSWORD 明文写进 .env，恒定时间比较。

覆盖：
- 正确密码通过、错误密码拒绝
- 不做 strip / 大小写归一：配置里写什么就必须一字不差地输入什么
- 非 ASCII 与含 `$` 的口令可用（明文不含 bcrypt 哈希的 compose 插值截断问题）
- 空密码守卫：配置为空时任何输入都拒绝（compare_digest(b"", b"") 为真的坑）
- 契约守卫：模块级缺失变量时报错文案要点名 ACU_ADMIN_PASSWORD
- 契约守卫：登录端点必须走统一校验函数；仓库不得再残留 bcrypt 哈希方案
"""
import os
import re
from pathlib import Path

import pytest

# 导入链上两处模块级强校验：database 要 PG_PASSWORD、admin_api 要管理员密码。
# 单测不连库不登录，仅提供占位值满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")
os.environ.setdefault("ACU_ADMIN_PASSWORD", "unit-test-placeholder")

from app import admin_api  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_MODULE_SRC = (_REPO / "gateway" / "app" / "admin_api.py").read_text(encoding="utf-8")
_PANEL_SRC = (_REPO / "gateway" / "app" / "admin_panel.py").read_text(encoding="utf-8")

_PW = "unit-test-admin-pw"


def _set_password(monkeypatch, value: str) -> None:
    """直接改模块级常量：该值在导入时读一次，运行中不再读环境变量"""
    monkeypatch.setattr(admin_api, "ADMIN_PASSWORD", value)


class TestVerify:
    """明文恒定时间比较"""

    def test_correct_password_passes(self, monkeypatch):
        _set_password(monkeypatch, _PW)
        assert admin_api._verify_admin_password(_PW) is True

    @pytest.mark.parametrize("wrong", ["", "x", _PW + " ", " " + _PW, _PW.upper(), _PW[:-1]])
    def test_wrong_password_rejected(self, monkeypatch, wrong):
        # 不做 strip/大小写归一：配置里写什么就必须一字不差地输入什么
        _set_password(monkeypatch, _PW)
        assert admin_api._verify_admin_password(wrong) is False

    def test_non_ascii_password(self, monkeypatch):
        _set_password(monkeypatch, "中文密码-α-🔑")
        assert admin_api._verify_admin_password("中文密码-α-🔑") is True
        assert admin_api._verify_admin_password("中文密码") is False

    def test_dollar_sign_password_works(self, monkeypatch):
        # 明文方案的卖点之一：含 $ 也能用（bcrypt 哈希必含 $，才有 compose 插值截断的坑）
        _set_password(monkeypatch, "p$a$$w0rd")
        assert admin_api._verify_admin_password("p$a$$w0rd") is True

    def test_empty_config_rejects_everything(self, monkeypatch):
        # compare_digest(b"", b"") 为真，若不挡空值就是"未配置即空密码可登录"
        _set_password(monkeypatch, "")
        assert admin_api._verify_admin_password("") is False
        assert admin_api._verify_admin_password("anything") is False


class TestContract:
    """源码契约守卫"""

    def test_fatal_message_names_the_variable(self):
        # 启动失败时用户要能一眼看到该配哪个变量
        m = re.search(r'raise RuntimeError\((.*?)\)\n', _MODULE_SRC, re.S)
        assert m, "未找到模块级管理员密码守卫"
        assert "ACU_ADMIN_PASSWORD=" in m.group(1)

    def test_verify_used_by_login_endpoint(self):
        # 登录端点必须走统一校验函数，不得内联比较（防两处逻辑分叉）
        assert "if not _verify_admin_password(req.password):" in _MODULE_SRC

    @pytest.mark.parametrize("src_name", ["admin_api.py", "admin_panel.py"])
    def test_no_bcrypt_hash_scheme_left(self, src_name):
        # 单一方案：两个登录口都不得残留哈希分支（注释里为解释取舍而提及 bcrypt 不算）
        text = _MODULE_SRC if src_name == "admin_api.py" else _PANEL_SRC
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        for token in ("import bcrypt", "bcrypt.checkpw", "ACU_ADMIN_PASSWORD_HASH"):
            assert token not in code, f"{src_name} 仍残留 {token}"

    def test_bcrypt_dropped_from_requirements(self):
        reqs = (_REPO / "gateway" / "requirements.txt").read_text(encoding="utf-8")
        assert "bcrypt" not in reqs

    def test_panel_shares_the_same_variable(self):
        # 两个登录口读同一个变量，行为天然一致；面板同样要挡空值
        assert 'os.environ.get("ACU_ADMIN_PASSWORD", "")' in _PANEL_SRC
        assert "if not pw_plain:" in _PANEL_SRC
        assert "hmac.compare_digest" in _PANEL_SRC
