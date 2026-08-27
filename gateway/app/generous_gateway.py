"""
慷慨型网关模块 (Generous Gateway Module)
设计理念: 不限量、不限速
功能:
1. 免费额度聚合池 - 聚合多个供应商的免费额度
2. 多供应商负载均衡 - 智能分配请求到最优供应商
3. 故障自动转移 - 供应商不可用时自动切换
4. 额度水位线监控 - 实时跟踪各供应商额度消耗
"""

import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ProviderStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class ProviderInfo:
    """供应商信息"""
    provider_id: str
    name: str
    base_url: str
    api_key_prefix: str  # First 8 chars for identification
    weight: float = 1.0
    status: ProviderStatus = ProviderStatus.HEALTHY
    total_quota: int = 0  # Total free quota in tokens (0 = unlimited)
    used_quota: int = 0
    rpm_limit: int = 0  # 0 = unlimited
    current_rpm: int = 0
    consecutive_errors: int = 0
    last_error_time: float = 0
    last_success_time: float = 0
    avg_latency_ms: int = 0
    supports_streaming: bool = True
    supports_tools: bool = True
    supported_models: List[str] = field(default_factory=list)


class FreeQuotaPool:
    """免费额度聚合池 - 将多个供应商的免费额度聚合为一个统一的额度池"""
    
    def __init__(self):
        self._providers: Dict[str, ProviderInfo] = {}
        self._lock = threading.Lock()
    
    def register_provider(self, provider: ProviderInfo):
        """注册供应商到额度池"""
        with self._lock:
            self._providers[provider.provider_id] = provider
            logger.info(f"供应商已注册: {provider.name} (额度: {provider.total_quota or '无限'})")
    
    def get_total_available_quota(self) -> int:
        """获取总可用额度 (0表示无限)"""
        with self._lock:
            total = 0
            has_unlimited = False
            for p in self._providers.values():
                if p.status == ProviderStatus.DOWN:
                    continue
                if p.total_quota == 0:
                    has_unlimited = True
                    continue
                total += max(0, p.total_quota - p.used_quota)
            return 0 if has_unlimited else total
    
    def consume_quota(self, provider_id: str, tokens: int) -> bool:
        """消耗指定供应商的额度"""
        with self._lock:
            provider = self._providers.get(provider_id)
            if not provider:
                return False
            if provider.total_quota > 0:  # 0 = unlimited
                if provider.used_quota + tokens > provider.total_quota:
                    return False  # 额度不足
            provider.used_quota += tokens
            return True
    
    def get_provider_usage(self) -> Dict[str, dict]:
        """获取各供应商额度使用情况"""
        with self._lock:
            result = {}
            for pid, p in self._providers.items():
                result[pid] = {
                    "name": p.name,
                    "status": p.status.value,
                    "total_quota": p.total_quota,
                    "used_quota": p.used_quota,
                    "remaining": p.total_quota - p.used_quota if p.total_quota > 0 else -1,
                    "usage_percent": round(p.used_quota / p.total_quota * 100, 2) if p.total_quota > 0 else 0,
                }
            return result


class GenerousLoadBalancer:
    """慷慨型负载均衡器 - 多供应商智能调度"""
    
    def __init__(self, quota_pool: FreeQuotaPool):
        self._pool = quota_pool
        self._failover_threshold = 3  # 连续3次错误触发故障转移
        self._recovery_interval = 300  # 5分钟后尝试恢复
    
    def select_provider(self, model: str) -> Optional[ProviderInfo]:
        """选择最优供应商处理请求
        
        选择逻辑:
        1. 过滤掉DOWN状态的供应商
        2. 过滤掉不支持该模型的供应商
        3. 优先选择HEALTHY且额度充足的供应商
        4. 在候选中按权重+健康度评分选择
        """
        candidates = []
        with self._pool._lock:
            for provider in self._pool._providers.values():
                # Skip down providers (unless recovery time)
                if provider.status == ProviderStatus.DOWN:
                    if time.time() - provider.last_error_time < self._recovery_interval:
                        continue
                    # Try recovery
                
                # Check model support
                if provider.supported_models and model not in provider.supported_models:
                    continue
                
                # Check quota
                if provider.total_quota > 0 and provider.used_quota >= provider.total_quota:
                    continue
                
                # Check RPM
                if provider.rpm_limit > 0 and provider.current_rpm >= provider.rpm_limit:
                    continue
                
                # Score: weight * health_bonus * latency_factor
                health_bonus = 1.0 if provider.status == ProviderStatus.HEALTHY else 0.7
                latency_factor = 1.0 / (1 + provider.avg_latency_ms / 1000) if provider.avg_latency_ms > 0 else 1.0
                score = provider.weight * health_bonus * latency_factor
                
                candidates.append((score, provider))
        
        if not candidates:
            return None
        
        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    
    def report_success(self, provider_id: str, latency_ms: int, tokens: int):
        """报告请求成功"""
        with self._pool._lock:
            provider = self._pool._providers.get(provider_id)
            if provider:
                provider.consecutive_errors = 0
                provider.last_success_time = time.time()
                provider.avg_latency_ms = int(
                    0.8 * provider.avg_latency_ms + 0.2 * latency_ms
                ) if provider.avg_latency_ms > 0 else latency_ms
                self._pool.consume_quota(provider_id, tokens)
                if provider.status == ProviderStatus.DEGRADED:
                    provider.status = ProviderStatus.HEALTHY
    
    def report_error(self, provider_id: str, is_5xx: bool = False):
        """报告请求失败,触发故障转移逻辑"""
        with self._pool._lock:
            provider = self._pool._providers.get(provider_id)
            if not provider:
                return
            provider.consecutive_errors += 1
            provider.last_error_time = time.time()
            
            if provider.consecutive_errors >= self._failover_threshold:
                if is_5xx:
                    provider.status = ProviderStatus.DOWN
                    logger.warning(f"供应商 {provider.name} 标记为DOWN (连续{provider.consecutive_errors}次5xx错误)")
                else:
                    provider.status = ProviderStatus.DEGRADED
                    logger.warning(f"供应商 {provider.name} 标记为DEGRADED (连续{provider.consecutive_errors}次错误)")
    
    def get_status(self) -> dict:
        """获取负载均衡器状态"""
        return {
            "providers": self._pool.get_provider_usage(),
            "total_available_quota": self._pool.get_total_available_quota(),
            "failover_threshold": self._failover_threshold,
            "recovery_interval_seconds": self._recovery_interval,
        }


# Global instances
quota_pool = FreeQuotaPool()
load_balancer = GenerousLoadBalancer(quota_pool)
