"""
用户平台 SQLAlchemy 2.0 异步 ORM 模型

与 database.py 中的表结构完全对应，用于 SQLAdmin 管理面板和未来异步迁移。
"""
from typing import Optional

from sqlalchemy import String, Integer, Float, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ========== 用户 ==========

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    gw_client_id: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user")
    api_keys: Mapped[list["UserApiKey"]] = relationship(back_populates="user")
    chat_histories: Mapped[list["ChatHistory"]] = relationship(back_populates="user")


# ========== 会话 ==========

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String, nullable=False)
    ip: Mapped[str] = mapped_column(String, default="")
    user_agent: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


# ========== 用户API密钥 ==========

class UserApiKey(Base):
    __tablename__ = "user_api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    gw_client_id: Mapped[str] = mapped_column(String, nullable=False)
    gw_key_id: Mapped[str] = mapped_column(String, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(String, default="")

    user: Mapped["User"] = relationship(back_populates="api_keys")


# ========== 对话历史 ==========

class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, default="")
    messages: Mapped[str] = mapped_column(Text, default="[]")
    model: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped["User"] = relationship(back_populates="chat_histories")


# ========== 请求日志 ==========

class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    key_id: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    is_stream: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String, default="success")
    error_msg: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, default="")
    completed_at: Mapped[str] = mapped_column(String, default="")
    latency_us: Mapped[int] = mapped_column(Integer, default=0)


# ========== 邮箱验证 ==========

class EmailVerification(Base):
    __tablename__ = "email_verification"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


# ========== 用量缓存 ==========

class UsageCache(Base):
    __tablename__ = "usage_cache"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    success_requests: Mapped[int] = mapped_column(Integer, default=0)
    error_requests: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0)
    last_synced_at: Mapped[str] = mapped_column(String, nullable=False)


# ========== 平台设置 ==========

class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# ========== 平台审计 ==========

class PlatformAudit(Base):
    __tablename__ = "platform_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
