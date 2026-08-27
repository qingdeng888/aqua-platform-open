"""
并发量控制系统 - v11.0 (已解除所有限制)
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("aqua.concurrency")


class ConcurrencyController:
    """
    并发控制器 - v11.0 已解除所有并发限制
    所有用户无限并发，不再拒绝任何请求
    try_acquire 始终返回 True
    """

    async def try_acquire(self, user_id: int, request_id: str, user_type: str = "old") -> bool:
        """已解除限制：始终返回 True"""
        return True

    async def release(self, user_id: int, request_id: str):
        """已解除限制：空操作"""
        pass

    def get_current(self, user_id: int) -> int:
        return 0

    def get_limit(self, user_id: int = None) -> int:
        return 9999

    def get_limit_for_user(self, user_type: str = "old") -> int:
        return 9999

    def get_all_active(self) -> dict:
        return {}

    def get_stats(self) -> dict:
        return {"limits": {"all": 9999}, "rejected": 0, "peak": 0, "active_users": 0}

    def cleanup_stale(self, max_age: float = 300.0):
        pass


# 全局实例
_controller: Optional[ConcurrencyController] = None


def get_concurrency_controller() -> ConcurrencyController:
    """获取全局并发控制器"""
    global _controller
    if _controller is None:
        _controller = ConcurrencyController()
    return _controller
