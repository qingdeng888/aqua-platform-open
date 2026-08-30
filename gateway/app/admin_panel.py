"""
网关 SQLAdmin 管理面板

提供数据库表的 Web 管理界面，挂载于 /gw/dbadmin。
使用与 admin_api.py 相同的管理员密码进行认证。
"""
import hmac
import logging
import os

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.db_async import async_engine
from app.models import (
    AdminSetting,
    UpstreamKey,
    Proxy,
    Client,
    ClientApiKey,
    RequestLog,
    AuditLog,
    KeyUsageStat,
    CommercialDetection,
    BucketSnapshot,
)


# ========== 认证后端 ==========

class AdminAuth(AuthenticationBackend):
    """SQLAdmin 认证：ACU_ADMIN_PASSWORD 明文恒定时间比较（与控制台 admin_api.py 同一变量）

    v10.1修复：此前空密码可命中未配置的空环境变量（"" == ""）导致空密码登录；
    现未配置时拒绝一切登录（面板禁用，见 create_admin 的 [FATAL] 日志）。
    """

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
        if not isinstance(password, str):
            password = ""
        pw_plain = os.environ.get("ACU_ADMIN_PASSWORD", "")
        # 空值必须先挡：compare_digest(b"", b"") 为真，不挡就是"未配置即空密码可登录"
        if not pw_plain:
            return False
        if hmac.compare_digest(password.encode("utf-8"), pw_plain.encode("utf-8")):
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)


# ========== 模型视图配置 ==========

class AdminSettingView(ModelView, model=AdminSetting):
    name = "管理设置"
    name_plural = "管理设置"
    icon = "fa-solid fa-gear"
    # v10.1修复：value 列可能含 upstream_master_key/gateway_secret 等敏感值 → 列表/详情/表单均不展示
    column_list = [AdminSetting.key, AdminSetting.updated_at]
    column_searchable_list = [AdminSetting.key]
    column_details_exclude_list = [AdminSetting.value]
    form_excluded_columns = [AdminSetting.value]
    can_delete = True


class UpstreamKeyView(ModelView, model=UpstreamKey):
    name = "上游密钥"
    name_plural = "上游密钥"
    icon = "fa-solid fa-key"
    column_list = [
        UpstreamKey.id, UpstreamKey.name, UpstreamKey.provider,
        UpstreamKey.key_prefix, UpstreamKey.weight, UpstreamKey.rpm_limit,
        UpstreamKey.switch_threshold, UpstreamKey.status,
        UpstreamKey.created_at, UpstreamKey.updated_at,
    ]
    column_searchable_list = [UpstreamKey.name, UpstreamKey.provider]
    column_sortable_list = [UpstreamKey.name, UpstreamKey.weight, UpstreamKey.status]
    form_excluded_columns = [UpstreamKey.api_key_ciphertext]
    # v10.1修复：详情页排除密钥密文，防密钥材料泄漏
    column_details_exclude_list = [UpstreamKey.api_key_ciphertext]
    can_delete = True


class ProxyView(ModelView, model=Proxy):
    name = "代理池"
    name_plural = "代理池"
    icon = "fa-solid fa-network-wired"
    column_list = [
        Proxy.id, Proxy.name, Proxy.scheme, Proxy.host, Proxy.port,
        Proxy.username, Proxy.status, Proxy.last_check_at, Proxy.last_check_ok,
        Proxy.created_at, Proxy.updated_at,
    ]
    column_searchable_list = [Proxy.name, Proxy.host]
    column_sortable_list = [Proxy.name, Proxy.scheme, Proxy.status]
    # 代理密码密文不进表单与详情页，防凭据泄漏
    form_excluded_columns = [Proxy.password_ciphertext]
    column_details_exclude_list = [Proxy.password_ciphertext]
    can_delete = True


class ClientView(ModelView, model=Client):
    name = "客户端"
    name_plural = "客户端"
    icon = "fa-solid fa-users"
    column_list = [Client.id, Client.name, Client.status, Client.created_at, Client.updated_at]
    column_searchable_list = [Client.name]
    can_delete = True


class ClientApiKeyView(ModelView, model=ClientApiKey):
    name = "客户端API密钥"
    name_plural = "客户端API密钥"
    icon = "fa-solid fa-id-badge"
    column_list = [
        ClientApiKey.id, ClientApiKey.client_id, ClientApiKey.key_prefix,
        ClientApiKey.status, ClientApiKey.created_at, ClientApiKey.last_used_at,
    ]
    column_searchable_list = [ClientApiKey.client_id]
    form_excluded_columns = [ClientApiKey.key_hash, ClientApiKey.key_ciphertext]
    # v10.1修复：详情页排除哈希/密文，防密钥材料泄漏
    column_details_exclude_list = [ClientApiKey.key_hash, ClientApiKey.key_ciphertext]
    can_delete = True


