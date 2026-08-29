"""
时间戳契约函数测试（gateway/app/database.py，纯函数，不连数据库）

覆盖：
- utcnow()：Z 结尾 UTC、毫秒精度（.mmmZ）、与真实时间偏差 < 5s
- utcnow_minus(n)：同样格式，与 utcnow() 差值 ≈ n 秒
- today_start_utc()：CST 零点对应的 UTC 边界（恒为 UTC 16:00:00.000Z）
- utc_from_ts(ts)：Unix 时间戳 → 同格式 Z 字符串
- 写库路径守卫：请求日志三列不得再出现 +08:00 变体（TEXT 列靠字典序比较窗口边界）
"""
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# gateway/app/database.py 在模块级强制要求 PG_PASSWORD（缺失即 raise），
# 单元测试不连接数据库，这里仅提供占位值以满足导入。
os.environ.setdefault("PG_PASSWORD", "unit-test-no-connection")

import app.database as database
from app.database import today_start_utc, utc_from_ts, utcnow, utcnow_minus

_APP_DIR = Path(__file__).resolve().parent.parent / "gateway" / "app"

# 形如 f"{...:03d}+08:00" 的本地时区字面量拼接（只匹配字符串字面量，不误伤注释）
_LOCAL_TS_LITERAL = re.compile(r"""["'][^"'\n]*\+08:00""")

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

    def test_utc_from_ts_format(self):
        assert _TS_RE.match(utc_from_ts(1_800_000_000.123)) is not None

    def test_utc_from_ts_value(self):
        # 1800000000 == 2027-01-15T08:00:00Z（UTC 口径，不受运行机时区影响）
        assert utc_from_ts(1_800_000_000).startswith("2027-01-15T08:00:00.")

    def test_utc_from_ts_keeps_millis(self):
        assert utc_from_ts(1_800_000_000.456).endswith(".456Z")


class TestWriteFormatGuard:
    """守卫：请求日志的写入路径只能产出 UTC Z 格式

    request_logs 的 created_at/started_at/completed_at 是 TEXT，窗口过滤
    （IP监控 5 分钟、日志清理 3 天/90 天、今日统计）靠字符串字典序比较。
    历史上写入端用 localnow() 写 +08:00，把每个窗口向外撑开了 8 小时。
    """

    def test_localnow_family_removed(self):
        # 一旦有人重新引入本地时区写入助手，这里立刻失败
        for name in ("localnow", "localnow_ms", "today_start_local"):
            assert not hasattr(database, name), f"database.{name} 不应再存在（会重新引入混格式时间戳）"

    def test_log_writers_use_utc_helpers(self):
        for filename in ("middleware.py", "public_api.py"):
            # 去掉行内注释后再查，避免命中说明这条纪律本身的中文注释
            code = "\n".join(
                line.split("#", 1)[0]
                for line in (_APP_DIR / filename).read_text(encoding="utf-8").splitlines()
            )
            assert "localnow" not in code, f"{filename} 不得使用 localnow()"
            assert _LOCAL_TS_LITERAL.search(code) is None, \
                f"{filename} 不得拼接 +08:00 时间戳字面量（请用 utcnow()/utc_from_ts()）"
