"""
代理池模块 (v12.1)

职责单一：把「上游密钥 → 出网通道」的解析与 httpx 客户端复用集中在一处。

- 代理协议：socks5 / socks5h / http / https，支持无认证与「用户名:密码」认证
- 上游密钥三种出网模式：
    direct  直连（默认，不经代理）
    bind    绑定代理池中指定代理
    rotate  在代理池活跃代理间轮询（round-robin）
- 热路径零 DB 查询：代理明文与 key→模式 绑定关系以 5 秒 TTL 快照缓存，
  管理端增删改后主动 invalidate() 立即失效
- 代理密码使用独立 HKDF salt 加密存储（见 security.encrypt_proxy_secret）
"""
import asyncio
import logging
import time
from typing import Optional
from urllib.parse import quote

import httpx

from app.database import fetch_all, get_setting
from app.security import decrypt_proxy_secret

logger = logging.getLogger("aqua.proxy_pool")

# 允许的代理协议（与 httpx.Proxy 支持的 scheme 集合一致）
ALLOWED_SCHEMES = ("socks5", "socks5h", "http", "https")

# 连接池上限（与调度器直连池保持同一口径）
POOL_MAX_CONNECTIONS = 100
POOL_MAX_KEEPALIVE = 20

# 出网模式
MODE_DIRECT = "direct"
MODE_BIND = "bind"
MODE_ROTATE = "rotate"
ALLOWED_MODES = (MODE_DIRECT, MODE_BIND, MODE_ROTATE)


def build_proxy_url(scheme: str, host: str, port: int,
                    username: str = "", password: str = "") -> str:
    """拼装代理 URL：scheme://[user:pass@]host:port

    用户名/密码做 percent-encoding，避免 @ : / 等字符破坏 URL 结构。
    """
    scheme = (scheme or "").strip().lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"不支持的代理协议: {scheme}")
    host = (host or "").strip()
    if not host:
        raise ValueError("代理地址不能为空")
    port = int(port)
    if not (1 <= port <= 65535):
        raise ValueError(f"代理端口非法: {port}")
    auth = ""
    if username:
        auth = quote(str(username), safe="")
        if password:
            auth += ":" + quote(str(password), safe="")
        auth += "@"
    return f"{scheme}://{auth}{host}:{port}"


def build_client(proxy_url: Optional[str], stream: bool,
                 max_connections: int = POOL_MAX_CONNECTIONS,
                 max_keepalive: int = POOL_MAX_KEEPALIVE) -> httpx.AsyncClient:
    """构造 httpx 异步客户端（直连或经代理），直连池与代理池共用此工厂

    - 非流式：timeout=120s（connect=10s），热路径均带 per-request timeout 覆盖
    - 流式：  timeout=600s（read=600s），大于 per-chunk 180s 空闲检测，保证优雅终止
    """
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
        keepalive_expiry=60,
    )
    timeout = (
        httpx.Timeout(600.0, connect=10.0, read=600.0) if stream
        else httpx.Timeout(120.0, connect=10.0)
    )
    kwargs = {"timeout": timeout, "limits": limits, "http2": False}
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**kwargs)