class RequestLogView(ModelView, model=RequestLog):
    name = "请求日志"
    name_plural = "请求日志"
    icon = "fa-solid fa-list"
    column_list = [
        RequestLog.id, RequestLog.client_id, RequestLog.upstream_key_id,
        RequestLog.model, RequestLog.status_code, RequestLog.latency_ms,
        RequestLog.total_tokens, RequestLog.is_stream, RequestLog.created_at,
    ]
    column_searchable_list = [RequestLog.model, RequestLog.client_id]
    column_sortable_list = [RequestLog.created_at, RequestLog.latency_ms, RequestLog.status_code]
    column_default_sort = ("created_at", True)  # 降序
    # 排除请求/响应体（含用户明文 prompt，详情页不应展示）
    column_details_exclude_list = [RequestLog.request_body, RequestLog.response_body]
    can_create = False
    can_edit = False
    can_delete = True


class AuditLogView(ModelView, model=AuditLog):
    name = "审计日志"
    name_plural = "审计日志"
    icon = "fa-solid fa-clipboard"
    column_list = [
        AuditLog.id, AuditLog.operator, AuditLog.action,
        AuditLog.target_type, AuditLog.target_id, AuditLog.created_at,
    ]
    column_searchable_list = [AuditLog.action, AuditLog.operator]
    column_default_sort = ("created_at", True)
    can_create = False
    can_edit = False
    can_delete = True


class KeyUsageStatView(ModelView, model=KeyUsageStat):
    name = "密钥使用统计"
    name_plural = "密钥使用统计"
    icon = "fa-solid fa-chart-bar"
    column_list = [
        KeyUsageStat.key_id, KeyUsageStat.total_requests, KeyUsageStat.total_success,
        KeyUsageStat.total_failures, KeyUsageStat.avg_rt, KeyUsageStat.updated_at,
    ]
    column_searchable_list = [KeyUsageStat.key_id]
    can_create = False
    can_delete = True


class CommercialDetectionView(ModelView, model=CommercialDetection):
    name = "商用行为识别"
    name_plural = "商用行为识别"
    icon = "fa-solid fa-triangle-exclamation"
    column_list = [
        CommercialDetection.client_id, CommercialDetection.confidence_score,
        CommercialDetection.interval_cv, CommercialDetection.model_switch_count,
        CommercialDetection.admin_confirmed, CommercialDetection.last_updated,
    ]
    column_searchable_list = [CommercialDetection.client_id]
    can_create = False


class BucketSnapshotView(ModelView, model=BucketSnapshot):
    name = "桶状态快照"
    name_plural = "桶状态快照"
    icon = "fa-solid fa-bucket"
    column_list = [
        BucketSnapshot.id, BucketSnapshot.key_id, BucketSnapshot.model,
        BucketSnapshot.rpm, BucketSnapshot.health_score, BucketSnapshot.captured_at,
    ]
    column_searchable_list = [BucketSnapshot.key_id, BucketSnapshot.model]
    column_default_sort = ("captured_at", True)
    can_create = False
    can_edit = False
    can_delete = True


# ========== 创建 Admin 实例 ==========

def create_admin(app=None) -> Admin:
    """创建 SQLAdmin 实例并注册所有模型"""
    # v10.1修复：未配置管理凭据时拒绝一切登录，启动时以 [FATAL] 明示面板已禁用
    if not os.environ.get("ACU_ADMIN_PASSWORD"):
        logging.getLogger("acu.gateway").error(
            "[FATAL] 未配置 ACU_ADMIN_PASSWORD，"
            "SQLAdmin 数据库面板(/gw/dbadmin)已禁用所有登录"
        )
    authentication_backend = AdminAuth(secret_key=os.environ.get("ADMIN_SESSION_SECRET", ""))
    admin = Admin(
        app=app,
        engine=async_engine,
        authentication_backend=authentication_backend,
        title="AQUA 网关数据库管理",
        base_url="/gw/dbadmin",
    )

    admin.add_view(AdminSettingView)
    admin.add_view(UpstreamKeyView)
    admin.add_view(ProxyView)
    admin.add_view(ClientView)
    admin.add_view(ClientApiKeyView)
    admin.add_view(RequestLogView)
    admin.add_view(AuditLogView)
    admin.add_view(KeyUsageStatView)
    admin.add_view(CommercialDetectionView)
    admin.add_view(BucketSnapshotView)

    return admin
