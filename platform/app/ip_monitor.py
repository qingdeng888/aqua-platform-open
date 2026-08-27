"""
IP 监测与防滥用监控系统 - v9.2（新增 UA 分析 + 机器人检测）

功能：
1. 检测单个 IP 下的异常使用行为
2. 识别多用户共享同一 IP
3. 监测批量请求模式和频率异常
4. 新增：User-Agent 分析（检测自动化工具/无头浏览器）
5. 新增：浏览器指纹分析（缺少关键特征头）
6. 新增：请求时序模式分析（检测脚本周期性请求）
7. 与现有防商用算法集成，形成双重防护

使用内存存储 + 定期持久化到数据库
"""
import time
import re
import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("aqua.ip_monitor")

# ===== 自动化工具 / 无头浏览器 UA 关键词 =====
_AUTOMATION_UA_KEYWORDS = [
    "headless", "phantomjs", "selenium", "puppeteer",
    "playwright", "chromium-browser", "electron",
    "python-requests", "python-httpx", "aiohttp",
    "curl", "wget", "ruby", "go-http-client",
    "axios", "node-fetch", "scrapy", "bot",
    "httpclient", "okhttp",
]


class IPMonitor:
    """
    IP 地址监测器

    跟踪每个 IP 的:
    - 关联用户数
    - 请求频率（每秒请求数）
    - 批量请求模式
    - User-Agent 特征
    - 异常行为标记
    """

    def __init__(
        self,
        max_requests_per_second: int = 5,
        max_users_per_ip: int = 3,
        batch_window: float = 1.0,
        batch_threshold: int = 10,
        cooldown_minutes: int = 30,
    ):
        self._max_rps = max_requests_per_second
        self._max_users = max_users_per_ip
        self._batch_window = batch_window
        self._batch_threshold = batch_threshold
        self._cooldown_seconds = cooldown_minutes * 60

        # ip -> deque of timestamps
        self._requests: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        # ip -> set of user_ids
        self._ip_users: Dict[str, Set[int]] = defaultdict(set)
        # ip -> float (冷却到期时间)
        self._blocked_ips: Dict[str, float] = {}
        # ip -> anomaly score (0~100)
        self._anomaly_scores: Dict[str, float] = defaultdict(float)

        # ===== v9.2 新增：UA 和浏览器指纹相关 =====
        # ip -> deque of user_agents
        self._ua_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        # ip -> 是否检测到自动化工具
        self._automation_flagged: Dict[str, bool] = defaultdict(bool)
        # ip -> 请求时间间隔序列（用于脚本周期性检测）
        self._interval_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        # ip -> 上次请求时间
        self._last_request_time: Dict[str, float] = {}

        self._stats = {
            "total_ips_tracked": 0,
            "blocked_ips": 0,
            "anomaly_events": 0,
            "automation_detected": 0,
        }

    def record_request(self, ip: str, user_id: int, request_id: str, user_agent: str = "") -> dict:
        """
        记录一次请求，返回 IP 状态评估

        返回:
            {
                "allowed": True/False,     # 是否允许继续
                "reason": "",               # 拒绝原因
                "score": 0-100,            # 异常评分
                "users_on_ip": N,           # 该 IP 上的用户数
                "rps": N,                   # 当前每秒请求数
            }
        """
        now = time.time()
        result = {
            "allowed": True,
            "reason": "",
            "score": 0.0,
            "users_on_ip": 0,
            "rps": 0,
        }

        # 检查 IP 是否被封锁
        if ip in self._blocked_ips:
            if now < self._blocked_ips[ip]:
                result["allowed"] = False
                result["reason"] = "ip_blocked"
                return result
            else:
                del self._blocked_ips[ip]

        # 记录请求
        self._requests[ip].append(now)
        self._ip_users[ip].add(user_id)

        # 记录请求间隔（用于周期性检测）
        prev_ts = self._last_request_time.get(ip)
        if prev_ts is not None:
            interval = now - prev_ts
            if 0 < interval < 10:  # 只记录 10 秒内的间隔
                self._interval_history[ip].append(interval)
        self._last_request_time[ip] = now

        # 记录 User-Agent
        if user_agent:
            self._ua_history[ip].append(user_agent)

        # 计算当前 RPS
        recent = [t for t in self._requests[ip] if now - t < 1.0]
        rps = len(recent)
        result["rps"] = rps
        result["users_on_ip"] = len(self._ip_users[ip])

        # === 异常检测 ===

        # 1. 频率检测
        if rps > self._max_rps:
            self._anomaly_scores[ip] = min(100, self._anomaly_scores[ip] + 20)
            result["reason"] = "high_rps"
            logger.warning(f"IP高频请求: ip={ip} rps={rps} user_id={user_id}")

        # 2. 多用户共享 IP
        if len(self._ip_users[ip]) > self._max_users:
            self._anomaly_scores[ip] = min(100, self._anomaly_scores[ip] + 15)
            result["reason"] = "multi_user_ip"
            logger.warning(
                f"IP多用户共享: ip={ip} users={self._ip_users[ip]} "
                f"user_id={user_id}"
            )

        # 3. 批量请求模式检测
        batch_recent = [t for t in self._requests[ip] if now - t < self._batch_window]
        if len(batch_recent) > self._batch_threshold:
            self._anomaly_scores[ip] = min(100, self._anomaly_scores[ip] + 25)
            result["reason"] = "batch_request"
            logger.warning(
                f"IP批量请求: ip={ip} count={len(batch_recent)} "
                f"in {self._batch_window}s user_id={user_id}"
            )

        # === v9.2: UA 自动化工具检测 ===
        if user_agent:
            ua_lower = user_agent.lower()
            for kw in _AUTOMATION_UA_KEYWORDS:
                if kw in ua_lower:
                    self._automation_flagged[ip] = True
                    self._anomaly_scores[ip] = min(100, self._anomaly_scores[ip] + 30)
                    self._stats["automation_detected"] += 1
                    result["reason"] = "automation_detected"
                    logger.warning(
                        f"自动化工具检测: ip={ip} ua_kw={kw} user_id={user_id}"
                    )
                    break
            # 空UA或过短UA
            if len(user_agent) < 20:
                self._anomaly_scores[ip] = min(100, self._anomaly_scores[ip] + 15)
                result["reason"] = "suspicious_ua"

        # === v9.2: 请求间隔规律性检测 ===
        intervals = list(self._interval_history.get(ip, []))
        if len(intervals) >= 10:
            # 计算变异系数（CV），CV 小 = 请求间隔高度规律 = 脚本特征
            mean_interval = sum(intervals) / len(intervals)
            if mean_interval > 0:
                variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
                cv = (variance ** 0.5) / mean_interval
                if cv < 0.3:  # 高度规律的请求间隔
                    self._anomaly_scores[ip] = min(100, self._anomaly_scores[ip] + 10)
                    result["reason"] = "regular_interval"

        # 综合异常评分
        score = self._anomaly_scores[ip]
        result["score"] = score

        if score >= 80:
            # 自动封锁 IP
            self._blocked_ips[ip] = now + self._cooldown_seconds
            self._stats["blocked_ips"] += 1
            result["allowed"] = False
            result["reason"] = "ip_blocked_auto"
            logger.error(
                f"IP自动封锁: ip={ip} score={score} "
                f"冷却{self._cooldown_seconds}s"
            )
        elif score >= 50:
            self._stats["anomaly_events"] += 1

        # 正常时逐渐降低分数
        if rps <= self._max_rps and len(self._ip_users[ip]) <= self._max_users:
            self._anomaly_scores[ip] = max(0, self._anomaly_scores[ip] - 1)

        self._stats["total_ips_tracked"] = len(self._requests)
        return result

    def get_ip_status(self, ip: str) -> Optional[dict]:
        """获取单个 IP 的状态"""
        if ip not in self._requests:
            return None

        now = time.time()
        blocked_until = self._blocked_ips.get(ip, 0)
        recent = [t for t in self._requests[ip] if now - t < 60.0]

        return {
            "ip": ip,
            "blocked": now < blocked_until,
            "blocked_remaining": max(0, int(blocked_until - now)) if blocked_until > now else 0,
            "anomaly_score": round(self._anomaly_scores.get(ip, 0), 1),
            "users_on_ip": list(self._ip_users.get(ip, set())),
            "requests_60s": len(recent),
            "rps": len([t for t in self._requests[ip] if now - t < 1.0]),
        }

    def get_all_blocked_ips(self) -> List[dict]:
        """获取所有被封锁的 IP"""
        now = time.time()
        return [
            {
                "ip": ip,
                "remaining": max(0, int(expires - now)),
                "score": round(self._anomaly_scores.get(ip, 0), 1),
                "users": list(self._ip_users.get(ip, set())),
            }
            for ip, expires in self._blocked_ips.items()
            if now < expires
        ]

    def get_all_anomalies(self, min_score: float = 30.0) -> List[dict]:
        """获取所有异常 IP"""
        now = time.time()
        anomalies = []
        for ip, score in self._anomaly_scores.items():
            if score >= min_score and ip in self._requests:
                recent = [t for t in self._requests[ip] if now - t < 60.0]
                anomalies.append({
                    "ip": ip,
                    "score": round(score, 1),
                    "users": list(self._ip_users.get(ip, set())),
                    "requests_60s": len(recent),
                    "blocked": ip in self._blocked_ips and now < self._blocked_ips[ip],
                })
        return sorted(anomalies, key=lambda x: -x["score"])

    def unblock_ip(self, ip: str) -> bool:
        """手动解封 IP"""
        if ip in self._blocked_ips:
            del self._blocked_ips[ip]
            self._anomaly_scores[ip] = 0
            logger.info(f"IP手动解封: {ip}")
            return True
        return False

    def reset_ip(self, ip: str):
        """重置 IP 的所有状态"""
        self._requests.pop(ip, None)
        self._ip_users.pop(ip, None)
        self._blocked_ips.pop(ip, None)
        self._anomaly_scores.pop(ip, None)
        logger.info(f"IP状态重置: {ip}")

    def get_stats(self) -> dict:
        """获取统计信息"""
        now = time.time()
        active_blocked = sum(
            1 for e in self._blocked_ips.values() if now < e
        )
        return {
            **self._stats,
            "active_blocked": active_blocked,
            "anomaly_count": len([
                s for s in self._anomaly_scores.values() if s >= 30
            ]),
        }

    def cleanup(self, max_age: float = 3600.0):
        """清理过期数据"""
        now = time.time()
        # 清理 1 小时前的请求记录
        expired_ips = []
        for ip, timestamps in self._requests.items():
            while timestamps and now - timestamps[0] > max_age:
                timestamps.popleft()
            if not timestamps:
                expired_ips.append(ip)
        for ip in expired_ips:
            self._requests.pop(ip, None)
            self._anomaly_scores.pop(ip, 0)
            # 保留 blocked 状态


# 全局实例
_monitor: IPMonitor = None


def get_ip_monitor() -> IPMonitor:
    """获取全局 IP 监测器"""
    global _monitor
    if _monitor is None:
        _monitor = IPMonitor()
    return _monitor