class ProxyPool:
    """代理池：快照缓存 + 客户端复用 + 轮询选择"""

    SNAPSHOT_TTL = 5.0

    def __init__(self):
        # (proxy_url, is_stream) -> AsyncClient
        self._clients: dict[tuple[str, bool], httpx.AsyncClient] = {}
        self._lock = asyncio.Lock()
        self._snapshot_at = 0.0
        # 活跃代理快照：[{"id","name","url"}]
        self._active: list[dict] = []
        # key_id -> (proxy_mode, proxy_id)
        self._bindings: dict[str, tuple[str, Optional[str]]] = {}
        self._rr_cursor = 0

    # ---------- 快照 ----------

    def invalidate(self) -> None:
        """使快照立即失效（代理/密钥的增删改后调用）"""
        self._snapshot_at = 0.0

    def _load_snapshot(self) -> tuple[list[dict], dict[str, tuple[str, Optional[str]]]]:
        """同步读取 DB 构建快照（须在 to_thread 中调用）"""
        master_key = get_setting("upstream_master_key")
        active: list[dict] = []
        rows = fetch_all(
            "SELECT id, name, scheme, host, port, username, password_ciphertext "
            "FROM proxies WHERE status = 'active' ORDER BY created_at"
        )
        for row in rows:
            password = ""
            cipher = row.get("password_ciphertext") or ""
            if cipher and master_key:
                try:
                    password = decrypt_proxy_secret(cipher, master_key)
                except Exception as e:
                    logger.error("代理密码解密失败: proxy=%s error=%s", row["id"][:8], e)
                    continue
            try:
                url = build_proxy_url(
                    row["scheme"], row["host"], row["port"],
                    row.get("username") or "", password,
                )
            except ValueError as e:
                logger.error("代理配置非法已跳过: proxy=%s error=%s", row["id"][:8], e)
                continue
            active.append({"id": row["id"], "name": row["name"], "url": url})

        bindings: dict[str, tuple[str, Optional[str]]] = {}
        for row in fetch_all("SELECT id, proxy_mode, proxy_id FROM upstream_keys"):
            bindings[row["id"]] = (row.get("proxy_mode") or MODE_DIRECT, row.get("proxy_id"))
        return active, bindings

    async def _ensure_snapshot(self) -> None:
        if self._snapshot_at and (time.time() - self._snapshot_at) < self.SNAPSHOT_TTL:
            return
        async with self._lock:
            # 双检：等锁期间可能已被其他协程刷新
            if self._snapshot_at and (time.time() - self._snapshot_at) < self.SNAPSHOT_TTL:
                return
            try:
                active, bindings = await asyncio.to_thread(self._load_snapshot)
            except Exception as e:
                logger.error("代理池快照加载失败，本轮按直连处理: %s", e)
                self._snapshot_at = time.time()
                return
            self._active = active
            self._bindings = bindings
            self._snapshot_at = time.time()
            await self._evict_stale_clients({p["url"] for p in active})

    async def _evict_stale_clients(self, live_urls: set) -> None:
        """关闭已被删除/改配的代理对应的客户端，防止连接与内存泄漏"""
        stale = [k for k in self._clients if k[0] not in live_urls]
        for key in stale:
            client = self._clients.pop(key, None)
            if client is not None and not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass
        if stale:
            logger.info("代理池已回收 %d 个失效客户端", len(stale))

    # ---------- 解析与客户端 ----------

    def _pick_rotate_url(self) -> Optional[str]:
        """轮询选择一个活跃代理（单 worker 进程内游标）"""
        if not self._active:
            return None
        self._rr_cursor = (self._rr_cursor + 1) % len(self._active)
        return self._active[self._rr_cursor]["url"]

    async def resolve_url(self, key_id: Optional[str]) -> Optional[str]:
        """解析某上游密钥的出网代理 URL；返回 None 表示直连"""
        if not key_id:
            return None
        await self._ensure_snapshot()
        return self._select_url(key_id)

    def resolve_url_sync(self, key_id: Optional[str]) -> Optional[str]:
        """同步解析出网代理 URL（仅供已在线程池中执行的同步探活路径调用）"""
        if not key_id:
            return None
        if not self._snapshot_at or (time.time() - self._snapshot_at) >= self.SNAPSHOT_TTL:
            try:
                self._active, self._bindings = self._load_snapshot()
                self._snapshot_at = time.time()
            except Exception as e:
                logger.error("代理池快照同步加载失败，按直连处理: %s", e)
                return None
        return self._select_url(key_id)

    def _select_url(self, key_id: str) -> Optional[str]:
        """按绑定模式在当前快照内选择代理 URL（快照已就绪）"""
        mode, proxy_id = self._bindings.get(key_id, (MODE_DIRECT, None))
        if mode == MODE_BIND:
            for item in self._active:
                if item["id"] == proxy_id:
                    return item["url"]
            logger.warning("上游密钥绑定的代理不可用，回退直连: key=%s", key_id[:8])
            return None
        if mode == MODE_ROTATE:
            url = self._pick_rotate_url()
            if url is None:
                logger.warning("代理池无活跃代理，轮询回退直连: key=%s", key_id[:8])
            return url
        return None

    async def get_client(self, key_id: Optional[str], stream: bool) -> Optional[httpx.AsyncClient]:
        """获取该密钥应使用的代理客户端；返回 None 表示应走直连池"""
        url = await self.resolve_url(key_id)
        if not url:
            return None
        return await self.get_client_for_url(url, stream)

    async def get_client_for_url(self, proxy_url: str, stream: bool) -> httpx.AsyncClient:
        """按 (代理URL, 是否流式) 复用客户端"""
        cache_key = (proxy_url, stream)
        client = self._clients.get(cache_key)
        if client is not None and not client.is_closed:
            return client
        async with self._lock:
            client = self._clients.get(cache_key)
            if client is not None and not client.is_closed:
                return client
            client = build_client(proxy_url, stream)
            self._clients[cache_key] = client
            return client

    # ---------- 连通性探测 ----------

    async def probe(self, proxy_url: str, target_url: str, timeout: float = 10.0) -> tuple[bool, str]:
        """通过指定代理访问 target_url，验证连通性

        只要拿到 HTTP 响应即视为代理可用（401 等鉴权失败同样证明通道打通）。
        使用一次性客户端，避免污染热路径缓存。
        """
        started = time.time()
        try:
            async with build_client(proxy_url, stream=False, max_connections=4, max_keepalive=0) as client:
                resp = await client.get(target_url, timeout=httpx.Timeout(timeout, connect=timeout))
            cost = int((time.time() - started) * 1000)
            return True, f"连通正常 HTTP {resp.status_code} ({cost}ms)"
        except Exception as e:
            cost = int((time.time() - started) * 1000)
            return False, f"{type(e).__name__}: {e} ({cost}ms)"

    # ---------- 观测与清理 ----------

    def get_status(self) -> dict:
        """代理池运行态（供系统监控展示）"""
        return {
            "active_proxies": len(self._active),
            "cached_clients": len(self._clients),
            "snapshot_age": round(time.time() - self._snapshot_at, 1) if self._snapshot_at else -1,
            "rotate_cursor": self._rr_cursor,
            "bound_keys": sum(1 for m, _ in self._bindings.values() if m != MODE_DIRECT),
        }

    async def close_all(self) -> None:
        """关闭全部代理客户端（进程退出时调用）"""
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            if not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass


# 全局单例（调度器算法为进程内状态，单 worker 部署）
proxy_pool = ProxyPool()
