"""
软限速模块 v10.0 - 均匀间隔排队模式

策略（v10.0 重写）：
  old:    不限速（间隔 = 0，不排队）
  new:    120 RPM，均匀铺开到每一秒
          机制：记录上次响应完成时间，保证两次响应之间至少间隔 500ms
          效果：即使用户秒发多次请求，每次响应完成后需等待最小间隔
          例子：120 RPM = 2 RPS = 每 500ms 放行一次

          用户连续发两次请求：
          第一次 0ms 到达 → 立即放行 → 响应耗时 2s → mark_response_done → t=2.0
          第二次 2.1s 到达 → 距上次完成仅 100ms → 等待 400ms → 放行

          超过 500ms 没来请求 → 下个请求立即放行（间隔重置）

注意：
  - 绝不返回 429 拒绝请求
  - 仅在 new 用户生效，old 用户不受影响
  - 考核期用户（is_special）由外部逻辑控制，不受此限
"""
import time
import asyncio
import logging
from typing import Dict

logger = logging.getLogger("aqua.soft_limiter")

# ====== 可配置参数 ======
# 每分钟目标请求数（RPM）
RATE_TARGET = {"old": 9999, "new": 120}

# 用户类型对应的最小间隔（秒）- 自动从 RATE_TARGET 计算
def _get_interval(user_type: str) -> float:
    target = RATE_TARGET.get(user_type, 9999)
    if target >= 9999:
        return 0.0
    return 60.0 / target


class SoftRateLimiter:
    """
    软限速器 v10.0：均匀间隔排队模式

    不拒绝请求，通过控制两次响应之间的最小间隔来限速。
    new 用户每 500ms 放行一次（120 RPM），
    old 用户不限速。
    
    使用方式：
      1. 请求到达时调用 apply_delay() → 如果距上次响应不足间隔，自动等待
      2. 响应完成后调用 mark_response_done() → 记录完成时间
    """

    def __init__(self):
        # user_id -> 上次响应完成时间戳（由 mark_response_done 设置）
        self._response_done: Dict[int, float] = {}

    def record_request(self, user_id: int):
        """兼容旧接口：v10.0 不再需要窗口计数，保留空方法"""
        pass

    def get_delay(self, user_id: int, user_type: str = "old") -> float:
        """
        计算需要等待的时间（秒）

        基于上次响应完成时间，计算距最小间隔还差多少。
        返回 0 表示不等待，正数表示需要 sleep 的秒数。
        永不返回 None，永不抛出异常。
        纯计算，不修改任何状态。
        """
        interval = _get_interval(user_type)
        if interval <= 0:
            return 0.0  # 不限速

        now = time.time()
        last = self._response_done.get(user_id, 0.0)

        # 还没有任何响应记录 → 直接放行
        if last == 0.0:
            return 0.0

        elapsed = now - last
        if elapsed >= interval:
            return 0.0  # 已经超过最小间隔

        # 需要等待剩余时间补齐间隔
        delay = interval - elapsed
        delay = round(max(delay, 0.0), 3)

        if delay >= 0.05:
            logger.debug(
                f"均匀间隔限速: user_id={user_id} type={user_type} "
                f"elapsed={elapsed:.3f}s interval={interval:.3f}s delay={delay:.3f}s"
            )

        return delay

    async def apply_delay(self, user_id: int, user_type: str = "old"):
        """
        在请求处理前执行均匀间隔等待（如需要）

        此函数只负责等待，不更新时间戳。
        时间戳由 mark_response_done 在响应完成后单独设置。
        """
        delay = self.get_delay(user_id, user_type)
        if delay > 0:
            await asyncio.sleep(delay)
        return delay

    def mark_response_done(self, user_id: int):
        """
        标记一次响应已完成，记录当前时间作为间隔起点

        v10.0: 由调用方在响应完成后调用，确保间隔从"响应完成"开始计算
        """
        self._response_done[user_id] = time.time()

    def get_stats(self) -> dict:
        """获取统计信息"""
        now = time.time()
        active = sum(1 for uid, t in self._response_done.items() if now - t < 60)
        return {
            "active_users_60s": active,
            "rate_targets": dict(RATE_TARGET),
            "intervals": {k: round(_get_interval(k), 3) for k in RATE_TARGET},
        }


# 全局实例
_limiter: SoftRateLimiter = None


def get_soft_limiter() -> SoftRateLimiter:
    """获取全局软限速器"""
    global _limiter
    if _limiter is None:
        _limiter = SoftRateLimiter()
    return _limiter
