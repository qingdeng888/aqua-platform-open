"""
用户平台 SQLAdmin 管理面板

提供数据库表的 Web 管理界面，挂载于 /platform/dbadmin。
密码契约（与网关/平台管理后台一致）：
优先 ACU_ADMIN_PASSWORD_HASH(bcrypt)，否则 ACU_ADMIN_PASSWORD(constant-time比较)；
两者均未配置时拒绝一切登录。
"""
import hmac
import logging
import os

import bcrypt
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.db_async import async_engine

logger = logging.getLogger("aqua.admin_panel")

_ADMIN_PASSWORD_HASH = os.environ.get("ACU_ADMIN_PASSWORD_HASH", "")
_ADMIN_PASSWORD = os.environ.get("ACU_ADMIN_PASSWORD", "")
if not _ADMIN_PASSWORD_HASH and not _ADMIN_PASSWORD:
    logger.critical(
        "[FATAL] ACU_ADMIN_PASSWORD_HASH 与 ACU_ADMIN_PASSWORD 均未配置，SQLAdmin 面板将拒绝一切登录！"
    )


def _verify_admin_password(password: str) -> bool:
    """管理密码校验：优先bcrypt哈希，否则constant-time明文比较；均未配置/空密码一律拒绝"""
    if not password:
        return False
    if _ADMIN_PASSWORD_HASH:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), _ADMIN_PASSWORD_HASH.encode("utf-8"))
        except (ValueError, TypeError):
            return False
    if _ADMIN_PASSWORD:
        return hmac.compare_digest(password, _ADMIN_PASSWORD)
    return False
from app.models import (
    User,
    Session,
    UserApiKey,
    ChatHistory,
    RequestLog,
    EmailVerification,
    UsageCache,
    PlatformSetting,
    PlatformAudit,
)


# ========== 认证后端 ==========

class AdminAuth(AuthenticationBackend):
    """SQLAdmin 认证：与平台管理后台共用管理密码契约（HASH优先/constant-time/未配置拒绝）"""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        password = form.get("password", "")
        # 修复：原实现未配置密码时空密码即可登录（"" == ""）
        # bcrypt校验（12 rounds）经线程池执行，避免阻塞事件循环
        import asyncio
        if await asyncio.to_thread(_verify_admin_password, password):
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)


# ========== 模型视图配置 ==========

class UserView(ModelView, model=User):
    name = "用户"
    name_plural = "用户"
    icon = "fa-solid fa-user"
    column_list = [
        User.id, User.uuid, User.username, User.email,
        User.display_name, User.status, User.created_at,
    ]
    column_searchable_list = [User.username, User.email]
    column_sortable_list = [User.id, User.username, User.status, User.created_at]
    form_excluded_columns = [User.password_hash]
    can_delete = True


class SessionView(ModelView, model=Session):
    name = "会话"
    name_plural = "会话"
    icon = "fa-solid fa-clock"
    column_list = [
        Session.id, Session.user_id, Session.ip,
        Session.created_at, Session.expires_at,
    ]
    column_searchable_list = [Session.id, Session.ip]
    column_default_sort = ("created_at", True)
    can_create = False
    can_edit = False
    can_delete = True


class UserApiKeyView(ModelView, model=UserApiKey):
    name = "用户API密钥"
    name_plural = "用户API密钥"
    icon = "fa-solid fa-key"
    column_list = [
        UserApiKey.id, UserApiKey.user_id, UserApiKey.key_prefix,
        UserApiKey.label, UserApiKey.status, UserApiKey.created_at,
    ]
    column_searchable_list = [UserApiKey.key_prefix, UserApiKey.label]
    form_excluded_columns = [UserApiKey.api_key_encrypted]
    can_delete = True


class ChatHistoryView(ModelView, model=ChatHistory):
    name = "对话历史"
    name_plural = "对话历史"
    icon = "fa-solid fa-comments"
    column_list = [
        ChatHistory.id, ChatHistory.user_id, ChatHistory.title,
        ChatHistory.model, ChatHistory.created_at, ChatHistory.updated_at,
    ]
    column_searchable_list = [ChatHistory.title]
    column_default_sort = ("updated_at", True)
    form_excluded_columns = [ChatHistory.messages]
    can_delete = True


class RequestLogView(ModelView, model=RequestLog):
    name = "请求日志"
    name_plural = "请求日志"
    icon = "fa-solid fa-list"
    column_list = [
        RequestLog.id, RequestLog.user_id, RequestLog.key_id,
        RequestLog.model, RequestLog.latency_ms, RequestLog.status,
        RequestLog.total_tokens, RequestLog.created_at,
    ]
    column_searchable_list = [RequestLog.model, RequestLog.key_id]
    column_sortable_list = [RequestLog.created_at, RequestLog.latency_ms]
    column_default_sort = ("created_at", True)
    can_create = False
    can_edit = False
    can_delete = True


class EmailVerificationView(ModelView, model=EmailVerification):
    name = "邮箱验证"
    name_plural = "邮箱验证"
    icon = "fa-solid fa-envelope"
    column_list = [
        EmailVerification.id, EmailVerification.email, EmailVerification.purpose,
        EmailVerification.used, EmailVerification.expires_at, EmailVerification.created_at,
    ]
    column_searchable_list = [EmailVerification.email]
    can_create = False
    can_edit = False


class UsageCacheView(ModelView, model=UsageCache):
    name = "用量缓存"
    name_plural = "用量缓存"
    icon = "fa-solid fa-chart-pie"
    column_list = [
        UsageCache.user_id, UsageCache.date, UsageCache.model,
        UsageCache.total_requests, UsageCache.success_requests,
        UsageCache.avg_latency_ms, UsageCache.last_synced_at,
    ]
    column_searchable_list = [UsageCache.model]
    can_create = False
    can_edit = False


class PlatformSettingView(ModelView, model=PlatformSetting):
    name = "平台设置"
    name_plural = "平台设置"
    icon = "fa-solid fa-gear"
    column_list = [PlatformSetting.key, PlatformSetting.value]
    column_searchable_list = [PlatformSetting.key]
    can_delete = True


class PlatformAuditView(ModelView, model=PlatformAudit):
    name = "平台审计"
    name_plural = "平台审计"
    icon = "fa-solid fa-clipboard"
    column_list = [
        PlatformAudit.id, PlatformAudit.user_id, PlatformAudit.action,
        PlatformAudit.ip, PlatformAudit.created_at,
    ]
    column_searchable_list = [PlatformAudit.action]
    column_default_sort = ("created_at", True)
    can_create = False
    can_edit = False
    can_delete = True


# ========== 创建 Admin 实例 ==========

def create_admin(app=None) -> Admin:
    """创建 SQLAdmin 实例并注册所有模型"""
    _admin_secret = os.environ.get("ADMIN_SESSION_SECRET")
    if not _admin_secret:
        raise RuntimeError("[FATAL] 环境变量 ADMIN_SESSION_SECRET 未设置！")
    authentication_backend = AdminAuth(secret_key=_admin_secret)
    admin = Admin(
        app=app,
        engine=async_engine,
        authentication_backend=authentication_backend,
        title="AQUA 平台数据库管理",
        base_url="/platform/dbadmin",
    )

    admin.add_view(UserView)
    admin.add_view(SessionView)
    admin.add_view(UserApiKeyView)
    admin.add_view(ChatHistoryView)
    admin.add_view(RequestLogView)
    admin.add_view(EmailVerificationView)
    admin.add_view(UsageCacheView)
    admin.add_view(PlatformSettingView)
    admin.add_view(PlatformAuditView)

    return admin
