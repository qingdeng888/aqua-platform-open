"""
IP监控模块 - 跟踪请求IP，检测异常模式，支持封禁

功能：
- 从 request_logs 记录唯一IP
- 检测异常模式（同IP多账号、快速密钥轮换、请求频率异常）
- 自动封禁高风险IP
- 支持手动封禁/解封
- CDN真实IP提取（CF-Connecting-IP / X-Forwarded-For）

异常检测算法：
1. 同IP多账号：同一IP关联≥3个不同client_id → 异常
2. 快速密钥轮换：同一IP短时间内使用多个API密钥 → 异常
3. 请求频率：1分钟内超过50次 → 异常，超过100次 → 严重异常
4. 自动封禁：异常分数≥80 自动封禁
"""
import json
import threading
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Set, List
from collections import defaultdict, deque
from fastapi import Request

from app.database import fetch_one, fetch_all, execute, utcnow
from app.middleware import get_client_ip

logger = logging.getLogger("acu.ipmonitor")


def get_real_client_ip(request: Request) -> str:
    """提取真实客户端IP

    v10.1修复：删除本模块自带的第二套IP提取逻辑（与 middleware 的提取规则
    漂移会导致"封禁的IP"和"日志记录的IP"不一致），统一委托 app.middleware.get_client_ip。
    函数名保留以兼容现有调用方（public_api）。
    """
    return get_client_ip(request)


