"""
时间戳契约函数测试（gateway/app/database.py，纯函数，不连数据库）

覆盖：
- utcnow()：Z 结尾 UTC、毫秒精度（.mmmZ）、与真实时间偏差 < 5s
- utcnow_minus(n)：同样格式，与 utcnow() 差值 ≈ n 秒
- today_start_utc()：CST 零点对应的 UTC 边界（恒为 UTC 16:00:00.000Z）
"""
import os
import re
from datetime import datetime, timezone

# gateway/app/database.py 在模块级强制要求 PG_PASSWORD（缺失即 raise），
# 单元测试不连接数据库，这里仅提供占位值以满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")

from _app_path import _switch_app

# gateway/app 与 platform/app 是同名 "app" 包，须先切换并清理模块缓存再导入
_switch_app("gateway")

from app.database import today_start_utc, utcnow, utcnow_minus

# ISO-8601 UTC、毫秒精度、Z 后缀
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class TestTimestamps:
    def test_utcnow_format(self):
        assert _TS_RE.match(utcnow()) is not None

    def test_utcnow_close_to_real_time(self):
        ts = _parse(utcnow())
        real = datetime.now(timezone.utc)
        assert abs((real - ts).total_seconds()) < 5

    def test_utcnow_minus_format(self):
        assert _TS_RE.match(utcnow_minus(60)) is not None

    def test_utcnow_minus_delta(self):
        now = _parse(utcnow())
        past = _parse(utcnow_minus(3600))
        delta = (now - past).total_seconds()
        # 差值应非常接近 3600s（允许 ±10s 的执行抖动，两次调用各自取了当前时间）
        assert 3590 <= delta <= 3610

    def test_utcnow_minus_zero(self):
        delta = (_parse(utcnow()) - _parse(utcnow_minus(0))).total_seconds()
        assert -5 <= delta <= 5

    def test_today_start_utc_format(self):
        ts = today_start_utc()
        assert _TS_RE.match(ts) is not None
        assert ts.endswith(".000Z")  # 边界恒为整毫秒零

    def test_today_start_utc_is_cst_midnight(self):
        # CST(UTC+8) 零点 == UTC 前一日 16:00:00.000
        t = _parse(today_start_utc())
        assert t.hour == 16
        assert t.minute == 0
        assert t.second == 0
        assert t.microsecond == 0

    def test_today_start_not_in_future(self):
        assert _parse(today_start_utc()) <= datetime.now(timezone.utc)
