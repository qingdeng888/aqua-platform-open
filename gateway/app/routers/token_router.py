"""
TokenRouter v9.0 - 模块化路由引擎

借鉴 Metapi `tokenRouter.ts` 的模块化设计：
- 路由匹配 (findRoute)
- 通道选择 (selectChannel) - 多维加权评分
- 故障切换 (Failover) - 重试 + 切换通道
- 粘性会话 (Sticky Session)
"""
import time
import logging
import random
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any, Callable
from collections import defaultdict

from app.routers.health import get_health_probe, RuntimeHealthState, HealthProbeService

logger = logging.getLogger("acu.router")


# ========== 数据模型 ==========

@dataclass
class Channel:
    """路由通道 - 对应一个密钥+模型的组合"""
    key_id: str
    model: str
    api_key: str
    provider: str  # nvidia / openai / anthropic / gemini
    base_url: str
    weight: float = 1.0
    rpm_limit: int = 40
    max_concurrency: int = 10


@dataclass
class Route:
    """路由规则"""
    id: str
    model_pattern: str  # 模型匹配模式 (exact / prefix / regex)
    channels: List[str]  # 通道名称列表
    strategy: str = "weighted"  # weighted / round_robin / stable_first / cost_first
    fallback: Optional[str] = None  # 失败时的备用路由


@dataclass
class SelectContext:
    """选择上下文"""
    model: str
    client_id: str = ""
    session_id: str = ""
    budget_mode: bool = False
    max_cost: float = 0.0
    exclude_keys: set = field(default_factory=set)


# ========== 路由引擎 ==========

class ChannelSelector:
    """
    通道选择器 - 多维加权评分

    借鉴 Metapi calculateWeightedSelection：
    score = baseWeight * healthMultiplier * loadMultiplier * successMultiplier
    """

    def __init__(self, health_probe: HealthProbeService):
        self._health_probe = health_probe
        self._channels: Dict[str, Channel] = {}
        self._routes: Dict[str, Route] = {}

    def register_channel(self, channel: Channel):
        """注册通道"""
        self._channels[channel.key_id] = channel

    def register_route(self, route: Route):
        """注册路由"""
        self._routes[route.id] = route

    def calculate_score(self, channel: Channel, context: SelectContext) -> float:
        """
        计算通道权重评分

        公式借鉴 Metapi：
        score = baseWeight * healthMultiplier * loadMultiplier * successRate
        """
        # 基础权重
        base = channel.weight

        # 健康度乘数
        state = self._health_probe.get_state(channel.key_id, context.model)
        health_multiplier = state.get_health_multiplier()

        # 负载因子
        healthy_keys = self._health_probe.get_healthy_keys_for_model(context.model)
        total_capacity = len(healthy_keys) if healthy_keys else 1
        load_multiplier = min(1.0, total_capacity / max(total_capacity, 1))

        # 成功率因子
        if state.recent_total > 0:
            success_rate = state.recent_success / max(state.recent_total, 1)
            success_multiplier = 0.5 + success_rate * 0.5
        else:
            success_multiplier = 1.0

        score = base * health_multiplier * load_multiplier * success_multiplier
        logger.debug(
            f"通道评分: key={channel.key_id[:8]} model={context.model} "
            f"score={score:.4f} health={health_multiplier:.2f} "
            f"load={load_multiplier:.2f} success={success_multiplier:.2f}"
        )
        return score

    def select_weighted(self, candidates: List[Channel], context: SelectContext) -> Optional[Channel]:
        """
        加权随机选择 - 轮盘赌算法

        借鉴 Metapi 的轮盘赌选择 (Math.random() * totalContribution)
        """
        scored = [(ch, self.calculate_score(ch, context)) for ch in candidates]
        scored = [(ch, s) for ch, s in scored if s > 0]

        if not scored:
            return None

        total = sum(s for _, s in scored)
        pick = random.uniform(0, total)
        cumulative = 0.0
        for ch, s in scored:
            cumulative += s
            if pick <= cumulative:
                return ch
        return scored[-1][0]

    def select_round_robin(self, candidates: List[Channel], context: SelectContext) -> Optional[Channel]:
        """轮询选择"""
        self._rr_index = getattr(self, '_rr_index', {})
        key = context.model
        idx = self._rr_index.get(key, 0)
        if not candidates:
            return None
        channel = candidates[idx % len(candidates)]
        self._rr_index[key] = (idx + 1) % len(candidates)
        return channel

    def select_stable_first(self, candidates: List[Channel], context: SelectContext) -> Optional[Channel]:
        """稳定优先 - 选择健康度最高的通道"""
        best = None
        best_score = -1.0
        for ch in candidates:
            score = self.calculate_score(ch, context)
            if score > best_score:
                best_score = score
                best = ch
        return best


