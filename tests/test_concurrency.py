"""
AQUA 平台核心模块测试 - 对标 litellm 测试体系
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "platform" / "app"))

import pytest


class TestConcurrencyController:
    """并发控制器测试"""

    @pytest.mark.asyncio
    async def test_basic_acquire_release(self):
        from app.concurrency import ConcurrencyController
        ctrl = ConcurrencyController()
        uid, rid = 1, "req-1"

        # 获取
        assert await ctrl.try_acquire(uid, rid, "old")
        # 释放
        await ctrl.release(uid, rid)
        # 释放后应该为0
        assert ctrl.get_current(uid) == 0

    @pytest.mark.asyncio
    async def test_limit_enforcement(self):
        from app.concurrency import ConcurrencyController
        ctrl = ConcurrencyController()
        uid = 2

        # 新用户限制为2
        assert await ctrl.try_acquire(uid, "r1", "new")
        assert await ctrl.try_acquire(uid, "r2", "new")
        assert not await ctrl.try_acquire(uid, "r3", "new")  # 应拒绝

        # 释放一个后应能再次获取
        await ctrl.release(uid, "r1")
        assert await ctrl.try_acquire(uid, "r4", "new")

    @pytest.mark.asyncio
    async def test_old_user_limit(self):
        from app.concurrency import ConcurrencyController
        ctrl = ConcurrencyController()
        uid = 3

        # 老用户限制为4
        for i in range(4):
            assert await ctrl.try_acquire(uid, f"r{i}", "old")
        assert not await ctrl.try_acquire(uid, "r5", "old")

    def test_get_stats(self):
        from app.concurrency import ConcurrencyController
        ctrl = ConcurrencyController()
        stats = ctrl.get_stats()
        assert "limits" in stats
        assert stats["limits"]["old"] == 4
        assert stats["limits"]["new"] == 2
