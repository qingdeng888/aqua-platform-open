"""
错误跟踪与统计系统

功能：
1. 按错误类型分类统计（语法错误、运行时错误、网络错误等）
2. 按发生时间统计错误分布
3. 按模块/组件统计错误发生位置
4. 记录错误持续时间及修复状态
5. 统计错误对系统性能的影响程度
6. 日志自动清理策略
"""

import time
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("acu.error_tracker")

# 错误分类
ERROR_CATEGORIES = {
    "runtime": {"name": "运行时错误", "severity": "高"},
    "network": {"name": "网络错误", "severity": "中"},
    "database": {"name": "数据库错误", "severity": "高"},
    "auth": {"name": "认证错误", "severity": "低"},
    "ratelimit": {"name": "限流错误", "severity": "低"},
    "upstream": {"name": "上游错误", "severity": "中"},
    "validation": {"name": "参数验证错误", "severity": "低"},
    "timeout": {"name": "超时错误", "severity": "中"},
    "unknown": {"name": "未知错误", "severity": "中"},
}

@dataclass
class ErrorRecord:
    """单条错误记录"""
    error_type: str          # 错误类型
    category: str            # 分类
    module: str             # 模块名称
    message: str            # 错误消息摘要
    status_code: int        # HTTP状态码
    path: str               # 请求路径
    count: int = 1          # 发生次数
    first_seen: float = 0.0 # 首次发生时间
    last_seen: float = 0.0  # 最近发生时间
    resolved: bool = False  # 是否已解决
    resolved_at: Optional[float] = None  # 解决时间
    duration: float = 0.0   # 持续时间（秒）

class ErrorTracker:
    """
    错误跟踪器 - 统计、分类、跟踪
    """
    
    def __init__(self, max_records: int = 1000):
        self._records: Dict[str, ErrorRecord] = {}  # key: category:module:path
        self._history: deque = deque(maxlen=10000)  # 错误历史
        self._max_records = max_records
        self._hourly_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._daily_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last_cleanup = time.time()
        
    def record_error(self, category: str, module: str, message: str, 
                     status_code: int = 0, path: str = ""):
        """记录一条错误"""
        now = time.time()
        key = f"{category}:{module}:{path}"
        
        if key in self._records:
            record = self._records[key]
            record.count += 1
            record.last_seen = now
            record.message = message[:200]
            record.duration = now - record.first_seen
        else:
            self._records[key] = ErrorRecord(
                error_type=category,
                category=category,
                module=module,
                message=message[:200],
                status_code=status_code,
                path=path,
                first_seen=now,
                last_seen=now,
            )
            # 限制记录数量
            if len(self._records) > self._max_records:
                oldest = min(self._records.keys(), key=lambda k: self._records[k].last_seen)
                del self._records[oldest]
        
        # 更新小时/天统计
        hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._hourly_stats[hour_key][category] += 1
        self._daily_stats[day_key][category] += 1
        
        # 添加到历史
        self._history.append({
            "ts": now, "category": category, "module": module,
            "message": message[:100], "status": status_code, "path": path
        })
        
        # 自动清理过时统计
        self._auto_cleanup()
    
    def mark_resolved(self, category: str, module: str, path: str = ""):
        """标记错误为已解决"""
        key = f"{category}:{module}:{path}"
        if key in self._records:
            self._records[key].resolved = True
            self._records[key].resolved_at = time.time()
    
    def get_stats(self) -> dict:
        """获取详细统计"""
        now = time.time()
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # 按分类统计
        by_category = defaultdict(lambda: {"count": 0, "unresolved": 0})
        for record in self._records.values():
            cat = record.category
            by_category[cat]["count"] += record.count
            if not record.resolved:
                by_category[cat]["unresolved"] += 1
        
        # 按模块统计
        by_module = defaultdict(lambda: {"count": 0, "errors": []})
        for record in self._records.values():
            by_module[record.module]["count"] += record.count
            if len(by_module[record.module]["errors"]) < 5:
                by_module[record.module]["errors"].append(record.message)
        
        # 按严重程度
        severity_count = defaultdict(int)
        for record in self._records.values():
            cat_info = ERROR_CATEGORIES.get(record.category, ERROR_CATEGORIES["unknown"])
            severity_count[cat_info["severity"]] += record.count
        
        return {
            "total_errors": sum(r.count for r in self._records.values()),
            "unique_error_types": len(self._records),
            "unresolved_errors": sum(1 for r in self._records.values() if not r.resolved),
            "by_category": dict(by_category),
            "by_module": dict(by_module),
            "by_severity": dict(severity_count),
            "today_count": sum(self._daily_stats.get(day_key, {}).values()),
            "hourly_trend": dict(self._hourly_stats),
            "daily_trend": dict(self._daily_stats),
            "categories_info": ERROR_CATEGORIES,
        }
    
    def get_active_errors(self, max_age: float = 3600) -> list:
        """获取活跃错误（最近N秒内）"""
        now = time.time()
        return [
            {"key": k, **vars(r)} 
            for k, r in self._records.items() 
            if not r.resolved and now - r.last_seen < max_age
        ]
    
    def _auto_cleanup(self):
        """自动清理过期统计"""
        now = time.time()
        if now - self._last_cleanup < 300:  # 每5分钟清理一次
            return
        self._last_cleanup = now
        
        # 清理超过7天的小时统计
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d-%H")
        for key in list(self._hourly_stats.keys()):
            if key < cutoff:
                del self._hourly_stats[key]
        
        # 清理超过30天的日统计
        day_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        for key in list(self._daily_stats.keys()):
            if key < day_cutoff:
                del self._daily_stats[key]
        
        # 清理超过24小时未更新的已解决错误
        for key, record in list(self._records.items()):
            if record.resolved and record.resolved_at and now - record.resolved_at > 86400:
                del self._records[key]


# 全局实例
_error_tracker: Optional[ErrorTracker] = None


def get_error_tracker() -> ErrorTracker:
    """获取全局错误跟踪器"""
    global _error_tracker
    if _error_tracker is None:
        _error_tracker = ErrorTracker()
    return _error_tracker


def reset_error_tracker():
    """重置错误跟踪器（测试用）"""
    global _error_tracker
    _error_tracker = None


# 便捷函数
def track_error(category: str, module: str, message: str, status_code: int = 0, path: str = ""):
    """便捷记录错误"""
    get_error_tracker().record_error(category, module, message, status_code, path)
