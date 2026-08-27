"""
运行时健康状态追踪 - v9.0

借鉴 Metapi `SiteRuntimeHealthState` 设计 + Portkey 健康检查机制

功能：
- 指数衰减惩罚分数
- 延迟指数移动平均 (EMA)
- 断路器模式 (Circuit Breaker)
- 主动健康探测
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List
from collections import defaultdict, deque

logger = logging.getLogger("acu.router.health")


@dataclass
class RuntimeHealthState:
    """运行时健康状态 - 借鉴 Metapi SiteRuntimeHealthState"""

    # 惩罚分数（指数衰减，半衰期10分钟）
    penalty_score: float = 0.0
    # 延迟指数移动平均 (ms)
    latency_ema_ms: float = 0.0
    # 最近成功/失败计数（半衰期30分钟）
    recent_success: int = 0
    recent_failure: int = 0
    recent_total: int = 0
    # 最近计数上次衰减时间戳（配合 RECENT_HALF_LIFE 使用）
    last_decay_ts: float = 0.0
    # 断路器级别 (0/1/2/3)
    breaker_level: int = 0
    breaker_until: float = 0.0
    # 瞬时失败连续计数
    transient_streak: int = 0
    # 最后更新/检查时间
    last_updated: float = 0.0
    # 历史记录
    success_history: deque = field(default_factory=lambda: deque(maxlen=100))
    failure_history: deque = field(default_factory=lambda: deque(maxlen=100))

    # 冷却等级（秒）
    COOLDOWN_LEVELS = [60, 300, 1800]  # 1min / 5min / 30min
    # 指数衰减半衰期（秒）
    PENALTY_HALF_LIFE = 600  # 10分钟
    # 最近成功/失败计数半衰期（秒）
    RECENT_HALF_LIFE = 1800  # 30分钟

    def record_success(self, latency_ms: float = 0.0):
        """记录成功"""
        now = time.time()
        # 修复：宣称30分钟半衰期但原先从未衰减 —— 先衰减再计数
        self._decay_recent(now)
        self.recent_success += 1
        self.recent_total += 1
        self.transient_streak = 0

        # 更新EMA延迟
        if self.latency_ema_ms == 0.0:
            self.latency_ema_ms = latency_ms
        else:
            alpha = 0.3
            self.latency_ema_ms = alpha * latency_ms + (1 - alpha) * self.latency_ema_ms

        # 惩罚衰减
        self._decay_penalty(now)

        # 成功时降低断路器级别
        if self.breaker_level > 0 and self.recent_success >= 10:
            self.breaker_level = max(0, self.breaker_level - 1)

        self.success_history.append(now)
        self.last_updated = now

    def record_failure(self, is_transient: bool = True) -> int:
        """
        记录失败
        返回: 冷却秒数（0表示不需要冷却）
        """
        now = time.time()
        # 修复：先按30分钟半衰期衰减，再计数
        self._decay_recent(now)
        self.recent_failure += 1
        self.recent_total += 1

        if is_transient:
            self.transient_streak += 1
            # 应用惩罚（指数衰减）
            self.penalty_score = self.penalty_score * 0.5 + 1.0

            # 断路器升级
            if self.transient_streak >= 3 and self.breaker_level < 3:
                self.breaker_level += 1
                level = self.breaker_level
                cooldown = self.COOLDOWN_LEVELS[min(level - 1, len(self.COOLDOWN_LEVELS) - 1)]
                self.breaker_until = now + cooldown
                logger.warning(f"断路器升级: level={level} cooldown={cooldown}s")
                self.failure_history.append(now)
                self.last_updated = now
                return cooldown
        else:
            # 非瞬时失败（如参数错误）- 不触发断路器
            pass

        self.failure_history.append(now)
        self.last_updated = now
        return 0

    def is_breaker_open(self) -> bool:
        """检查断路器是否开启"""
        if self.breaker_level > 0 and time.time() < self.breaker_until:
            return True
        if self.breaker_level > 0 and time.time() >= self.breaker_until:
            # 半开状态
            return False
        return False

    def get_health_multiplier(self) -> float:
        """获取健康度乘数 (0.0~1.0)"""
        now = time.time()
        self._decay_penalty(now)
        self._decay_recent(now)

        # 惩罚因子
        health = 1.0 / (1.0 + self.penalty_score)

        # 延迟惩罚
        if self.latency_ema_ms > 0:
            latency_penalty = min(self.latency_ema_ms / 2500.0, 1.0) * 0.35
            health *= (1.0 - latency_penalty)

        # 成功率因子
        if self.recent_total > 0:
            success_rate = self.recent_success / max(self.recent_total, 1)
            health *= (0.5 + success_rate * 0.5)

        return max(0.0, min(1.0, health))

    def get_health_score(self) -> float:
        """获取健康分 (0~100)"""
        return self.get_health_multiplier() * 100

    def _decay_penalty(self, now: float):
        """指数衰减惩罚分数"""
        if self.last_updated > 0 and self.penalty_score > 0:
            elapsed = now - self.last_updated
            half_lives = elapsed / self.PENALTY_HALF_LIFE
            self.penalty_score *= 0.5 ** half_lives
            if self.penalty_score < 0.001:
                self.penalty_score = 0.0

    def _decay_recent(self, now: float):
        """最近成功/失败计数按30分钟半衰期衰减

        修复：注释宣称"半衰期30分钟"但原先没有任何衰减实现，
        导致历史成功/失败计数无限累积、成功率被旧数据永久污染。
        实现：每经过一个半衰期窗口计数减半（不足整窗按比例衰减），
        首次调用只记录 last_decay_ts 不衰减。
        """
        if self.last_decay_ts <= 0:
            self.last_decay_ts = now
            return
        elapsed = now - self.last_decay_ts
        if elapsed <= 0:
            return
        factor = 0.5 ** (elapsed / self.RECENT_HALF_LIFE)
        self.recent_success = int(self.recent_success * factor)
        self.recent_failure = int(self.recent_failure * factor)
        self.recent_total = int(self.recent_total * factor)
        self.last_decay_ts = now


class HealthProbeService:
    """
    主动健康探测服务 - 借鉴 Metapi modelAvailabilityProbeService.ts

    定时探测所有可用模型-密钥组合的真实可用性
    """

    def __init__(self):
        self._states: Dict[str, Dict[str, RuntimeHealthState]] = defaultdict(dict)
        self._last_probe_time: float = 0.0
        self._probe_interval: float = 300.0  # 5分钟

    def get_state(self, key_id: str, model: str) -> RuntimeHealthState:
        """获取/创建健康状态"""
        if model not in self._states[key_id]:
            self._states[key_id][model] = RuntimeHealthState()
        return self._states[key_id][model]

    def record_success(self, key_id: str, model: str, latency_ms: float = 0.0):
        """记录成功"""
        state = self.get_state(key_id, model)
        state.record_success(latency_ms)

    def record_failure(self, key_id: str, model: str, is_transient: bool = True) -> int:
        """
        记录失败
        返回: 冷却秒数
        """
        state = self.get_state(key_id, model)
        return state.record_failure(is_transient)

    def get_best_model_key(self, model: str, exclude_keys: set = None) -> Optional[Tuple[str, float]]:
        """
        获取某模型的最佳健康密钥
        返回: (key_id, health_multiplier)
        """
        best_key = None
        best_health = -1.0
        exclude = exclude_keys or set()

        for key_id, models in self._states.items():
            if key_id in exclude:
                continue
            state = models.get(model)
            if state is None:
                continue
            if state.is_breaker_open():
                continue
            health = state.get_health_multiplier()
            if health > best_health:
                best_health = health
                best_key = key_id

        if best_key:
            return (best_key, best_health)
        return None

    def get_healthy_keys_for_model(self, model: str, min_health: float = 0.1) -> List[str]:
        """获取某模型的健康密钥列表"""
        healthy = []
        for key_id, models in self._states.items():
            state = models.get(model)
            if state is None:
                continue
            if state.is_breaker_open():
                continue
            if state.get_health_multiplier() >= min_health:
                healthy.append(key_id)
        return healthy

    def get_all_health(self) -> dict:
        """获取所有健康状态（用于管理后台）"""
        result = {}
        for key_id, models in self._states.items():
            for model, state in models.items():
                result[f"{key_id}:{model}"] = {
                    "health_score": round(state.get_health_score(), 1),
                    "penalty_score": round(state.penalty_score, 3),
                    "latency_ema_ms": round(state.latency_ema_ms, 1),
                    "breaker_level": state.breaker_level,
                    "breaker_open": state.is_breaker_open(),
                    "recent_success": state.recent_success,
                    "recent_failure": state.recent_failure,
                    "transient_streak": state.transient_streak,
                }
        return result


# 全局实例
_health_probe: Optional[HealthProbeService] = None


def get_health_probe() -> HealthProbeService:
    """获取全局健康探测服务实例"""
    global _health_probe
    if _health_probe is None:
        _health_probe = HealthProbeService()
    return _health_probe