class TokenRouter:
    """
    TokenRouter v9.0 - 智能路由引擎

    借鉴 Metapi tokenRouter.ts：
    - findRoute: 按模型名称匹配路由规则
    - selectChannel: 按策略选择最优通道
    - execute: 执行代理请求（含重试和故障切换）
    """

    def __init__(self, health_probe: HealthProbeService):
        self._selector = ChannelSelector(health_probe)
        self._health = health_probe
        self._strategies: Dict[str, Callable] = {
            "weighted": self._selector.select_weighted,
            "round_robin": self._selector.select_round_robin,
            "stable_first": self._selector.select_stable_first,
        }
        self._stats = {"route_calls": 0, "fallbacks": 0, "failures": 0}

    def register_channel(self, channel: Channel):
        self._selector.register_channel(channel)

    def register_route(self, route: Route):
        self._selector.register_route(route)

    def find_route(self, model: str) -> Optional[Route]:
        """按模型名称查找匹配的路由"""
        # 精确匹配优先
        for rid, route in self._selector._routes.items():
            if route.model_pattern == model:
                return route

        # 前缀匹配
        for rid, route in self._selector._routes.items():
            if route.model_pattern.endswith("*"):
                prefix = route.model_pattern[:-1]
                if model.startswith(prefix):
                    return route

        return None

    def _get_candidates(self, route: Route, context: SelectContext) -> List[Channel]:
        """获取候选通道列表"""
        candidates = []
        for ch_id in route.channels:
            if ch_id in context.exclude_keys:
                continue
            ch = self._selector._channels.get(ch_id)
            if ch:
                # 检查断路器
                state = self._health.get_state(ch.key_id, context.model)
                if state.is_breaker_open():
                    logger.debug(f"跳过断路器开启通道: {ch.key_id[:8]}")
                    continue
                candidates.append(ch)
        return candidates

    def select_channel(self, model: str, exclude_keys: set = None) -> Optional[Channel]:
        """
        选择最优通道

        借鉴 Metapi 的多层路由选择：
        1. 查找路由规则
        2. 获取候选通道
        3. 按策略选择最优通道
        """
        self._stats["route_calls"] += 1
        context = SelectContext(model=model, exclude_keys=exclude_keys or set())

        # 查找路由
        route = self.find_route(model)
        if not route:
            # 无路由规则，尝试从所有通道中直接选择
            candidates = list(self._selector._channels.values())
            candidates = [c for c in candidates if c.key_id not in context.exclude_keys]
        else:
            candidates = self._get_candidates(route, context)

        if not candidates:
            self._stats["failures"] += 1
            return None

        # 按策略选择
        strategy_fn = self._strategies.get(route.strategy if route else "weighted", self._strategies["weighted"])
        channel = strategy_fn(candidates, context)

        if not channel:
            # 尝试备用路由
            if route and route.fallback:
                self._stats["fallbacks"] += 1
                fallback_route = self._selector._routes.get(route.fallback)
                if fallback_route:
                    fb_candidates = self._get_candidates(fallback_route, context)
                    channel = self._strategies["weighted"](fb_candidates, context)

        return channel

    def record_success(self, key_id: str, model: str, latency_ms: float = 0.0):
        """记录成功"""
        self._health.record_success(key_id, model, latency_ms)

    def record_failure(self, key_id: str, model: str, is_transient: bool = True) -> int:
        """
        记录失败
        返回: 冷却秒数
        """
        return self._health.record_failure(key_id, model, is_transient)

    def get_stats(self) -> dict:
        """获取路由统计"""
        return {
            **self._stats,
            "channels": len(self._selector._channels),
            "routes": len(self._selector._routes),
        }


# 全局实例
_router: Optional[TokenRouter] = None


def get_router() -> TokenRouter:
    """获取全局路由引擎实例"""
    global _router
    if _router is None:
        _router = TokenRouter(get_health_probe())
    return _router
