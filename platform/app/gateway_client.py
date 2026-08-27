"""
网关API客户端 - 用户平台与网关服务对接 (v10.0: 共享连接池)

通过平台令牌（apt_前缀）调用网关管理API：
- 创建/删除/更新客户端
- 创建/删除客户端密钥
- 获取用量统计
- 获取模型列表
"""
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("aqua.gateway")

# 启动即提示：平台令牌缺失时所有网关管理API调用都会被拒（401），提前暴露配置问题
if not os.environ.get("AQUA_PLATFORM_TOKEN"):
    logger.error(
        "[FATAL] 环境变量 AQUA_PLATFORM_TOKEN 未设置，平台调用网关管理API将全部返回401！"
        "请在 .env 中配置网关颁发的平台令牌（apt_ 前缀）。"
    )


class GatewayClient:
    """ACU网关API客户端（v10.0: 注入共享连接池，避免每次调用新建连接）"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        platform_token: Optional[str] = None,
        http_pool: Optional[httpx.AsyncClient] = None,
    ):
        # 默认指向网关8000端口（原默认8001是平台自身端口，会递归请求自己）
        self.base_url = base_url or os.environ.get("GW_BASE_URL", "http://127.0.0.1:8000")
        self.platform_token = platform_token or os.environ.get("AQUA_PLATFORM_TOKEN", "")
        self._http_pool = http_pool
        self._own_pool = http_pool is None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取HTTP客户端（优先使用共享连接池）"""
        if self._http_pool and not self._http_pool.is_closed:
            return self._http_pool
        if self._own_pool:
            self._http_pool = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=25.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60),
            )
        return self._http_pool

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """统一请求方法"""
        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {self.platform_token}")
        headers.setdefault("Content-Type", "application/json")
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        return await client.request(method, url, headers=headers, **kwargs)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.platform_token}",
            "Content-Type": "application/json",
        }

    async def create_client(self, name: str, user_type: str = "old") -> dict:
        """创建网关客户端 → POST /gw/admin/clients"""
        try:
            resp = await self._request("POST", "/gw/admin/clients", json={"name": name, "user_type": user_type})
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"网关创建客户端成功: name={name}, id={result.get('id','')}")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"网关创建客户端失败: status={e.response.status_code}, body={e.response.text[:300]}")
            raise
        except Exception as e:
            logger.error(f"网关创建客户端异常: {e}")
            raise

    async def delete_client(self, client_id: str) -> bool:
        """删除网关客户端 → DELETE /gw/admin/clients/{client_id}"""
        try:
            resp = await self._request("DELETE", f"/gw/admin/clients/{client_id}")
            if resp.status_code == 200:
                logger.info(f"网关删除客户端成功: id={client_id}")
                return True
            logger.warning(f"网关删除客户端返回非200: id={client_id}, status={resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"网关删除客户端异常: id={client_id}, error={e}")
            return False

    async def create_client_key(self, client_id: str) -> dict:
        """创建客户端API密钥 → POST /gw/admin/clients/{client_id}/keys"""
        try:
            resp = await self._request("POST", f"/gw/admin/clients/{client_id}/keys")
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"网关创建密钥成功: client_id={client_id}, key_id={result.get('id','')}")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"网关创建密钥失败: status={e.response.status_code}, body={e.response.text[:300]}")
            raise
        except Exception as e:
            logger.error(f"网关创建密钥异常: {e}")
            raise

    async def delete_client_key(self, client_id: str, key_id: str) -> bool:
        """删除客户端API密钥 → DELETE /gw/admin/clients/{client_id}/keys/{key_id}"""
        try:
            resp = await self._request("DELETE", f"/gw/admin/clients/{client_id}/keys/{key_id}")
            if resp.status_code == 200:
                logger.info(f"网关删除密钥成功: key_id={key_id}")
                return True
            logger.warning(f"网关删除密钥返回非200: key_id={key_id}, status={resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"网关删除密钥异常: key_id={key_id}, error={e}")
            return False

    async def update_client_key_status(self, client_id: str, key_id: str, status: str) -> bool:
        """更新客户端密钥状态（启用/禁用）→ PUT /gw/admin/clients/{client_id}/keys/{key_id}"""
        try:
            resp = await self._request("PUT", f"/gw/admin/clients/{client_id}/keys/{key_id}", json={"status": status})
            if resp.status_code == 200:
                logger.info(f"网关更新密钥状态成功: key_id={key_id}, status={status}")
                return True
            logger.warning(f"网关更新密钥状态返回非200: key_id={key_id}, status={resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"网关更新密钥状态异常: key_id={key_id}, error={e}")
            return False

    async def reveal_client_key(self, client_id: str, key_id: str) -> dict:
        """从网关获取客户密钥明文 → GET /gw/admin/clients/{client_id}/keys/{key_id}/reveal"""
        try:
            resp = await self._request("GET", f"/gw/admin/clients/{client_id}/keys/{key_id}/reveal")
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"网关获取密钥明文返回非200: key_id={key_id}, status={resp.status_code}")
            raise Exception(f"网关返回 {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"网关获取密钥明文异常: key_id={key_id}, error={e}")
            raise

    async def list_client_keys(self, client_id: str) -> list:
        """获取客户所有密钥 → GET /gw/admin/clients/{client_id}/keys"""
        try:
            resp = await self._request("GET", f"/gw/admin/clients/{client_id}/keys")
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
            return []
        except Exception as e:
            logger.error(f"网关获取客户密钥列表异常: client_id={client_id}, error={e}")
            return []

    async def get_client_usage(self, client_id: str) -> dict:
        """获取客户用量统计 → GET /gw/admin/clients/{client_id}/usage"""
        try:
            resp = await self._request("GET", f"/gw/admin/clients/{client_id}/usage")
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            logger.error(f"网关获取用量异常: client_id={client_id}, error={e}")
            return {}

    async def get_models_status(self) -> dict:
        """获取模型健康状态 → GET /gw/admin/models/status"""
        try:
            resp = await self._request("GET", "/gw/admin/models/status")
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"网关模型状态API返回非200: status={resp.status_code}, body={resp.text[:200]}")
            return {"models": [], "summary": {}, "error": f"网关返回 {resp.status_code}"}
        except httpx.TimeoutException:
            logger.error("网关模型状态API超时")
            return {"models": [], "summary": {}, "error": "网关超时"}
        except Exception as e:
            logger.error(f"网关模型状态API异常: {e}")
            return {"models": [], "summary": {}, "error": str(e)}

    async def get_models(self) -> list:
        """获取模型列表 → GET /api/public/models"""
        try:
            resp = await self._request("GET", "/api/public/models")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data.get("data", [])
                return data if isinstance(data, list) else []
            return []
        except Exception as e:
            logger.error(f"网关获取模型列表异常: {e}")
            return []

    async def close(self):
        """关闭自有连接池"""
        if self._own_pool and self._http_pool and not self._http_pool.is_closed:
            await self._http_pool.aclose()
            self._http_pool = None
