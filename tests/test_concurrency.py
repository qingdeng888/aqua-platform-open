"""
并发控制测试 - 断言 v11.0 现实（已解除所有硬性并发限制）

对应实现：platform/app/concurrency.py
- try_acquire 恒返回 True（不拒绝任何请求）
- get_limit / get_limit_for_user 恒返回 9999（哨兵值 = 不限制）
- get_stats 结构为 {"limits": {"all": 9999}, ...}
"""
import pytest

from _app_path import _switch_app


class TestConcurrencyControllerV11:
    """并发控制器 - v11.0 无限制语义"""

    @pytest.mark.asyncio
    async def test_try_acquire_always_true(self):
        _switch_app("platform")
        from app.concurrency import ConcurrencyController

        ctrl = ConcurrencyController()
        # 任意 user_type 下，无限次获取均应成功（不再拒绝）
        for i in range(50):
            assert await ctrl.try_acquire(1, f"req-{i}", "new") is True
            assert await ctrl.try_acquire(1, f"req-{i}", "old") is True

    @pytest.mark.asyncio
    async def test_release_is_noop(self):
        _switch_app("platform")
        from app.concurrency import ConcurrencyController

        ctrl = ConcurrencyController()
        await ctrl.try_acquire(1, "req-1", "old")
        await ctrl.release(1, "req-1")  # 空操作，不应抛异常
        assert ctrl.get_current(1) == 0

    def test_limit_is_sentinel_9999(self):
        _switch_app("platform")
        from app.concurrency import ConcurrencyController

        ctrl = ConcurrencyController()
        assert ctrl.get_limit_for_user("old") == 9999
        assert ctrl.get_limit_for_user("new") == 9999
        assert ctrl.get_limit(1) == 9999
        assert ctrl.get_limit(None) == 9999

    def test_get_stats_shape(self):
        _switch_app("platform")
        from app.concurrency import ConcurrencyController

        ctrl = ConcurrencyController()
        stats = ctrl.get_stats()
        # v11.0：不再有 old/new 分级，只有统一的 {"all": 9999}
        assert stats["limits"] == {"all": 9999}
        assert stats["rejected"] == 0
        assert stats["peak"] == 0
        assert stats["active_users"] == 0

    def test_cleanup_stale_is_noop(self):
        _switch_app("platform")
        from app.concurrency import ConcurrencyController

        ctrl = ConcurrencyController()
        ctrl.cleanup_stale(max_age=300.0)  # 空操作，不应抛异常