class IpMonitor:
    """IP监控引擎 - 实时异常检测与自动防护"""

    # 自动封禁阈值
    AUTO_BLOCK_THRESHOLD = 80
    # 内存数据保留时长（秒）
    MEMORY_TTL = 3600
    # v10.1修复：封禁默认时长（小时），到期自动解封（原先永久封禁无过期）
    BLOCK_DURATION_HOURS = 24.0
    # client_ids / user_agents 记录上限（FIFO淘汰，防止单IP关联集合无限增长）
    MAX_TRACKED_PER_IP = 50

    def __init__(self):
        # 内存缓存：IP -> 最近关联的client_id记录（deque上限50，FIFO淘汰）
        self._ip_client_map: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.MAX_TRACKED_PER_IP))
        # 内存缓存：IP -> 最近使用的密钥前缀集合（检测密钥轮换）
        self._ip_key_map: Dict[str, set] = defaultdict(set)
        # 内存缓存：IP -> 最近请求时间戳队列
        self._ip_timestamps: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        # 内存缓存：IP -> 最近请求的去重客户端数统计
        self._ip_client_count: Dict[str, int] = defaultdict(int)
        # 封禁缓存（避免每次查数据库）
        self._blocked_cache: Dict[str, float] = {}  # ip -> expiry timestamp (0 = permanent)
        # 上次刷新封禁缓存时间
        self._last_blocked_refresh: float = 0
        # 上次自动清理时间
        self._last_cleanup: float = 0

    def init_table(self):
        """确保ip_monitor表存在"""
        try:
            execute("""
                CREATE TABLE IF NOT EXISTS ip_monitor (
                    ip TEXT PRIMARY KEY,
                    client_ids TEXT DEFAULT '[]',
                    first_seen TEXT,
                    last_seen TEXT,
                    request_count INT DEFAULT 0,
                    anomaly_score INT DEFAULT 0,
                    anomaly_reasons TEXT DEFAULT '[]',
                    blocked INT DEFAULT 0,
                    block_reason TEXT DEFAULT '',
                    blocked_at TEXT,
                    unblocked_at TEXT,
                    user_agents TEXT DEFAULT '[]'
                )
            """)
            execute("""
                CREATE TABLE IF NOT EXISTS ip_blocked (
                    ip TEXT PRIMARY KEY,
                    reason TEXT DEFAULT '',
                    blocked_at TEXT NOT NULL,
                    unblocked_at TEXT
                )
            """)
            logger.info("IP监控表已初始化")
        except Exception as e:
            logger.error(f"IP监控表初始化失败: {e}")

    def _refresh_blocked_cache(self):
        """从数据库刷新封禁IP缓存

        v10.1修复：根据 blocked_at + 默认封禁时长推算过期时间戳，
        服务重启后封禁TTL依然生效（无法解析的记录视为永久封禁）
        """
        now = time.time()
        if now - self._last_blocked_refresh < 60:
            return
        # 本方法会被事件循环内的 check_ip_blocked 同步调用（每请求一次），
        # 到期刷新改为后台线程查库，旧缓存继续生效，避免每60秒一次的同步DB阻塞事件循环
        self._last_blocked_refresh = now
        threading.Thread(target=self._do_refresh_blocked_cache, daemon=True).start()

    def _do_refresh_blocked_cache(self):
        """后台线程：执行封禁IP缓存的真实刷新"""
        try:
            rows = fetch_all("SELECT ip, blocked_at FROM ip_blocked WHERE unblocked_at IS NULL")
            self._blocked_cache = {row["ip"]: self._parse_blocked_until(row.get("blocked_at")) for row in rows}
        except Exception as e:
            logger.error(f"刷新封禁缓存失败: {e}")

    def _parse_blocked_until(self, blocked_at: Optional[str]) -> float:
        """由 blocked_at（utcnow() Z格式UTC时间）推算封禁过期时间戳；解析失败视为永久(0)"""
        if not blocked_at:
            return 0.0
        try:
            dt = datetime.fromisoformat(str(blocked_at).replace("Z", "+00:00"))
            return dt.timestamp() + self.BLOCK_DURATION_HOURS * 3600
        except (ValueError, TypeError):
            return 0.0

    def record_request(self, client_ip: str, client_id: str, key_prefix: str = "", user_agent: str = ""):
        """
        记录一次请求的IP信息（实时检测异常）

        参数:
            client_ip: 客户端真实IP（已穿透CDN）
            client_id: 客户端ID
            key_prefix: API密钥前缀（用于检测密钥轮换）
            user_agent: 用户代理字符串
        """
        if not client_ip or not client_id:
            return

        now_ts = time.time()
        now_str = utcnow()

        # 更新内存缓存（client_id 记录用 deque(maxlen=50) FIFO淘汰，v10.1修复集合无上限）
        client_ids_mem = self._ip_client_map[client_ip]
        if client_id not in client_ids_mem:
            client_ids_mem.append(client_id)
        if key_prefix:
            self._ip_key_map[client_ip].add(key_prefix)
        self._ip_timestamps[client_ip].append(now_ts)
        self._ip_client_count[client_ip] = len(client_ids_mem)

        # 运行实时异常检测
        anomaly_score, anomaly_reasons = self._detect_anomalies(client_ip)

        # 自动封禁：异常分数超过阈值
        if anomaly_score >= self.AUTO_BLOCK_THRESHOLD and not self.check_ip_blocked(client_ip):
            self.block_ip(client_ip, f"自动封禁(异常分数{anomaly_score}): {'; '.join(anomaly_reasons)}")
            logger.warning(f"IP自动封禁: {client_ip} score={anomaly_score} reasons={anomaly_reasons}")
            return  # 封禁后不必再写入数据库

        # 写入数据库（client_ids / user_agents 保持插入顺序并限制上限50，FIFO淘汰最旧）
        try:
            existing = fetch_one("SELECT * FROM ip_monitor WHERE ip = %s", (client_ip,))
            if existing:
                client_ids = json.loads(existing.get("client_ids", "[]")) or []
                if client_id not in client_ids:
                    client_ids.append(client_id)
                client_ids = client_ids[-self.MAX_TRACKED_PER_IP:]
                uas = json.loads(existing.get("user_agents", "[]")) or []
                if user_agent and user_agent not in uas:
                    uas.append(user_agent)
                uas = uas[-self.MAX_TRACKED_PER_IP:]

                execute(
                    "UPDATE ip_monitor SET "
                    "client_ids = %s, last_seen = %s, request_count = request_count + 1, "
                    "anomaly_score = %s, anomaly_reasons = %s, user_agents = %s "
                    "WHERE ip = %s",
                    (
                        json.dumps(client_ids),
                        now_str,
                        anomaly_score,
                        json.dumps(anomaly_reasons),
                        json.dumps(uas),
                        client_ip,
                    ),
                )
            else:
                client_ids = [client_id]
                uas = [user_agent] if user_agent else []
                execute(
                    "INSERT INTO ip_monitor (ip, client_ids, first_seen, last_seen, "
                    "request_count, anomaly_score, anomaly_reasons, blocked, user_agents) "
                    "VALUES (%s, %s, %s, %s, 1, %s, %s, 0, %s)",
                    (client_ip, json.dumps(client_ids), now_str, now_str,
                     anomaly_score, json.dumps(anomaly_reasons), json.dumps(uas)),
                )
        except Exception as e:
            logger.error(f"IP记录写入失败: {e}")

    def _detect_anomalies(self, ip: str) -> tuple:
        """
        综合异常检测算法

        检测维度：
        1. 同IP多账号：同一IP关联多个不同client_id
        2. 密钥轮换：同一IP短时间内使用多个不同API密钥
        3. 请求频率：短时间内大量请求

        返回:
            (anomaly_score, anomaly_reasons)
        """
        score = 0
        reasons = []

        now = time.time()

        # === 算法1: 同IP多账号检测 ===
        current_clients = set(self._ip_client_map.get(ip, deque()))  # deque转set用于并集
        num_clients = len(current_clients)

        # 合并数据库中的历史记录
        try:
            row = fetch_one("SELECT client_ids FROM ip_monitor WHERE ip = %s", (ip,))
            if row:
                db_ids = set(json.loads(row.get("client_ids", "[]")))
                all_clients = current_clients | db_ids
            else:
                all_clients = current_clients
        except Exception:
            all_clients = current_clients

        total_clients = len(all_clients)
        if total_clients >= 10:
            score += 60
            reasons.append(f"同IP关联{total_clients}个账号(严重)")
        elif total_clients >= 5:
            score += 50
            reasons.append(f"同IP关联{total_clients}个账号(高危)")
        elif total_clients >= 3:
            score += 30
            reasons.append(f"同IP关联{total_clients}个账号")

        # === 算法2: 密钥轮换检测 ===
        num_keys = len(self._ip_key_map.get(ip, set()))
        if num_keys >= 10:
            score += 40
            reasons.append(f"同IP使用{num_keys}个不同密钥(严重轮换)")
        elif num_keys >= 5:
            score += 25
            reasons.append(f"同IP使用{num_keys}个不同密钥")

        # === 算法3: 请求频率检测 ===
        timestamps = list(self._ip_timestamps.get(ip, deque()))
        recent_1m = 0
        recent_5m = 0
        if len(timestamps) >= 10:
            # 1分钟窗口
            recent_1m = sum(1 for t in timestamps if now - t < 60)
            # 5分钟窗口
            recent_5m = sum(1 for t in timestamps if now - t < 300)

            if recent_1m > 200:
                score += 40
                reasons.append(f"1分钟{recent_1m}次请求(严重)")
            elif recent_1m > 100:
                score += 30
                reasons.append(f"1分钟{recent_1m}次请求(高频)")
            elif recent_1m > 50:
                score += 15
                reasons.append(f"1分钟{recent_1m}次请求")

            # 5分钟窗口的持续性检测
            if recent_5m > 500:
                score += 20
                reasons.append(f"5分钟{recent_5m}次请求(持续高频)")

        # === 组合权重: 多维度叠加 ===
        # 如果同时触发了多账号+密钥轮换，额外加分
        if total_clients >= 3 and num_keys >= 3:
            score += 15
            reasons.append("多账号+密钥轮换(组合异常)")

        # 如果同时触发了多账号+高频请求，额外加分
        if total_clients >= 3 and recent_1m > 50:
            score += 10
            reasons.append("多账号+高频请求(组合异常)")

        return min(100, score), reasons[:5]

    def cleanup_old_data(self):
        """清理过期内存数据"""
        now = time.time()
        if now - self._last_cleanup < 300:  # 每5分钟清理一次
            return
        self._last_cleanup = now

        # 清理超过TTL的IP数据
        stale_ips = []
        for ip, timestamps in self._ip_timestamps.items():
            if timestamps and (now - timestamps[-1]) > self.MEMORY_TTL:
                stale_ips.append(ip)

        for ip in stale_ips:
            self._ip_client_map.pop(ip, None)
            self._ip_key_map.pop(ip, None)
            self._ip_timestamps.pop(ip, None)
            self._ip_client_count.pop(ip, None)

        if stale_ips:
            logger.debug(f"IP监控缓存清理: 移除{len(stale_ips)}个过期IP")

    def get_stats(self) -> dict:
        """获取IP监控统计"""
        try:
            total_row = fetch_one("SELECT COUNT(*) as cnt FROM ip_monitor")
            blocked_row = fetch_one("SELECT COUNT(*) as cnt FROM ip_blocked WHERE unblocked_at IS NULL")
            anomaly_row = fetch_one(
                "SELECT COUNT(*) as cnt FROM ip_monitor WHERE anomaly_score >= 30"
            )
            return {
                "total_ips": total_row["cnt"] if total_row else 0,
                "anomaly_count": anomaly_row["cnt"] if anomaly_row else 0,
                "blocked_count": blocked_row["cnt"] if blocked_row else 0,
            }
        except Exception as e:
            logger.error(f"获取IP监控统计失败: {e}")
            return {"total_ips": 0, "anomaly_count": 0, "blocked_count": 0}

    def get_anomalies(self, min_score: int = 30) -> list:
        """获取异常IP列表"""
        try:
            rows = fetch_all(
                "SELECT * FROM ip_monitor WHERE anomaly_score >= %s ORDER BY anomaly_score DESC LIMIT 100",
                (min_score,),
            )
            result = []
            for row in rows:
                r = dict(row)
                if isinstance(r.get("client_ids"), str):
                    r["client_ids"] = json.loads(r["client_ids"])
                if isinstance(r.get("anomaly_reasons"), str):
                    r["anomaly_reasons"] = json.loads(r["anomaly_reasons"])
                result.append(r)
            return result
        except Exception as e:
            logger.error(f"获取异常IP列表失败: {e}")
            return []

    def get_blocked_ips(self) -> list:
        """获取被封禁的IP列表"""
        try:
            return fetch_all("SELECT * FROM ip_blocked WHERE unblocked_at IS NULL ORDER BY blocked_at DESC")
        except Exception as e:
            logger.error(f"获取封禁IP列表失败: {e}")
            return []

    def check_ip_blocked(self, ip: str) -> bool:
        """检查IP是否被封禁（v10.1修复：封禁过期自动解封）"""
        self._refresh_blocked_cache()
        expiry = self._blocked_cache.get(ip)
        if expiry is None:
            return False
        if expiry > 0 and time.time() >= expiry:
            # 封禁已过期：自动解封
            logger.info(f"IP封禁已过期，自动解封: {ip}")
            self.unblock_ip(ip)
            return False
        return True

    def block_ip(self, ip: str, reason: str = "异常行为封禁", duration_hours: float = None):
        """封禁IP

        v10.1修复：新增封禁时长参数，默认24小时后自动过期解封（原先永久封禁无恢复路径）。
        duration_hours=None 使用默认24h；0 表示永久封禁（仅内存语义，
        缓存按 blocked_at 重算时会回落为默认时长）。
        """
        try:
            if duration_hours is None:
                duration_hours = self.BLOCK_DURATION_HOURS
            now_str = utcnow()
            execute(
                "INSERT INTO ip_blocked (ip, reason, blocked_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (ip) DO UPDATE SET reason = %s, blocked_at = %s, unblocked_at = NULL",
                (ip, reason, now_str, reason, now_str),
            )
            # 更新ip_monitor表中的封禁状态
            execute(
                "UPDATE ip_monitor SET blocked = 1, block_reason = %s, blocked_at = %s WHERE ip = %s",
                (reason, now_str, ip),
            )
            # 缓存过期时间戳（0=永久）
            self._blocked_cache[ip] = time.time() + duration_hours * 3600 if duration_hours > 0 else 0
            logger.warning(f"IP已封禁: {ip} reason={reason} duration={duration_hours}h")
        except Exception as e:
            logger.error(f"封禁IP失败: {e}")

    def unblock_ip(self, ip: str):
        """解封IP"""
        try:
            now_str = utcnow()
            execute(
                "UPDATE ip_blocked SET unblocked_at = %s WHERE ip = %s AND unblocked_at IS NULL",
                (now_str, ip),
            )
            execute(
                "UPDATE ip_monitor SET blocked = 0, unblocked_at = %s WHERE ip = %s",
                (now_str, ip),
            )
            self._blocked_cache.pop(ip, None)
            logger.info(f"IP已解封: {ip}")
        except Exception as e:
            logger.error(f"解封IP失败: {e}")


# 全局实例
_monitor: Optional[IpMonitor] = None


def get_ip_monitor() -> IpMonitor:
    global _monitor
    if _monitor is None:
        _monitor = IpMonitor()
        _monitor.init_table()
    return _monitor
