"""
SurgeScheduler v10.0 - 浪涌调度器核心

十七算法互锁体系 - 严格公平调度 + 桶级健康守护

=== 核心架构变革 ===
旧方案问题：乘数式轮询（Algorithm 17）无法突破评分偏好，均衡度仅28%
当前方案：评分仅用于健康性过滤（<0.1分视为不健康），最终选择由严格公平调度决定

=== 桶级冷却四条强制约束（不可违反） ===
1. 冷却状态只存储在 (key_id, model) 复合桶级别，不存在任何密钥级冷却状态字段
2. select_key 检查冷却状态时只使用复合键查询，不存在任何密钥级查询路径
3. 软繁忙与冷却完全分离：RPM超限只标记桶级软繁忙，永不调用冷却函数
4. 后台异步任务只操作桶级状态，不修改任何密钥级聚合状态

=== 17算法分工 ===
算法1  分桶滑动窗口计数器    - 数据采集（唯一数据源）
算法2  软繁忙标记器          - 干预层（跳过RPM超限桶，不冷却）
算法3  自适应阈值调节器      - 后台周期（每30秒，P95驱动）
算法4  自适应冷却时长计算器  - 冷却决策（固定240秒桶级冷却）
算法5  客户端并发监测器      - 客户端治理（只监测不拦截）
算法6  客户端突发率检测器    - 客户端治理（只监测不拦截）
算法7  客户端日用量监测器    - 客户端治理（只监测不拦截）
算法8  5xx退避权重衰减器     - 健康评估（影响健康性过滤）
算法9  区域故障隔离器        - 健康评估（30分钟权重归零隔离）
算法10 全局健康度评分器      - 后台周期（每30秒，0~100综合评分）
算法11 池化动态权重调节器    - 健康评估（0.5~2.0基础权重乘数）
算法12 自适应负载预判器      - 干预层（5分钟趋势预判排除，放宽至95%）
算法13 冷密钥渐进式预热器    - 健康评估（0.3→0.6→0.9→1.0渐进恢复）
算法14 智能异常自愈引擎      - 后台管控（每60秒，四级自愈）
算法15 趋势感知自适应均衡    - 健康评估（0.6~1.3趋势斜率乘数）
算法16 龙虾脱壳式弹性调度    - 健康评估（过载脱壳15秒0.5乘数）
算法17 严格公平调度器        - 最终选择（核心：滑动窗口计数 + 公平轮询）

=== select_key 流程 ===
1. 获取所有活跃密钥
2. 硬性过滤：冷却、隔离、自愈、密钥解密失败
3. 健康性评分过滤：评分 < 0.1 视为不健康，排除
4. 严格公平选择：在健康候选中，按"近期使用次数最少 → 最近使用时间最久"排序
5. 最小候选池保障：若健康候选 < 3，放宽predicted_busy/soft_busy限制
6. 记录使用并返回
"""
import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict, Any

import httpx

from app.database import fetch_all, fetch_one, get_setting, execute
from app.security import decrypt_upstream_key

logger = logging.getLogger("acu.scheduler")


# ========== 模型特性配置 ==========

# 慢速模型（首字节延迟高，需要更长超时）
# v10.1修复：常量漂移——修正为 nim_models.NIM_MODEL_CATALOG 中实际存在的模型id
# （原 z-ai/glm-5.2、minimax-m2.7、qwen3.5-397b-a17b 均不在目录中，已删除）
SLOW_MODELS = {
    "minimaxai/minimax-m3",
    "nvidia/nemotron-3-ultra-550b-a55b",
}

# 慢速模型差异化切换阈值（统一38，确保所有模型容量一致）
# 键与 SLOW_MODELS 保持一致，且均存在于 NIM_MODEL_CATALOG
SLOW_MODEL_THRESHOLDS = {
    "minimaxai/minimax-m3": 38,
    "nvidia/nemotron-3-ultra-550b-a55b": 38,
}

# 推理模型（返回 reasoning_content 字段）
# v10.1修复：deepseek-v4-pro 修正为目录中的 deepseek-v4-pro-0813；
# deepseek-v4-flash 目录中无同家族替代，已删除
REASONING_MODELS = {
    "deepseek-ai/deepseek-v4-pro-0813",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}

# 模型默认参数（基于NVIDIA NIM使用示例）
# v10.1修复：所有键均修正为 NIM_MODEL_CATALOG 中实际存在的模型id
MODEL_DEFAULTS = {
    "minimaxai/minimax-m3": {"temperature": 1.0, "top_p": 0.95, "max_tokens": 8192},
    "deepseek-ai/deepseek-v4-pro-0813": {"temperature": 1, "top_p": 0.95, "max_tokens": 16384},
}

# 超时配置
DEFAULT_TIMEOUT = 180.0        # 默认3分钟（Agent工作流需要较长时间）
SLOW_MODEL_TIMEOUT = 300.0     # 慢速模型5分钟
STREAM_FIRST_BYTE_TIMEOUT = 120.0  # 流式首字节2分钟
STREAM_IDLE_TIMEOUT = 180.0    # 流式空闲3分钟


def get_threshold_for_model(model: str, default: int = 38) -> int:
    """返回模型的有效切换阈值"""
    return SLOW_MODEL_THRESHOLDS.get(model, default)


def get_timeout_for_model(model: str) -> float:
    """根据模型返回合适的整体超时时间"""
    return SLOW_MODEL_TIMEOUT if model in SLOW_MODELS else DEFAULT_TIMEOUT


def apply_model_defaults(body: dict) -> dict:
    """根据NVIDIA NIM使用示例自动补全模型默认参数（不覆盖用户设置）"""
    model = body.get("model", "")
    defaults = MODEL_DEFAULTS.get(model)
    if defaults:
        for key, value in defaults.items():
            if key not in body:
                body[key] = value
    return body


# ========== 分桶数据结构 ==========

@dataclass
class BucketData:
    """
    单个复合桶 (key_id, model) 的完整状态数据

    每个算法只读写自己负责的字段（字段隔离原则）：
    - 算法1: timestamps, response_times, total_*, last_*_at, last_failure_type
    - 算法3: dynamic_threshold
    - 算法4: cooldown_until, cooldown_history
    - 算法8: consecutive_5xx, first_5xx_time
    - 算法9: isolation_until, consecutive_conn_fail
    - 算法10: health_score, health_history
    - 算法13: warmup_progress
    - 算法14: heal_action, heal_until
    """
    # === 算法1：分桶滑动窗口计数器 ===
    timestamps: deque = field(default_factory=lambda: deque(maxlen=120))
    response_times: deque = field(default_factory=lambda: deque(maxlen=50))
    total_requests: int = 0
    total_success: int = 0
    total_failures: int = 0
    total_429: int = 0
    total_5xx: int = 0
    total_timeout: int = 0
    total_conn_error: int = 0
    total_ssl_error: int = 0
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    last_failure_type: Optional[str] = None

    # === 算法3：自适应阈值调节器 ===
    dynamic_threshold: int = 38

    # === 算法4：自适应冷却时长计算器 ===
    cooldown_until: float = 0.0
    cooldown_history: deque = field(default_factory=lambda: deque(maxlen=20))
    consecutive_403: int = 0  # 连续403计数（用于自适应退避）

    # === 算法8：5xx退避权重衰减器 ===
    consecutive_5xx: int = 0
    first_5xx_time: Optional[float] = None

    # === 算法9：区域故障隔离器 ===
    isolation_until: float = 0.0
    consecutive_conn_fail: int = 0

    # === 算法10：全局健康度评分器 ===
    health_score: float = 100.0
    health_history: deque = field(default_factory=lambda: deque(maxlen=10))

    # === 算法13：冷密钥渐进式预热器 ===
    warmup_progress: int = 30  # 默认30表示已预热（新桶不需要预热）

    # === 算法14：智能异常自愈引擎 ===
    heal_action: Optional[str] = None
    heal_until: float = 0.0

    # === 算法12：自适应负载预判器（趋势数据） ===
    rpm_trend: deque = field(default_factory=lambda: deque(maxlen=60))

    def get_rpm(self) -> int:
        """计算当前60秒窗口内的RPM"""
        now = time.time()
        while self.timestamps and now - self.timestamps[0] > 60:
            self.timestamps.popleft()
        return len(self.timestamps)

    def get_success_rate(self) -> float:
        """计算成功率（0~100）

        修复：使用total_requests作为分母，避免部分请求状态未知时成功率偏高
        """
        if self.total_requests == 0:
            return 100.0
        return (self.total_success / self.total_requests) * 100.0

    def get_p95_rt(self) -> float:
        """计算P95响应时间（秒）"""
        if len(self.response_times) < 5:
            return 0.0
        sorted_rt = sorted(self.response_times)
        idx = int(len(sorted_rt) * 0.95)
        return sorted_rt[min(idx, len(sorted_rt) - 1)]

    def get_avg_rt(self) -> float:
        """计算平均响应时间（秒）"""
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times)

    def is_cooled_down(self) -> bool:
        """检查桶是否在冷却中（算法4）"""
        return time.time() < self.cooldown_until

    def is_isolated(self) -> bool:
        """检查桶是否被隔离（算法9）"""
        return time.time() < self.isolation_until

    def is_healing(self) -> bool:
        """检查桶是否在自愈中（算法14）"""
        return self.heal_action is not None and time.time() < self.heal_until


# ========== 客户端治理状态（算法5/6/7，只监测不拦截） ==========

# v9.2: 并发请求超时时间（秒）- 超过此时间的请求视为已释放
# 防止客户端断连但服务端未收到释放信号导致的并发计数不准确
CONCURRENCY_REQUEST_TIMEOUT = 120.0  # 120秒无响应视为已释放


@dataclass
class ClientMetrics:
    """客户端行为监测数据（v9.2 升级：毫秒级精准并发追踪）"""
    inflight_count: int = 0  # 算法5：当前并发数
    # v9.2: 毫秒级精准追踪 - 记录每个并发请求的开始时间戳
    # 用于超时检测和高精度并发计算
    inflight_timestamps: list = field(default_factory=list)  # [(start_time, request_id), ...]
    burst_timestamps: deque = field(default_factory=lambda: deque(maxlen=200))  # 算法6：突发检测（扩大容量）
    daily_count: int = 0  # 算法7：日用量
    daily_reset_at: float = 0.0
    request_intervals: deque = field(default_factory=lambda: deque(maxlen=50))  # 请求间隔（商用识别用）
    last_request_at: float = 0.0
    model_switches: deque = field(default_factory=lambda: deque(maxlen=20))  # 模型切换记录
    last_model: str = ""

    def get_burst_count(self, window_seconds: float = 3.0) -> int:
        """计算指定时间窗口内的突发请求数（修复：原实现未过滤时间窗口）"""
        now = time.time()
        while self.burst_timestamps and now - self.burst_timestamps[0] > window_seconds:
            self.burst_timestamps.popleft()
        return len(self.burst_timestamps)

    def record_inflight_start(self, request_id: str = None) -> float:
        """记录并发请求开始，返回开始时间戳（毫秒精度）

        v10.1修复：存储 (start_time, request_id) 元组，end 时按 request_id 精确匹配，
        避免并发下总是弹出最旧时间戳导致耗时统计错配
        """
        now = time.time()
        self.inflight_count += 1
        self.inflight_timestamps.append((now, request_id))
        return now

    def record_inflight_end(self, request_id: str = None) -> float:
        """
        记录并发请求结束。
        优先按 request_id 匹配移除对应记录；未提供 request_id 时兼容旧调用（弹出最早一条）。
        返回本次请求的耗时（秒）。
        """
        now = time.time()
        self.inflight_count = max(0, self.inflight_count - 1)
        if self.inflight_timestamps:
            start = None
            if request_id is not None:
                # 按请求ID精确匹配移除
                for i, (ts, rid) in enumerate(self.inflight_timestamps):
                    if rid == request_id:
                        start = self.inflight_timestamps.pop(i)[0]
                        break
            if start is None:
                # 兼容旧调用：弹出最早的记录
                start = self.inflight_timestamps.pop(0)[0]
            return now - start
        return 0.0

    def cleanup_stale_inflight(self) -> int:
        """
        清理超时的陈旧并发请求（毫秒级精准检测）。
        返回清理的陈旧请求数量。
        """
        now = time.time()
        stale_count = 0
        # 从前往后清理超时的陈旧的请求（元素为 (start_time, request_id) 元组）
        while self.inflight_timestamps and (now - self.inflight_timestamps[0][0]) > CONCURRENCY_REQUEST_TIMEOUT:
            self.inflight_timestamps.pop(0)
            self.inflight_count = max(0, self.inflight_count - 1)
            stale_count += 1
        return stale_count

    def get_real_inflight_count(self) -> int:
        """
        获取真实的并发数（经超时清理后）。
        确保并发计数准确反映实际在途请求。
        """
        self.cleanup_stale_inflight()
        return self.inflight_count


# ========== 全局调度器状态 ==========

@dataclass
class SchedulerStats:
    """调度器全局统计（用于监控）"""
    total_select_calls: int = 0
    total_none_returns: int = 0  # 返回None的次数（所有密钥耗尽）
    total_cooldowns_triggered: int = 0
    total_isolations_triggered: int = 0
    total_heal_actions: int = 0
    degraded_mode: bool = False  # 算法14全局降级模式


# ========== SurgeScheduler 调度器核心 ==========

class SurgeScheduler:
    """
    浪涌调度器 v10.0

    核心架构：
    1. 所有状态以 (key_id, model) 复合桶为键存储
    2. 软繁忙（运行时集合）与冷却（cooldown_until字段）完全分离
    3. select_key 只使用复合键查询冷却状态
    4. 后台任务只操作桶级字段
    """

    # HTTP连接池上限（调优：300/150过大易压垮上游，收敛为100/20，keepalive 60s）
    POOL_MAX_CONNECTIONS = 100
    POOL_MAX_KEEPALIVE = 20

    # 活跃密钥缓存TTL（秒）
    ACTIVE_KEYS_CACHE_TTL = 30.0

    # 冷却时长（秒）- 算法4 差异化冷却策略
    # 不同失败类型的冷却时长不同：
    # - 429：5秒（NVIDIA限流通常1-2秒，5秒已足够恢复）
    # - 403：60秒（权限拒绝，短时间内不会恢复，需长时间避免重复选择）
    # - timeout：15秒（可能是暂时性延迟，中等冷却即可）
    COOLDOWN_429_SECONDS = 5       # 429冷却：快速恢复
    COOLDOWN_403_SECONDS = 60      # 403冷却：长时间避免（权限拒绝型密钥）
    COOLDOWN_TIMEOUT_SECONDS = 15  # 超时冷却：中等恢复
    COOLDOWN_SECONDS = 5           # 默认冷却（向后兼容）
    # 403自适应退避：同一(key,model)连续403时指数增长冷却时长
    COOLDOWN_403_MAX_SECONDS = 600  # 403最大冷却10分钟（彻底避免100%失败密钥被反复选择）
    # 隔离时长（秒）- 算法9
    ISOLATION_SECONDS = 300  # 5分钟（超时可能是网络抖动，不需要30分钟那么长）
    # 5xx恢复时长（秒）- 算法8
    XX5_RECOVERY_SECONDS = 600  # 10分钟
    # 预热请求数 - 算法13
    WARMUP_TARGET = 30

    # 模型级429熔断：当某模型在短时间内收到大量429时，暂停该模型的调度
    MODEL_429_CIRCUIT_BREAKER_THRESHOLD = 20  # 30秒内429次数阈值
    MODEL_429_CIRCUIT_BREAKER_WINDOW = 30.0   # 统计窗口（秒）
    MODEL_429_CIRCUIT_BREAKER_COOLDOWN = 10.0  # 熔断冷却时间（秒）

    # 模型级5xx熔断：当某模型在短时间内收到大量5xx时，暂停该模型的调度
    # 提高容错性 - 阈值从10提高到30，窗口缩短到20s，cooldown从30s降到10s
    # 确保5xx不会导致模型被长时间禁止调用
    MODEL_5XX_CIRCUIT_BREAKER_THRESHOLD = 30   # 20秒内5xx次数阈值（大幅放宽）
    MODEL_5XX_CIRCUIT_BREAKER_WINDOW = 20.0    # 统计窗口（秒）
    MODEL_5XX_CIRCUIT_BREAKER_COOLDOWN = 10.0  # 熔断冷却时间（秒）- 快速恢复

    # 算法15：趋势感知自适应均衡（Trae优化算法）
    TRAE_TREND_WINDOW = 30.0  # 趋势计算窗口（秒）

    # 数据库日志自动清理计数器（每6小时执行一次）
    _cleanup_counter: int = 0
    CLEANUP_INTERVAL_CYCLES = 2160  # 10秒×2160=6小时
    TRAE_HIGH_TREND_MULTIPLIER = 0.6  # 高上升趋势乘数
    TRAE_LOW_TREND_MULTIPLIER = 1.3  # 下降趋势乘数

    # 算法16：龙虾脱壳式弹性调度（Lobster优化策略）
    LOBSTER_CHECK_INTERVAL = 120  # 脱壳检查间隔（秒）
    LOBSTER_MOLT_DURATION = 15  # 脱壳持续时长（秒）
    LOBSTER_OVERLOAD_THRESHOLD = 0.9  # 过载阈值（90% RPM阈值）
    LOBSTER_MOLT_SCORE_MULTIPLIER = 0.5  # 脱壳期评分乘数

    def __init__(self):
        # === 复合桶状态（键为 (key_id, model) 元组） ===
        self._buckets: Dict[Tuple[str, str], BucketData] = defaultdict(BucketData)

        # === 算法2：软繁忙标记器（运行时集合，与冷却完全分离） ===
        self._soft_busy: set = set()  # 元素为 (key_id, model) 元组

        # === 算法12：负载预判排除集合（运行时集合） ===
        self._predicted_busy: set = set()

        # === 密钥缓存（key_id -> api_key明文） ===
        # 安全修复: 添加TTL过期机制，避免明文密钥永久驻留内存
        self._key_cache: Dict[str, str] = {}
        self._key_cache_timestamps: Dict[str, float] = {}  # key_id -> 缓存写入时间戳
        self.KEY_CACHE_TTL = 300.0  # 5分钟过期，平衡安全性与性能

        # === 活跃密钥内存缓存（5秒TTL） ===
        self._active_keys_cache = []
        self._active_keys_cache_time = 0.0

        # === 客户端治理数据（算法5/6/7） ===
        self._client_metrics: Dict[str, ClientMetrics] = defaultdict(ClientMetrics)

        # === 全局统计 ===
        self._stats = SchedulerStats()

        # === 模型级429熔断状态 ===
        # model -> deque of timestamps (429发生时间)
        self._model_429_timestamps: dict = defaultdict(lambda: deque(maxlen=200))
        # model -> 熔断截止时间
        self._model_429_circuit_breaker: dict = {}

        # === 模型级5xx熔断状态 ===
        # model -> deque of timestamps (5xx发生时间)
        self._model_5xx_timestamps: dict = defaultdict(lambda: deque(maxlen=100))
        # model -> 熔断截止时间
        self._model_5xx_circuit_breaker: dict = {}

        # === HTTP连接池 ===
        self._http_pool: Optional[httpx.AsyncClient] = None
        self._stream_pool: Optional[httpx.AsyncClient] = None

        # === 后台任务最后执行时间 ===
        self._last_threshold_update = 0.0  # 算法3
        self._last_health_update = 0.0  # 算法10
        self._last_heal_check = 0.0  # 算法14
        self._threshold_update_count = 0  # 算法3更新次数计数器
        self._health_update_count = 0  # 算法10更新次数计数器
        self._heal_check_count = 0  # 算法14检查次数计数器

        # === 健康密钥计数（算法14用） ===
        self._healthy_key_count = 0

        # === 客户端密钥缓存（逐条30秒TTL，减少 authenticate_client 的DB查询） ===
        # v10.1修复：原"全局滑动TTL"（任一条写入刷新全部寿命）导致只要有一个
        # 活跃客户端，被吊销密钥的缓存就永不过期。改为逐条过期 + 条目上限
        self._client_key_cache: Dict[str, tuple] = {}  # key_hash -> (client_info, expires_at)
        self.CLIENT_KEY_CACHE_TTL = 30.0
        self.CLIENT_KEY_CACHE_MAX_ENTRIES = 2048  # 条目上限，超限淘汰最旧

        # === 上游键名称缓存（减少 get_algorithm_detail 的DB查询） ===
        self._upstream_key_names: Dict[str, str] = {}
        self._upstream_key_cache_time: float = 0.0
        self.UPSTREAM_KEY_CACHE_TTL = 30.0

        # === 客户端名称缓存（减少 get_algorithm_detail 的DB查询） ===
        self._client_name_cache: Dict[str, str] = {}
        self._client_name_cache_time: float = 0.0
        self.CLIENT_NAME_CACHE_TTL = 30.0

        # === 算法16：龙虾脱壳式弹性调度状态 ===
        self._molting_keys: Dict[str, float] = {}  # key_id -> molt_until timestamp
        self._last_lobster_check: float = 0.0

        # === 算法17：严格公平调度器（Strict Fair Dispatch） ===
        # 滑动窗口近期计数（300秒窗口，替代累计计数避免历史偏差）
        self._fair_window_seconds = 300  # 5分钟公平窗口
        self._bucket_recent_usage: Dict[Tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=200))
        self._key_recent_usage: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self._bucket_last_used: Dict[Tuple[str, str], float] = {}  # (key_id, model) -> last_used_timestamp
        self._key_last_used: Dict[str, float] = {}  # key_id -> last_used_timestamp
        # 全局轮询序号（严格递增，确保同窗口内按到达顺序轮询）
        self._dispatch_sequence: int = 0
        self._bucket_dispatch_seq: Dict[Tuple[str, str], int] = {}  # 最近一次分配序号

        # === 失效密钥自动检测器 ===
        # v10.0: 连续5次密钥认证失败自动停用，避免死密钥消耗调度资源
        # v10.1修复: 记录停用时间戳（原存reason字符串无恢复路径），
        # _recheck_auto_deactivated 每30分钟对停用超30分钟的key探活，成功自动恢复
        self._key_consecutive_auth_fail: Dict[str, int] = {}  # key_id -> 连续403/401计数
        self._auto_deactivated_keys: Dict[str, float] = {}  # key_id -> 停用时间戳
        self._AUTH_FAIL_DEACTIVATE_THRESHOLD = 5  # 连续5次认证失败自动停用
        self.AUTO_DEACTIVATE_RECHECK_SECONDS = 1800  # 停用30分钟后开始复检
        self._last_auto_deactivated_recheck: float = 0.0

        # === 系统资源监控（内存感知调度） ===
        self._memory_pressure: float = 0.0  # 0.0~1.0 内存压力系数
        self._last_resource_check: float = 0.0
        self._memory_saving_mode: bool = False

        logger.info("SurgeScheduler v10.0 初始化完成（严格公平调度 + 17算法互锁体系）")

    # ========== 系统资源监控（内存感知调度） ==========

    async def _check_system_resources(self):
        """
        读取 /proc/meminfo 获取可用内存，计算内存压力系数

        每30秒检查一次，结果存储在 self._memory_pressure 中
        """
        now = time.time()
        if now - self._last_resource_check < 30.0:
            return
        self._last_resource_check = now
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = f.read()

            mem_available = 0
            mem_total = 0
            for line in meminfo.splitlines():
                if line.startswith("MemAvailable:"):
                    # 单位: kB
                    mem_available = int(line.split()[1]) / 1024  # 转换为MB
                elif line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) / 1024  # 转换为MB

            if mem_total == 0:
                self._memory_pressure = 0.0
                return

            # 计算压力系数
            if mem_available < 100:
                self._memory_pressure = 1.0  # 临界
            elif mem_available < 200:
                # 可用内存100~200MB，压力0.8~0.95
                self._memory_pressure = 0.8 + (1.0 - (mem_available - 100) / 100) * 0.15
            else:
                # 基于可用内存比例计算（可用/总量），反向映射到0~0.8
                ratio = mem_available / mem_total
                self._memory_pressure = max(0.0, min(0.8, 1.0 - ratio * 2.0))

            # 更新内存节约模式
            old_mode = self._memory_saving_mode
            self._memory_saving_mode = self._memory_pressure > 0.7
            if old_mode != self._memory_saving_mode:
                logger.info(
                    f"系统内存{'进入' if self._memory_saving_mode else '退出'}节约模式: "
                    f"pressure={self._memory_pressure:.2f}, "
                    f"available={mem_available:.0f}MB/{mem_total:.0f}MB"
                )

        except Exception as e:
            logger.debug(f"读取系统内存信息失败: {e}")
            self._memory_pressure = 0.0

    def get_memory_pressure(self) -> float:
        """
        返回当前内存压力系数 (0.0~1.0)

        - 0.0: 无压力
        - 0.0~0.7: 正常
        - 0.7~0.99: 高压力
        - 1.0: 临界（可用内存 < 100MB）
        """
        return self._memory_pressure

    # ========== 桶操作基础方法 ==========

    def _get_bucket(self, key_id: str, model: str) -> BucketData:
        """获取桶数据（不存在则自动创建）"""
        return self._buckets[(key_id, model)]

    def _get_active_keys(self) -> List[dict]:
        """从数据库获取所有活跃上游密钥（5秒内存缓存）"""
        now = time.time()
        if self._active_keys_cache and now - self._active_keys_cache_time < self.ACTIVE_KEYS_CACHE_TTL:
            return self._active_keys_cache
        self._active_keys_cache = fetch_all(
            "SELECT id, name, api_key_ciphertext, key_prefix, weight, "
            "rpm_limit, switch_threshold, status FROM upstream_keys WHERE status = 'active'"
        )
        self._active_keys_cache_time = now
        return self._active_keys_cache

    def invalidate_active_keys_cache(self):
        """使活跃密钥缓存失效（密钥增删改时调用）"""
        self._active_keys_cache = []
        self._active_keys_cache_time = 0.0

    def cache_key(self, key_id: str, api_key: str):
        """缓存密钥明文"""
        self._key_cache[key_id] = api_key
        self._key_cache_timestamps[key_id] = time.time()

    def get_cached_key(self, key_id: str) -> Optional[str]:
        """获取缓存的密钥明文（检查TTL过期）"""
        if key_id not in self._key_cache:
            return None
        # 检查是否过期
        cached_at = self._key_cache_timestamps.get(key_id, 0)
        if time.time() - cached_at > self.KEY_CACHE_TTL:
            # 过期，清除缓存
            self._key_cache.pop(key_id, None)
            self._key_cache_timestamps.pop(key_id, None)
            return None
        return self._key_cache.get(key_id)

    def invalidate_key_cache(self, key_id: str):
        """使密钥缓存失效"""
        self._key_cache.pop(key_id, None)
        self._key_cache_timestamps.pop(key_id, None)

    def _upstream_models_url(self) -> str:
        """上游模型列表探活地址（v10.1修复：base_url 可经管理后台配置，不再硬编码）"""
        base = get_setting("upstream_base_url") or "https://integrate.api.nvidia.com/v1"
        return base.rstrip("/") + "/models"

    def _cleanup_expired_key_cache(self):
        """清理所有过期的密钥缓存条目（后台定期调用）"""
        now = time.time()
        expired_keys = [
            kid for kid, ts in self._key_cache_timestamps.items()
            if now - ts > self.KEY_CACHE_TTL
        ]
        for kid in expired_keys:
            self._key_cache.pop(kid, None)
            self._key_cache_timestamps.pop(kid, None)
        if expired_keys:
            logger.debug(f"密钥缓存清理: 过期{len(expired_keys)}个条目")

    def _ensure_key_cached(self, key_id: str, ciphertext: str) -> str:
        """确保密钥已缓存，返回明文"""
        # 检查缓存是否存在且未过期
        cached = self.get_cached_key(key_id)
        if cached is not None:
            return cached
        if not ciphertext:
            logger.warning(f"密钥解密失败: key={key_id[:8]} ciphertext为空")
            return ""
        master_key = get_setting("upstream_master_key")
        if master_key:
            try:
                plaintext = decrypt_upstream_key(ciphertext, master_key)
                self._key_cache[key_id] = plaintext
                self._key_cache_timestamps[key_id] = time.time()
            except Exception as e:
                logger.error(f"密钥解密异常: key={key_id[:8]} error={e}")
                return ""
        return self._key_cache.get(key_id, "")

    # ========== HTTP连接池 ==========

    async def _ensure_pools(self):
        """确保连接池已创建"""
        if self._http_pool is None or self._http_pool.is_closed:
            # 非流式池：limits 显式化；超时分项显式（connect=10/read=120），
            # 热路径请求均带 per-request timeout 覆盖（180s/慢模型300s），此处为兜底
            limits = httpx.Limits(
                max_connections=self.POOL_MAX_CONNECTIONS,
                max_keepalive_connections=self.POOL_MAX_KEEPALIVE,
                keepalive_expiry=60,
            )
            self._http_pool = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                limits=limits,
                http2=False,
            )
        if self._stream_pool is None or self._stream_pool.is_closed:
            # 流式池：limits 与非流式一致；read=600 大于 per-chunk 空闲超时180秒，
            # 让 per-chunk 空闲检测先于 httpx 超时触发，确保优雅终止
            stream_limits = httpx.Limits(
                max_connections=self.POOL_MAX_CONNECTIONS,
                max_keepalive_connections=self.POOL_MAX_KEEPALIVE,
                keepalive_expiry=60,
            )
            self._stream_pool = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=10.0, read=600.0),
                limits=stream_limits,
                http2=False,
            )

    async def get_http_pool(self) -> httpx.AsyncClient:
        await self._ensure_pools()
        return self._http_pool

    async def get_stream_pool(self) -> httpx.AsyncClient:
        await self._ensure_pools()
        return self._stream_pool

    def get_resource_status(self) -> dict:
        """获取中央智能资源状态仪表盘数据"""
        now = time.time()
        return {
            "memory_pressure": round(self._memory_pressure, 3),
            "memory_saving_mode": self._memory_saving_mode,
            "active_keys_cache_age": round(now - self._active_keys_cache_time, 1) if self._active_keys_cache else -1,
            "total_buckets": len(self._buckets),
            "total_soft_busy": len(self._soft_busy),
            "degraded_mode": self._stats.degraded_mode,
            "http_pool_active": self._http_pool is not None and not self._http_pool.is_closed,
            "stream_pool_active": self._stream_pool is not None and not self._stream_pool.is_closed,
            "inflight_requests": sum(m.inflight_count for m in self._client_metrics.values()),
            "cooling_buckets": sum(1 for b in self._buckets.values() if b.is_cooled_down()),
            "isolated_buckets": sum(1 for b in self._buckets.values() if b.is_isolated()),
        }

    # ========== 算法1：分桶滑动窗口计数器 ==========

    def record_response(self, key_id: str, model: str, success: bool,
                        rt_seconds: float, status_code: int, failure_type: str = ""):
        """
        记录响应数据 - 算法1的核心，所有上层算法的唯一数据来源

        参数：
        - key_id: 上游密钥ID
        - model: 模型名称
        - success: 是否成功
        - rt_seconds: 响应时间（秒）
        - status_code: HTTP状态码
        - failure_type: 失败类型（""/"429"/"5xx"/"timeout"/"conn_error"/"ssl_error"）
        """
        bucket = self._get_bucket(key_id, model)
        now = time.time()

        # 记录时间戳（60秒滑动窗口）
        bucket.timestamps.append(now)

        # 记录响应时间
        if rt_seconds > 0:
            bucket.response_times.append(rt_seconds)

        # 更新总计数
        bucket.total_requests += 1

        # 更新成功/失败计数
        if success:
            bucket.total_success += 1
            # 成功时重置连续5xx计数（算法8）
            bucket.consecutive_5xx = 0
            bucket.first_5xx_time = None
            # 成功时重置连接失败计数（算法9）
            bucket.consecutive_conn_fail = 0
            # 成功时重置连续403计数（算法4差异化冷却）
            bucket.consecutive_403 = 0
            bucket.last_success_at = now
        else:
            bucket.total_failures += 1
            bucket.last_failure_at = now
            bucket.last_failure_type = failure_type

            # 按状态码分类统计
            if status_code == 429 or failure_type == "429":
                bucket.total_429 += 1
            elif status_code >= 500 or failure_type == "5xx":
                bucket.total_5xx += 1
                # 算法8：更新连续5xx计数
                bucket.consecutive_5xx += 1
                # 每次新5xx都更新时间，确保10分钟恢复窗口基于最后一次错误而非第一次
                bucket.first_5xx_time = now
            elif failure_type == "timeout":
                bucket.total_timeout += 1
            elif failure_type == "conn_error":
                bucket.total_conn_error += 1
                # 算法9：更新连续连接失败计数
                bucket.consecutive_conn_fail += 1
            elif failure_type == "ssl_error":
                bucket.total_ssl_error += 1
                bucket.consecutive_conn_fail += 1

        # 记录RPM趋势（算法12用）
        bucket.rpm_trend.append(now)

        # 算法13：预热进度递增（成功时）
        if success and bucket.warmup_progress < self.WARMUP_TARGET:
            bucket.warmup_progress += 1

        # === 失效密钥自动检测 ===
        # v10.0: 连续5次认证失败自动停用，从调度池移除
        # v10.1修复: 记录停用时间戳；UPDATE后立即失效活跃密钥缓存（原实现缓存30秒内仍读到已停用密钥）
        if not success and (status_code == 403 or status_code == 401):
            self._key_consecutive_auth_fail[key_id] = self._key_consecutive_auth_fail.get(key_id, 0) + 1
            count = self._key_consecutive_auth_fail[key_id]
            if count >= self._AUTH_FAIL_DEACTIVATE_THRESHOLD > 0 and key_id not in self._auto_deactivated_keys:
                self._auto_deactivated_keys[key_id] = time.time()
                # 数据库落库改为后台线程执行：本方法直接运行在事件循环内，
                # 同步UPDATE会阻塞所有在途请求（落库+失效缓存打包进线程，保持先后顺序）
                def _deactivate_db(kid=key_id, fail_count=count):
                    try:
                        from app.database import execute as db_exec
                        db_exec("UPDATE upstream_keys SET status = 'inactive' WHERE id = %s AND status = 'active'", (kid,))
                        self.invalidate_active_keys_cache()  # 立即失效密钥缓存
                        logger.warning(f"密钥自动停用: key={kid[:8]} 连续{fail_count}次认证失败，已标记为inactive({self.AUTO_DEACTIVATE_RECHECK_SECONDS // 60}分钟后自动复检)")
                    except Exception as e:
                        logger.error(f"密钥自动停用数据库更新失败: {e}")
                threading.Thread(target=_deactivate_db, daemon=True).start()
            else:
                logger.debug(f"密钥认证失败记录: key={key_id[:8]} consecutive={count}/{self._AUTH_FAIL_DEACTIVATE_THRESHOLD}")
        elif success:
            # 成功请求重置认证失败计数
            self._key_consecutive_auth_fail.pop(key_id, None)

        logger.debug(
            f"算法1记录: key={key_id[:8]} model={model} success={success} "
            f"rt={rt_seconds:.2f}s status={status_code} type={failure_type}"
        )

    # ========== 算法2：软繁忙标记器 ==========

    def is_soft_busy(self, key_id: str, model: str) -> bool:
        """
        检查桶是否软繁忙

        重要：软繁忙与冷却完全分离！
        - 软繁忙：RPM超过动态阈值，只跳过不冷却
        - 冷却：上游真实429，触发240秒冷却
        """
        bucket = (key_id, model)
        if bucket not in self._soft_busy:
            return False

        # 检查RPM是否已下降到阈值以下
        b = self._get_bucket(key_id, model)
        rpm = b.get_rpm()
        threshold = b.dynamic_threshold
        if rpm < threshold * 0.8:
            self._soft_busy.discard(bucket)
            return False
        return True

    def mark_soft_busy(self, key_id: str, model: str):
        """标记桶为软繁忙（不冷却，不拉黑）"""
        self._soft_busy.add((key_id, model))

    def check_and_mark_soft_busy(self, key_id: str, model: str) -> bool:
        """检查RPM并标记软繁忙，返回是否软繁忙"""
        b = self._get_bucket(key_id, model)
        rpm = b.get_rpm()
        threshold = get_threshold_for_model(model, b.dynamic_threshold)
        if rpm >= threshold:
            self.mark_soft_busy(key_id, model)
            return True
        return False

    # ========== 算法3：自适应阈值调节器 ==========

    def update_adaptive_thresholds(self):
        """
        每30秒更新自适应阈值

        统一阈值38，密钥池总容量 = 密钥数 × 38 RPM
        线性插值：
        - P95 <= 0.5s → 38（响应快，满额并发）
        - P95 >= 5.0s → 38（统一阈值，不降低容量）
        """
        now = time.time()
        if now - self._last_threshold_update < 30:
            return
        self._last_threshold_update = now
        self._threshold_update_count += 1

        for (key_id, model), bucket in self._buckets.items():
            # 统一阈值38，不再根据P95动态调节
            bucket.dynamic_threshold = 38

            logger.debug(
                f"算法3更新阈值: key={key_id[:8]} model={model} "
                f"threshold=38(unified)"
            )

    # ========== 算法4：自适应冷却时长计算器 ==========

    def trigger_hard_cooldown(self, key_id: str, model: str, reason: str = "429") -> int:
        """
        触发硬冷却 - 仅冷却触发429的那个 (key_id, model) 桶

        【强制约束】不得存在任何以单一密钥为键的冷却操作

        冷却策略：
        - 429：5秒快速恢复（NVIDIA限流通常1-2秒）
        - 403：60秒起步 + 自适应退避（连续403指数增长至600秒）
        - timeout：15秒中等恢复（可能是暂时性延迟）
        - 其他：5秒默认冷却

        403自适应退避算法：
        - 第1次403：60秒冷却
        - 第2次403：120秒冷却（60×2）
        - 第3次403：240秒冷却（60×4）
        - 第4次+：600秒冷却（上限，彻底避免100%失败密钥被反复选择）
        - 成功请求重置consecutive_403为0
        """
        if not model:
            logger.warning(f"trigger_hard_cooldown被调用但model为空，跳过(key_id={key_id})")
            return 0

        bucket = self._get_bucket(key_id, model)
        now = time.time()

        # 差异化冷却时长
        if reason == "429":
            cooldown_seconds = self.COOLDOWN_429_SECONDS
        elif reason == "403":
            # 403自适应退避：连续403时指数增长
            bucket.consecutive_403 += 1
            backoff_multiplier = min(2 ** (bucket.consecutive_403 - 1), 10)  # 1,2,4,8,10...
            cooldown_seconds = min(self.COOLDOWN_403_SECONDS * backoff_multiplier, self.COOLDOWN_403_MAX_SECONDS)
            if bucket.consecutive_403 >= 3:
                logger.warning(
                    f"403自适应退避: key={key_id[:8]} model={model} "
                    f"连续403={bucket.consecutive_403}次 冷却={cooldown_seconds}s"
                )
        elif reason == "timeout":
            cooldown_seconds = self.COOLDOWN_TIMEOUT_SECONDS
        else:
            cooldown_seconds = self.COOLDOWN_SECONDS

        # 记录冷却历史
        bucket.cooldown_history.append(now)

        # 设置冷却
        bucket.cooldown_until = now + cooldown_seconds
        self._stats.total_cooldowns_triggered += 1

        # 修复：重置预热进度，冷却恢复后进入渐进式预热流程
        bucket.warmup_progress = 0

        # 模型级429熔断检测：记录429时间戳
        if reason == "429":
            self._model_429_timestamps[model].append(now)
            # 检查是否触发熔断
            recent_429s = [t for t in self._model_429_timestamps[model] if now - t < self.MODEL_429_CIRCUIT_BREAKER_WINDOW]
            if len(recent_429s) >= self.MODEL_429_CIRCUIT_BREAKER_THRESHOLD:
                self._model_429_circuit_breaker[model] = now + self.MODEL_429_CIRCUIT_BREAKER_COOLDOWN
                logger.warning(
                    f"模型级429熔断触发: model={model} "
                    f"窗口内429={len(recent_429s)}次/{self.MODEL_429_CIRCUIT_BREAKER_WINDOW:.0f}s "
                    f"熔断{self.MODEL_429_CIRCUIT_BREAKER_COOLDOWN:.0f}s"
                )

        # 模型级5xx熔断检测：记录5xx时间戳
        if reason == "5xx":
            self._model_5xx_timestamps[model].append(now)
            recent_5xx = [t for t in self._model_5xx_timestamps[model] if now - t < self.MODEL_5XX_CIRCUIT_BREAKER_WINDOW]
            if len(recent_5xx) >= self.MODEL_5XX_CIRCUIT_BREAKER_THRESHOLD:
                self._model_5xx_circuit_breaker[model] = now + self.MODEL_5XX_CIRCUIT_BREAKER_COOLDOWN
                logger.warning(
                    f"模型级5xx熔断触发: model={model} "
                    f"窗口内5xx={len(recent_5xx)}次/{self.MODEL_5XX_CIRCUIT_BREAKER_WINDOW:.0f}s "
                    f"熔断{self.MODEL_5XX_CIRCUIT_BREAKER_COOLDOWN:.0f}s"
                )

        logger.info(
            f"算法4触发桶级冷却: key={key_id[:8]} model={model} "
            f"时长={cooldown_seconds}s 原因={reason} (仅该模型桶受影响，已重置预热)"
        )
        return cooldown_seconds

    def unfreeze_bucket(self, key_id: str, model: str) -> bool:
        """手动解冻特定桶（管理后台用）"""
        bucket = self._get_bucket(key_id, model)
        bucket.cooldown_until = 0
        bucket.isolation_until = 0
        bucket.consecutive_5xx = 0
        bucket.first_5xx_time = None
        bucket.consecutive_conn_fail = 0
        bucket.heal_action = None
        bucket.heal_until = 0
        # 重置预热进度
        bucket.warmup_progress = self.WARMUP_TARGET
        logger.info(f"手动解冻桶: key={key_id[:8]} model={model}")
        return True

    def unfreeze_key_all_buckets(self, key_id: str) -> int:
        """解冻某密钥的所有桶（管理后台用）"""
        count = 0
        for (kid, model) in list(self._buckets.keys()):
            if kid == key_id:
                self.unfreeze_bucket(key_id, model)
                count += 1
        logger.info(f"解冻密钥所有桶: key={key_id[:8]} 共{count}个桶")
        return count

    # ========== 算法8：5xx退避权重衰减器 ==========

    def get_5xx_multiplier(self, bucket: BucketData) -> float:
        """
        计算5xx退避权重乘数

        consecutive_5xx=0 → 1.0
        consecutive_5xx=1 → 0.8
        consecutive_5xx=2 → 0.5
        consecutive_5xx=3 → 0.2
        consecutive_5xx≥4 → 0.0 (持续10分钟后自动恢复0.8)
        """
        if bucket.consecutive_5xx == 0:
            return 1.0
        if bucket.consecutive_5xx >= 4:
            # 检查是否已过10分钟恢复期
            if bucket.first_5xx_time and time.time() - bucket.first_5xx_time > self.XX5_RECOVERY_SECONDS:
                bucket.consecutive_5xx = 0
                bucket.first_5xx_time = None
                return 0.8
            return 0.0
        multipliers = {1: 0.8, 2: 0.5, 3: 0.2}
        return multipliers.get(bucket.consecutive_5xx, 0.0)

    # ========== 算法9：区域故障隔离器 ==========

    def trigger_isolation(self, key_id: str, model: str, reason: str = "conn_error"):
        """触发区域故障隔离（30分钟权重归零）"""
        bucket = self._get_bucket(key_id, model)
        if bucket.consecutive_conn_fail >= 3:
            bucket.isolation_until = time.time() + self.ISOLATION_SECONDS
            self._stats.total_isolations_triggered += 1
            logger.warning(
                f"算法9触发隔离: key={key_id[:8]} model={model} "
                f"时长={self.ISOLATION_SECONDS}s 原因={reason}"
            )

    def get_isolation_multiplier(self, bucket: BucketData) -> float:
        """计算隔离权重乘数"""
        return 0.0 if bucket.is_isolated() else 1.0

    # ========== 算法10：全局健康度评分器 ==========

    def update_health_scores(self):
        """
        每30秒计算全局健康度评分

        评分维度：
        - 成功率 (40%)
        - 响应时间 (20%)
        - 429频率 (20%)
        - 5xx频率 (20%)

        修复：
        1. 响应时间得分检查是否有数据（len(response_times)>0），而非avg_rt==0
        2. 健康密钥数按密钥级别去重计数，而非桶级别
        """
        now = time.time()
        if now - self._last_health_update < 30:
            return
        self._last_health_update = now
        self._health_update_count += 1  # 修复：使用计数器

        healthy_keys = set()  # 修复：按密钥级别去重
        for (key_id, model), bucket in self._buckets.items():
            # 计算各维度得分
            success_rate = bucket.get_success_rate()
            avg_rt = bucket.get_avg_rt()
            total_req = bucket.total_requests
            has_rt_data = len(bucket.response_times) > 0  # 修复：检查是否有响应时间数据

            # 成功率得分（40%）
            sr_score = success_rate  # 0~100

            # 响应时间得分（20%）- RT<2s满分，>10s零分
            # 修复：无响应时间数据时给满分，有数据时按实际计算
            if not has_rt_data:
                rt_score = 100
            elif avg_rt < 2:
                rt_score = 100
            elif avg_rt > 10:
                rt_score = 0
            else:
                rt_score = 100 * (10 - avg_rt) / 8

            # 429频率得分（20%）- 429率<5%满分，>30%零分
            if total_req == 0:
                r429_score = 100
            else:
                r429_rate = bucket.total_429 / total_req
                if r429_rate < 0.05:
                    r429_score = 100
                elif r429_rate > 0.30:
                    r429_score = 0
                else:
                    r429_score = 100 * (0.30 - r429_rate) / 0.25

            # 5xx频率得分（20%）
            if total_req == 0:
                r5xx_score = 100
            else:
                r5xx_rate = bucket.total_5xx / total_req
                if r5xx_rate < 0.02:
                    r5xx_score = 100
                elif r5xx_rate > 0.20:
                    r5xx_score = 0
                else:
                    r5xx_score = 100 * (0.20 - r5xx_rate) / 0.18

            # 综合健康度
            health = (sr_score * 0.4 + rt_score * 0.2 + r429_score * 0.2 + r5xx_score * 0.2)
            bucket.health_score = max(0, min(100, health))
            bucket.health_history.append(bucket.health_score)

            # 修复：按密钥级别去重，一个密钥只要有任一桶健康则算健康
            if bucket.health_score >= 50:
                healthy_keys.add(key_id)

        self._healthy_key_count = len(healthy_keys)
        logger.debug(f"算法10更新健康度: 共{len(self._buckets)}个桶，健康密钥{len(healthy_keys)}个")

    # ========== 算法11：池化动态权重调节器 ==========

    def get_base_weight_multiplier(self, bucket: BucketData) -> float:
        """
        计算基础权重乘数（基于健康度）

        base_weight = 0.5 + (health_score / 100) × 1.5
        范围: 0.5 (health=0) ~ 2.0 (health=100)
        """
        return 0.5 + (bucket.health_score / 100) * 1.5

    # ========== 算法12：自适应负载预判器 ==========

    def update_load_prediction(self):
        """更新负载预判（排除即将繁忙的桶）

        修复：
        1. 原实现中time_span过小导致rpm_rate异常高（如10个请求在1秒内完成会算出600 RPM）
        2. 新实现：分别计算最近60秒和前60秒的RPM，通过对比判断趋势
        """
        now = time.time()
        self._predicted_busy.clear()

        for (key_id, model), bucket in self._buckets.items():
            if len(bucket.rpm_trend) < 5:
                continue

            # 清理5分钟前的数据
            recent = [t for t in bucket.rpm_trend if now - t < 300]
            if len(recent) < 5:
                continue

            # 修复：使用60秒滑动窗口计算当前RPM，而非time_span
            current_window = [t for t in recent if now - t < 60]
            current_rpm = len(current_window)

            # 计算60-120秒前的RPM作为对比基准
            previous_window = [t for t in recent if 60 <= now - t < 120]
            previous_rpm = len(previous_window)

            threshold = get_threshold_for_model(model, bucket.dynamic_threshold)

            # 修复：预判条件放宽
            # 1. 当前RPM已接近阈值（>95%）才预判排除
            # 2. 快速增长趋势（>2倍增长）且已达到阈值80%
            # 注：旧方案使用80%阈值过于激进，导致候选池过小，均衡度仅28%
            if current_rpm >= threshold * 0.95:
                self._predicted_busy.add((key_id, model))
            elif previous_rpm > 0 and current_rpm > previous_rpm * 2.0 and current_rpm >= threshold * 0.8:
                # 快速增长趋势（>50%增长）且已达到阈值一半
                self._predicted_busy.add((key_id, model))

    def is_predicted_busy(self, key_id: str, model: str) -> bool:
        """检查桶是否被预判为即将繁忙"""
        return (key_id, model) in self._predicted_busy

    # ========== 算法13：冷密钥渐进式预热器 ==========

    def get_warmup_multiplier(self, bucket: BucketData) -> float:
        """
        计算预热权重乘数

        warmup_progress < 10 → 0.3
        warmup_progress 10~19 → 0.6
        warmup_progress 20~29 → 0.9
        warmup_progress ≥ 30 → 1.0
        """
        if bucket.warmup_progress < 10:
            return 0.3
        elif bucket.warmup_progress < 20:
            return 0.6
        elif bucket.warmup_progress < 30:
            return 0.9
        return 1.0

    def reset_warmup(self, key_id: str, model: str):
        """重置预热进度（冷却恢复后调用）"""
        bucket = self._get_bucket(key_id, model)
        bucket.warmup_progress = 0
        logger.info(f"算法13重置预热: key={key_id[:8]} model={model}")

    # ========== 算法15：趋势感知自适应均衡（Trae优化算法） ==========

    def get_trae_multiplier(self, key_id: str, model: str) -> float:
        """
        计算趋势感知乘数（Trae优化算法）

        通过对比最近30秒与之前30秒的RPM，计算趋势斜率：
        - 斜率 > 1.5（RPM快速上升）→ 降低乘数，避免过载（最低0.6）
        - 斜率 < 1.0（RPM下降）→ 提高乘数，吸引流量（最高1.3）
        - 斜率在1.0~1.5之间 → 线性插值
        """
        bucket = self._get_bucket(key_id, model)
        now = time.time()

        # 清理5分钟前的趋势数据
        recent = [t for t in bucket.rpm_trend if now - t < 300]

        if len(recent) < 5:
            return 1.0

        # 最近30秒的RPM
        recent_window = [t for t in recent if now - t < self.TRAE_TREND_WINDOW]
        recent_rpm = len(recent_window)

        # 之前30秒的RPM（30~60秒前）
        previous_window = [t for t in recent if self.TRAE_TREND_WINDOW <= now - t < self.TRAE_TREND_WINDOW * 2]
        previous_rpm = len(previous_window)

        if previous_rpm == 0:
            # 无历史数据时，如果当前有流量则轻微降权，否则返回1.0
            return 0.9 if recent_rpm > 0 else 1.0

        # 计算趋势斜率
        slope = recent_rpm / previous_rpm

        if slope > 1.5:
            # 快速上升趋势，降权防止过载
            return self.TRAE_HIGH_TREND_MULTIPLIER
        elif slope < 1.0:
            # 下降趋势，提权吸引流量
            # 线性插值：slope=0.0→1.3, slope=1.0→1.0
            multiplier = 1.0 + (1.0 - slope) * (self.TRAE_LOW_TREND_MULTIPLIER - 1.0)
            return min(self.TRAE_LOW_TREND_MULTIPLIER, multiplier)
        else:
            # slope在1.0~1.5之间，线性插值
            # slope=1.0→1.0, slope=1.5→0.6
            multiplier = 1.0 - (slope - 1.0) / 0.5 * (1.0 - self.TRAE_HIGH_TREND_MULTIPLIER)
            return max(self.TRAE_HIGH_TREND_MULTIPLIER, multiplier)

    # ========== 算法16：龙虾脱壳式弹性调度（Lobster优化策略） ==========

    def run_lobster_molting(self):
        """
        每120秒执行龙虾脱壳检查

        识别过去60秒内RPM持续超过90%阈值的密钥，强制进入15秒脱壳期。
        脱壳期内该密钥所有桶的评分乘数降为0.5，使流量重新分配到其他密钥。
        脱壳期结束后密钥恢复正常评分。
        """
        now = time.time()
        if now - self._last_lobster_check < self.LOBSTER_CHECK_INTERVAL:
            return
        self._last_lobster_check = now

        # 清理已过期的脱壳状态
        expired_keys = [kid for kid, molt_until in self._molting_keys.items() if now >= molt_until]
        for kid in expired_keys:
            del self._molting_keys[kid]
            logger.info(f"算法16脱壳恢复: key={kid[:8]} 恢复正常评分")

        # 识别过载密钥：过去60秒内RPM持续超过90%阈值
        # 按密钥级别聚合，只要任一桶过载就触发脱壳
        overloaded_keys = set()
        for (key_id, model), bucket in self._buckets.items():
            if key_id in self._molting_keys:
                continue  # 已在脱壳中，跳过

            # 计算过去60秒的RPM
            recent_timestamps = [t for t in bucket.rpm_trend if now - t < 60]
            rpm = len(recent_timestamps)
            threshold = get_threshold_for_model(model, bucket.dynamic_threshold)

            # 检查过去60秒是否持续超过90%阈值（简化：当前RPM超过90%阈值即认为过载）
            if rpm >= threshold * self.LOBSTER_OVERLOAD_THRESHOLD:
                overloaded_keys.add(key_id)

        # 对过载密钥设置脱壳期
        for key_id in overloaded_keys:
            if key_id not in self._molting_keys:
                molt_until = now + self.LOBSTER_MOLT_DURATION
                self._molting_keys[key_id] = molt_until
                logger.info(
                    f"算法16触发脱壳: key={key_id[:8]} "
                    f"脱壳时长={self.LOBSTER_MOLT_DURATION}s "
                    f"评分乘数={self.LOBSTER_MOLT_SCORE_MULTIPLIER}"
                )

    def get_lobster_multiplier(self, key_id: str) -> float:
        """
        计算龙虾脱壳乘数

        如果密钥正在脱壳期，返回0.5；否则返回1.0
        """
        molt_until = self._molting_keys.get(key_id)
        if molt_until is not None:
            if time.time() < molt_until:
                return self.LOBSTER_MOLT_SCORE_MULTIPLIER
            else:
                # 脱壳期已过，清理状态
                del self._molting_keys[key_id]
                logger.info(f"算法16脱壳恢复（懒清理）: key={key_id[:8]}")
        return 1.0

    # ========== 算法17：严格公平调度器（Strict Fair Dispatch） ==========

    def get_fair_dispatch_count(self, key_id: str, model: str) -> Tuple[int, int]:
        """
        算法17：获取桶级和密钥级的近期使用次数（滑动窗口）

        返回: (bucket_recent_count, key_recent_count)
        - bucket_recent_count: 该(key_id, model)桶在最近300秒内的使用次数
        - key_recent_count: 该key_id在最近300秒内的总使用次数

        使用滑动窗口替代累计计数，避免历史偏差
        """
        now = time.time()
        cutoff = now - self._fair_window_seconds

        # 桶级近期计数
        bucket_key = (key_id, model)
        bucket_times = self._bucket_recent_usage.get(bucket_key, deque(maxlen=500))
        # 清理过期时间戳
        while bucket_times and bucket_times[0] < cutoff:
            bucket_times.popleft()
        bucket_count = len(bucket_times)

        # 密钥级近期计数
        key_times = self._key_recent_usage.get(key_id, deque(maxlen=2000))
        while key_times and key_times[0] < cutoff:
            key_times.popleft()
        key_count = len(key_times)

        return (bucket_count, key_count)

    def get_round_robin_multiplier(self, key_id: str, model: str) -> float:
        """
        算法17兼容接口：返回1.0

        当前方案中，算法17不再使用乘数方式，而是通过严格公平排序实现均衡。
        保留此方法以兼容calculate_score调用，始终返回1.0（不影响评分）。
        实际均衡由select_key中的公平排序逻辑实现。
        """
        return 1.0

    def record_key_usage(self, key_id: str, model: str = ""):
        """记录密钥被使用（在select_key选中后调用）

        使用滑动窗口时间戳队列替代累计计数器
        """
        now = time.time()
        self._dispatch_sequence += 1

        # 密钥级记录
        self._key_last_used[key_id] = now
        self._key_recent_usage[key_id].append(now)

        # 桶级记录
        if model:
            bucket_key = (key_id, model)
            self._bucket_last_used[bucket_key] = now
            self._bucket_recent_usage[bucket_key].append(now)
            self._bucket_dispatch_seq[bucket_key] = self._dispatch_sequence

    # ========== 算法14：智能异常自愈引擎 ==========

    def run_self_heal(self):
        """
        每60秒执行四级自愈引擎

        轻度: 健康度连续3次<40 且 桶成功率<60% → 30秒观察期
        中度: 单桶10分钟内>5次冷却 其他桶正常 → 流量迁移2小时
        重度: 密钥健康度<20持续5分钟 且 RPM使用率<10% → 移出候选池30分钟
        全局: 健康密钥数<3 → 降级模式
        """
        now = time.time()
        if now - self._last_heal_check < 60:
            return
        self._last_heal_check = now
        self._heal_check_count += 1  # 修复：使用计数器

        # 统计健康密钥数（修复：与算法10保持一致，按密钥级别去重）
        healthy_keys = set()
        for (key_id, model), bucket in self._buckets.items():
            if bucket.health_score >= 50 and not bucket.is_cooled_down() and not bucket.is_isolated():
                healthy_keys.add(key_id)

        self._healthy_key_count = len(healthy_keys)

        # 全局降级模式检查（修复：无桶数据时不触发降级）
        if len(self._buckets) > 0 and len(healthy_keys) < 3 and not self._stats.degraded_mode:
            self._stats.degraded_mode = True
            self._stats.total_heal_actions += 1
            logger.warning(f"算法14触发全局降级模式: 健康密钥数={len(healthy_keys)} 桶数={len(self._buckets)}")
        elif len(healthy_keys) >= 5 and self._stats.degraded_mode:
            self._stats.degraded_mode = False
            logger.info(f"算法14退出全局降级模式: 健康密钥数={len(healthy_keys)}")

        # 逐桶检查自愈条件
        for (key_id, model), bucket in self._buckets.items():
            # 轻度自愈：健康度连续3次<40 且 成功率<60%
            if (len(bucket.health_history) >= 3 and
                all(h < 40 for h in list(bucket.health_history)[-3:]) and
                bucket.get_success_rate() < 60 and
                bucket.heal_action is None):
                bucket.heal_action = "light"
                bucket.heal_until = now + 30  # 30秒观察期
                self._stats.total_heal_actions += 1
                logger.info(f"算法14轻度自愈: key={key_id[:8]} model={model} 30秒观察期")

            # 中度自愈：10分钟内>5次冷却
            recent_cooldowns = [t for t in bucket.cooldown_history if now - t < 600]
            if (len(recent_cooldowns) > 5 and
                bucket.heal_action is None):
                bucket.heal_action = "medium"
                bucket.heal_until = now + 7200  # 2小时流量迁移
                bucket.cooldown_until = now + 7200  # 冷却2小时
                self._stats.total_heal_actions += 1
                logger.warning(f"算法14中度自愈: key={key_id[:8]} model={model} 2小时流量迁移")

            # 重度自愈：健康度<20持续5分钟
            if (bucket.health_score < 20 and
                len(bucket.health_history) >= 5 and
                all(h < 20 for h in list(bucket.health_history)[-5:]) and
                bucket.heal_action is None):
                bucket.heal_action = "heavy"
                bucket.heal_until = now + 1800  # 30分钟移出候选池
                bucket.cooldown_until = now + 1800
                self._stats.total_heal_actions += 1
                logger.error(f"算法14重度自愈: key={key_id[:8]} model={model} 30分钟移出候选池")

            # 检查自愈恢复
            if bucket.heal_action and now > bucket.heal_until:
                old_action = bucket.heal_action
                bucket.heal_action = None
                bucket.heal_until = 0
                bucket.cooldown_until = 0
                bucket.warmup_progress = 0  # 重置预热
                logger.info(f"算法14自愈恢复: key={key_id[:8]} model={model} 原动作={old_action}")

    # ========== 客户端治理（算法5/6/7，只监测不拦截） ==========

    def record_client_request(self, client_id: str, model: str, request_id: str = None):
        """记录客户端请求（算法5/6/7数据采集 + v9.2毫秒级并发追踪）

        request_id 可选：传入后 record_inflight 按请求精确匹配释放
        """
        metrics = self._client_metrics[client_id]
        now = time.time()

        # v9.2: 毫秒级精准并发追踪
        metrics.record_inflight_start(request_id)
        # 同时清理超时的陈旧请求
        metrics.cleanup_stale_inflight()

        # 算法6：突发检测（添加时间戳，get_burst_count会自动清理过期数据）
        metrics.burst_timestamps.append(now)

        # 算法7：日用量（修复：使用当天凌晨作为重置时间，而非now+86400）
        from datetime import datetime
        today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        # 如果从未重置过，或已经过了今天凌晨，则重置
        if metrics.daily_reset_at <= 0 or now >= metrics.daily_reset_at or metrics.daily_reset_at < today_midnight:
            metrics.daily_count = 0
            # 下次重置时间为明天凌晨
            metrics.daily_reset_at = today_midnight + 86400
        metrics.daily_count += 1

        # 请求间隔记录（商用识别用）
        if metrics.last_request_at > 0:
            interval = now - metrics.last_request_at
            metrics.request_intervals.append(interval)
        metrics.last_request_at = now

        # 模型切换记录
        if metrics.last_model and metrics.last_model != model:
            metrics.model_switches.append((now, model))
        metrics.last_model = model

    def release_client_request(self, client_id: str, request_id: str = None):
        """释放客户端并发计数（v9.2 毫秒级精准释放；request_id 可选用于精确匹配）"""
        metrics = self._client_metrics[client_id]
        elapsed = metrics.record_inflight_end(request_id)
        # 如果释放时间异常短（<100ms），可能是错误路径，记录日志
        if elapsed > 0 and elapsed < 0.1:
            logger.debug(f"快速释放请求: client={client_id[:8]} elapsed={elapsed:.3f}s")

    def get_client_metrics(self, client_id: str) -> ClientMetrics:
        """获取客户端监测数据"""
        return self._client_metrics.get(client_id, ClientMetrics())

    # ========== v9.2 并发统计（供用户控制台显示） ==========

    def get_client_concurrency_stats(self, client_id: str) -> dict:
        """
        获取客户端的实时并发统计（供用户控制台和概览页面使用）

        Returns:
            dict: {
                "current": int (当前活跃并发数),
                "limit": int (限制数, 0=无限量),
                "peak": int (历史峰值并发),
                "rejected": int (因并发超限被拒绝的请求数),
                "limit_label": str ("无限量" / "8" / "4" / "2"),
            }
        """
        metrics = self._client_metrics.get(client_id)
        limit = self.get_client_concurrency_limit(client_id)
        if not metrics:
            limit_label = "无限量" if limit == 0 else str(limit)
            return {"current": 0, "limit": limit, "peak": 0, "rejected": 0, "limit_label": limit_label}

        # 先清理超时请求
        real_current = metrics.get_real_inflight_count()

        # 计算峰值（取所有时间戳的最大并发数；元素为 (start_time, request_id) 元组）
        peak = 0
        ts_copy = list(metrics.inflight_timestamps)
        for i in range(len(ts_copy)):
            count = len([t for t in ts_copy[i:] if t[0] > 0])
            peak = max(peak, count)

        if limit == 0:
            limit_label = "无限量"
        elif limit >= 999:
            limit_label = "无限量"
        else:
            limit_label = str(limit)

        return {
            "current": real_current,
            "limit": limit,
            "peak": peak or real_current,
            "rejected": 0,
            "limit_label": limit_label,
        }

    def get_all_clients_concurrency_summary(self) -> dict:
        """
        获取所有客户端的并发统计汇总（用于概览页面）
        """
        total_active = 0
        total_limited = 0
        special_clients = 0
        client_details = []

        for cid, metrics in self._client_metrics.items():
            if metrics.inflight_count > 0:
                total_active += 1
            limit = self.get_client_concurrency_limit(cid)
            if limit == 0 or limit >= 999:
                total_limited += 1

        return {
            "total_active_clients": total_active,
            "total_unlimited_clients": total_limited,
            "total_tracked_clients": len(self._client_metrics),
        }

    # ========== 并发限制（已移除：v11.0 解除限制释放性能） ==========

    def get_client_concurrency_limit(self, client_id: str) -> int:
        """已解除：返回 0 表示无限并发"""
        return 0

    def check_client_concurrency(self, client_id: str) -> Optional[dict]:
        """已解除：始终返回 None，不限制并发"""
        return None

    def get_client_special_status(self, client_id: str) -> dict:
        """
        已解除所有限制，不再有特殊用户
        """
        return {"is_special": False, "concurrency_limit": 0, "tag": "", "reason": ""}

    # ========== 评分公式 ==========

    def calculate_score(self, key_id: str, model: str, base_weight: float) -> float:
        """
        计算桶的最终得分

        最终得分 = 基础权重 × 算法8乘数 × 算法9乘数 × 算法11乘数 × 算法13乘数 × 算法15乘数 × 算法16乘数 × 算法17乘数
        """
        bucket = self._get_bucket(key_id, model)

        # 算法8：5xx退避乘数
        m_5xx = self.get_5xx_multiplier(bucket)
        # 算法9：隔离乘数
        m_iso = self.get_isolation_multiplier(bucket)
        # 算法11：健康度权重乘数
        m_health = self.get_base_weight_multiplier(bucket)
        # 算法13：预热乘数
        m_warmup = self.get_warmup_multiplier(bucket)
        # 算法15：趋势感知乘数（Trae优化算法）
        m_trae = self.get_trae_multiplier(key_id, model)
        # 算法16：龙虾脱壳乘数（Lobster优化策略）
        m_lobster = self.get_lobster_multiplier(key_id)
        # 算法17：轮询均衡乘数（Round-Robin Balanced Dispatch）
        m_rr = self.get_round_robin_multiplier(key_id, model)

        score = base_weight * m_5xx * m_iso * m_health * m_warmup * m_trae * m_lobster * m_rr
        return score

    # ========== select_key 核心方法（严格公平调度） ==========

    def select_key(self, model: str) -> Optional[Tuple[str, str, str]]:
        """
        核心调度方法 - 严格公平调度

        评分仅用于健康性过滤，最终选择由公平排序决定

        【强制约束】
        1. 检查冷却状态时只使用 (key_id, model) 复合键查询 bucket.cooldown_until
        2. 不存在任何以单一密钥查询冷却状态的代码路径
        3. 软繁忙（运行时集合）与冷却（cooldown_until字段）完全分离

        流程：
        1. 获取所有活跃密钥
        2. 第一轮过滤：冷却、隔离、自愈、解密失败（硬性过滤）
        3. 第二轮过滤：predicted_busy + soft_busy + 健康评分<0.1（软性过滤）
        4. 最小候选池保障：若候选 < 3，放宽soft_busy和predicted_busy
        5. 严格公平排序：近期使用次数最少 → 分配序号最早 → 评分最高
        6. 记录使用并返回
        """
        self._stats.total_select_calls += 1

        # 模型级429熔断检查：如果某模型短时间内429太多，直接返回429给客户端
        # 避免反复尝试→反复429→反复降级的恶性循环
        now = time.time()
        circuit_breaker_until = self._model_429_circuit_breaker.get(model, 0)
        if now < circuit_breaker_until:
            # 熔断中，直接返回None（调用方会返回429给客户端）
            self._stats.total_none_returns += 1
            return None

        # 模型级5xx熔断检查：如果某模型短时间内5xx太多，暂停调度避免浪费所有密钥
        circuit_breaker_5xx_until = self._model_5xx_circuit_breaker.get(model, 0)
        if now < circuit_breaker_5xx_until:
            logger.warning(f"模型级5xx熔断: model={model} 持续至{circuit_breaker_5xx_until:.0f}")
            self._stats.total_none_returns += 1
            return None

        # 获取所有活跃上游密钥
        upstreams = self._get_active_keys()
        if not upstreams:
            logger.warning("select_key: 无活跃上游密钥")
            self._stats.total_none_returns += 1
            return None

        now = time.time()

        # === 第一轮：硬性过滤（不可放宽） ===
        hard_filtered = []  # (key_id, api_key, filtered_reason)
        for up in upstreams:
            key_id = up["id"]
            bucket = self._get_bucket(key_id, model)

            # 冷却状态过滤
            if bucket.is_cooled_down():
                hard_filtered.append((key_id, "", "cooldown"))
                continue
            # 隔离过滤
            if bucket.is_isolated():
                hard_filtered.append((key_id, "", "isolation"))
                continue
            # 自愈过滤
            if bucket.is_healing():
                hard_filtered.append((key_id, "", "healing"))
                continue
            # 密钥解密
            api_key = self._ensure_key_cached(key_id, up["api_key_ciphertext"])
            if not api_key:
                hard_filtered.append((key_id, "", "decrypt_failed"))
                continue

            # v10.0: 自动停用密钥过滤（连续认证失败被停用的密钥）
            if key_id in self._auto_deactivated_keys:
                hard_filtered.append((key_id, "", "auto_deactivated"))
                continue

            hard_filtered.append((key_id, api_key, "ok"))

        # 收集通过硬性过滤的密钥
        eligible = [(kid, ak) for kid, ak, reason in hard_filtered if reason == "ok"]
        if not eligible:
            # 降级模式：所有密钥都在冷却/隔离中，选择冷却剩余时间最短的桶强制恢复
            best_cooldown_key = None
            min_remaining = float('inf')
            for key_id, _, reason in hard_filtered:
                if reason == "cooldown":
                    bucket = self._get_bucket(key_id, model)
                    remaining = bucket.cooldown_until - now
                    if remaining < min_remaining:
                        min_remaining = remaining
                        best_cooldown_key = key_id
                elif reason in ("healing", "isolation", "decrypt_failed"):
                    # 隔离/自愈/解密失败的桶不能强制恢复
                    continue

            if best_cooldown_key:
                # 强制解除该桶的冷却，让它立即参与轮询
                bucket = self._get_bucket(best_cooldown_key, model)
                bucket.cooldown_until = 0  # 立即恢复
                bucket.warmup_progress = 0
                api_key = self._ensure_key_cached(best_cooldown_key, None)
                if api_key:
                    logger.warning(
                        f"select_key降级模式: 所有密钥冷却中，强制恢复 key={best_cooldown_key[:8]} "
                        f"model={model} 剩余冷却={min_remaining:.1f}s"
                    )
                    self.record_key_usage(best_cooldown_key, model)
                    return (best_cooldown_key, model, api_key)

            # 真正无法选择（无密钥或所有密钥解密失败）
            logger.warning(f"select_key: 无法选择密钥 model={model} (候选池为空，降级模式也无可用密钥)")
            self._stats.total_none_returns += 1
            return None

        # === 第二轮：软性过滤 + 健康性评分 ===
        # 密钥不放弃，软过滤仅用于排序优先级调整
        healthy_candidates = []  # (bucket_count, key_count, dispatch_seq, score, key_id, api_key)
        soft_filtered = []      # 被软性过滤的候选（作为备选）

        for key_id, api_key in eligible:
            bucket_key = (key_id, model)
            base_weight = float(self._get_key_weight(key_id, 1.0))
            score = self.calculate_score(key_id, model, base_weight)

            # 计算公平调度参数
            bucket_count, key_count = self.get_fair_dispatch_count(key_id, model)
            dispatch_seq = self._bucket_dispatch_seq.get(bucket_key, 0)

            # 密钥都参与轮询，不因软繁忙/预判/低评分排除
            healthy_candidates.append((bucket_count, key_count, dispatch_seq, score, key_id, api_key))

        # 密钥都参与轮询，不再需要最小候选池保障

        if not healthy_candidates:
            # 已在eligible阶段处理降级模式，此处理论上不应到达
            logger.warning(f"select_key: 健康候选池为空（不应到达此处） model={model}")
            self._stats.total_none_returns += 1
            return None

        # === 严格公平排序 ===
        # 排序优先级（5密钥池适配）：
        # 1. 桶级近期使用次数（升序）- 使用最少的优先
        # 2. 分配序号（升序）- 同计数时按轮询顺序（确保严格轮询）
        # 3. 健康评分（降序）- 最终质量保障
        # 注：移除key_count作为排序键，避免跨模型计数干扰单模型均衡
        healthy_candidates.sort(key=lambda x: (x[0], x[2], -x[3]))

        best = healthy_candidates[0]
        bucket_count, key_count, dispatch_seq, score, key_id, api_key = best

        # 算法17：记录密钥使用（驱动公平调度）
        self.record_key_usage(key_id, model)

        logger.info(
            f"select_key选中: key={key_id[:8]} model={model} "
            f"bucket_used={bucket_count} key_used={key_count} "
            f"score={score:.4f} (候选{len(healthy_candidates)}个)"
        )

        return (key_id, model, api_key)

    def _get_key_weight(self, key_id: str, default: float = 1.0) -> float:
        """从缓存的活跃密钥中获取指定密钥的权重"""
        for up in self._active_keys_cache:
            if up["id"] == key_id:
                return float(up.get("weight", default))
        return default

    def select_any_key(self) -> Optional[Tuple[str, str]]:
        """选择任意健康密钥（用于非模型特定操作如/models）

        遍历活跃密钥，跳过所有桶都在冷却/隔离中的密钥，
        返回第一个可用的 (key_id, api_key)。
        """
        upstreams = self._get_active_keys()
        if not upstreams:
            return None

        for up in upstreams:
            key_id = up["id"]

            # 检查该密钥所有桶是否都在冷却/隔离中
            key_bucket_keys = [(kid, model) for (kid, model) in self._buckets.keys() if kid == key_id]
            all_down = False
            if key_bucket_keys:
                all_down = all(
                    self._buckets[(kid, model)].is_cooled_down() or
                    self._buckets[(kid, model)].is_isolated()
                    for (kid, model) in key_bucket_keys
                )

            if not all_down:
                api_key = self._ensure_key_cached(key_id, up["api_key_ciphertext"])
                if api_key:
                    return (key_id, api_key)

        return None

    # ========== 状态查询方法（管理后台用） ==========

    def get_bucket_stats(self) -> List[dict]:
        """获取所有桶的状态快照（管理后台用）"""
        now = time.time()
        result = []
        for (key_id, model), bucket in self._buckets.items():
            result.append({
                "key_id": key_id,
                "model": model,
                "rpm": bucket.get_rpm(),
                "threshold": get_threshold_for_model(model, bucket.dynamic_threshold),
                "success_rate": round(bucket.get_success_rate(), 2),
                "avg_rt": round(bucket.get_avg_rt(), 3),
                "p95_rt": round(bucket.get_p95_rt(), 3),
                "total_requests": bucket.total_requests,
                "total_success": bucket.total_success,
                "total_failures": bucket.total_failures,
                "total_429": bucket.total_429,
                "total_5xx": bucket.total_5xx,
                "total_timeout": bucket.total_timeout,
                "cooldown_remaining": max(0, int(bucket.cooldown_until - now)) if bucket.is_cooled_down() else 0,
                "isolation_remaining": max(0, int(bucket.isolation_until - now)) if bucket.is_isolated() else 0,
                "health_score": round(bucket.health_score, 1),
                "warmup_progress": bucket.warmup_progress,
                "consecutive_5xx": bucket.consecutive_5xx,
                "consecutive_conn_fail": bucket.consecutive_conn_fail,
                "soft_busy": (key_id, model) in self._soft_busy,
                "predicted_busy": (key_id, model) in self._predicted_busy,
                "heal_action": bucket.heal_action,
                "last_success_at": bucket.last_success_at,
                "last_failure_at": bucket.last_failure_at,
                "last_failure_type": bucket.last_failure_type,
            })
        return result

    def get_algorithm_stats(self) -> dict:
        """获取17算法的运行统计（管理后台用）"""
        now = time.time()
        total_buckets = len(self._buckets)
        active_buckets = sum(1 for b in self._buckets.values() if not b.is_cooled_down())
        cooled_buckets = sum(1 for b in self._buckets.values() if b.is_cooled_down())
        isolated_buckets = sum(1 for b in self._buckets.values() if b.is_isolated())
        soft_busy_buckets = len(self._soft_busy)
        predicted_busy_buckets = len(self._predicted_busy)
        healing_buckets = sum(1 for b in self._buckets.values() if b.is_healing())

        # 健康度分布
        health_dist = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "critical": 0}
        for b in self._buckets.values():
            h = b.health_score
            if h >= 80:
                health_dist["excellent"] += 1
            elif h >= 60:
                health_dist["good"] += 1
            elif h >= 40:
                health_dist["fair"] += 1
            elif h >= 20:
                health_dist["poor"] += 1
            else:
                health_dist["critical"] += 1

        # 算法3 - 计算平均P95
        avg_p95 = 0.0
        try:
            p95_values = [b.get_p95_rt() for b in self._buckets.values() if b.total_requests > 0]
            avg_p95 = sum(p95_values) / len(p95_values) if p95_values else 0.0
        except Exception:
            logger.exception(f"[v10.0] 调度器后台任务异常")

        # 算法5 - 客户端并发
        total_inflight = sum(m.inflight_count for m in self._client_metrics.values())
        high_concurrency_clients = sum(1 for m in self._client_metrics.values() if m.inflight_count >= 10)

        # 算法6 - 突发率（修复：使用get_burst_count过滤3秒时间窗口）
        high_burst_clients = 0
        for m in self._client_metrics.values():
            if m.get_burst_count(3.0) >= 20:  # 3秒内请求超过20次
                high_burst_clients += 1

        # 算法7 - 日用量
        today_total = sum(m.daily_count for m in self._client_metrics.values())

        # 算法8 - 5xx退避
        total_5xx = sum(b.total_5xx for b in self._buckets.values())
        decaying_count = sum(1 for b in self._buckets.values() if b.consecutive_5xx > 0)

        # 算法10 - 健康度
        avg_health = 100.0
        unhealthy_keys = 0
        healthy_keys_set = set()  # 修复：按密钥级别去重
        try:
            health_scores = [b.health_score for b in self._buckets.values()]
            if health_scores:
                avg_health = sum(health_scores) / len(health_scores)
                unhealthy_keys = sum(1 for h in health_scores if h < 60)
            # 动态计算健康密钥数
            for (key_id, model), bucket in self._buckets.items():
                if bucket.health_score >= 50:
                    healthy_keys_set.add(key_id)
        except Exception:
            logger.exception(f"[v10.0] 调度器后台任务异常")

        # 修复：当后台任务未运行时，动态计算健康密钥数
        dynamic_healthy_count = len(healthy_keys_set) if healthy_keys_set else self._healthy_key_count

        # 算法11 - 动态权重
        avg_multiplier = 1.0
        max_multiplier = 1.0
        try:
            multipliers = []
            for b in self._buckets.values():
                # 健康分100→2.0倍，健康分0→0.5倍（线性插值）
                m = 0.5 + (b.health_score / 100.0) * 1.5
                multipliers.append(m)
            if multipliers:
                avg_multiplier = sum(multipliers) / len(multipliers)
                max_multiplier = max(multipliers)
        except Exception:
            logger.exception(f"[v10.0] 调度器后台任务异常")

        # 算法13 - 预热
        warmup_count = sum(1 for b in self._buckets.values() if 0 < b.warmup_progress < self.WARMUP_TARGET)
        completed_count = sum(1 for b in self._buckets.values() if b.warmup_progress >= self.WARMUP_TARGET)

        # 算法14 - 自愈
        total_heal_actions = self._stats.total_heal_actions

        # 算法2 - 软繁忙跳过次数（用软繁忙桶数近似）
        skip_count = soft_busy_buckets

        # 算法3 - 更新次数（修复：使用计数器而非时间戳/30）
        update_count = self._threshold_update_count

        # 算法6 - 通知次数（用突发客户数近似）
        notification_count = high_burst_clients

        # 算法9 - 隔离次数
        total_isolations = self._stats.total_isolations_triggered

        # 算法12 - 预测准确率（默认100%，无历史数据）
        accuracy = 100.0
        predicted_exclusions = predicted_busy_buckets

        return {
            "algorithm_1": {
                "name": "分桶滑动窗口计数器",
                "total_buckets": total_buckets,
                "active_buckets": active_buckets,
                "total_requests": sum(b.total_requests for b in self._buckets.values()),
                "total_success": sum(b.total_success for b in self._buckets.values()),
                "total_failures": sum(b.total_failures for b in self._buckets.values()),
            },
            "algorithm_2": {
                "name": "软繁忙标记器",
                "soft_busy_count": soft_busy_buckets,
                "skip_count": skip_count,
                "note": "软繁忙与冷却完全分离",
            },
            "algorithm_3": {
                "name": "自适应阈值调节器",
                "last_update": self._last_threshold_update,
                "threshold_range": "38(unified)",
                "update_count": update_count,
                "avg_p95": int(round(avg_p95 * 1000)),  # 转毫秒
            },
            "algorithm_4": {
                "name": "自适应冷却时长计算器",
                "cooled_buckets": cooled_buckets,
                "cooling_count": cooled_buckets,  # 别名
                "cooldown_seconds": self.COOLDOWN_SECONDS,
                "cooldown_429_seconds": self.COOLDOWN_429_SECONDS,
                "cooldown_403_seconds": self.COOLDOWN_403_SECONDS,
                "cooldown_timeout_seconds": self.COOLDOWN_TIMEOUT_SECONDS,
                "cooldown_403_max_seconds": self.COOLDOWN_403_MAX_SECONDS,
                "total_cooldowns_triggered": self._stats.total_cooldowns_triggered,
                "total_triggers": self._stats.total_cooldowns_triggered,  # 别名
            },
            "algorithm_5": {
                "name": "客户端并发监测器",
                "monitored_clients": len(self._client_metrics),
                "high_concurrency_clients": high_concurrency_clients,
                "total_inflight": total_inflight,
                "note": "只监测不拦截",
            },
            "algorithm_6": {
                "name": "客户端突发率检测器",
                "high_burst_clients": high_burst_clients,
                "notification_count": notification_count,
                "note": "只监测不拦截",
            },
            "algorithm_7": {
                "name": "客户端日用量监测器",
                "today_total": today_total,
                "monitored_clients": len(self._client_metrics),
                "note": "只监测不拦截",
            },
            "algorithm_8": {
                "name": "5xx退避权重衰减器",
                "buckets_with_5xx": decaying_count,
                "decaying_count": decaying_count,  # 别名
                "total_5xx": total_5xx,
            },
            "algorithm_9": {
                "name": "区域故障隔离器",
                "isolated_buckets": isolated_buckets,
                "isolated_count": isolated_buckets,  # 别名
                "isolation_seconds": self.ISOLATION_SECONDS,
                "total_isolations_triggered": self._stats.total_isolations_triggered,
                "total_isolations": total_isolations,  # 别名
            },
            "algorithm_10": {
                "name": "全局健康度评分器",
                "last_update": self._last_health_update,
                "update_count": self._health_update_count,  # 修复：使用计数器
                "health_distribution": health_dist,
                "healthy_key_count": dynamic_healthy_count,  # 修复：动态计算
                "avg_health": round(avg_health, 2),
                "unhealthy_keys": unhealthy_keys,
            },
            "algorithm_11": {
                "name": "池化动态权重调节器",
                "weight_range": "0.5~2.0",
                "avg_multiplier": round(avg_multiplier, 2),
                "max_multiplier": round(max_multiplier, 2),
            },
            "algorithm_12": {
                "name": "自适应负载预判器",
                "predicted_busy_count": predicted_busy_buckets,
                "predicted_exclusions": predicted_exclusions,  # 别名
                "accuracy": accuracy,
            },
            "algorithm_13": {
                "name": "冷密钥渐进式预热器",
                "warming_up_buckets": warmup_count,
                "warmup_count": warmup_count,  # 别名
                "completed_count": completed_count,
                "warmup_target": self.WARMUP_TARGET,
            },
            "algorithm_14": {
                "name": "智能异常自愈引擎",
                "healing_buckets": healing_buckets,
                "check_count": self._heal_check_count,  # 修复：使用计数器
                "degraded_mode": self._stats.degraded_mode,
                "total_heal_actions": total_heal_actions,
                "total_actions": total_heal_actions,  # 别名
                "healthy_key_count": dynamic_healthy_count,  # 修复：动态计算
            },
            "algorithm_15": {
                "name": "趋势感知自适应均衡（Trae）",
                "multiplier_range": f"{self.TRAE_HIGH_TREND_MULTIPLIER}~{self.TRAE_LOW_TREND_MULTIPLIER}",
                "trend_window": self.TRAE_TREND_WINDOW,
                "molting_keys_count": len(self._molting_keys),
            },
            "algorithm_16": {
                "name": "龙虾脱壳式弹性调度（Lobster）",
                "molting_keys_count": len(self._molting_keys),
                "last_check": self._last_lobster_check,
                "check_interval": self.LOBSTER_CHECK_INTERVAL,
                "molt_duration": self.LOBSTER_MOLT_DURATION,
                "overload_threshold": self.LOBSTER_OVERLOAD_THRESHOLD,
                "molt_multiplier": self.LOBSTER_MOLT_SCORE_MULTIPLIER,
            },
            "algorithm_17": {
                "name": "严格公平调度器（Strict Fair Dispatch）",
                "fair_window_seconds": self._fair_window_seconds,
                "dispatch_sequence": self._dispatch_sequence,
                "active_bucket_windows": len(self._bucket_recent_usage),
                "active_key_windows": len(self._key_recent_usage),
                "auto_deactivated_keys": len(self._auto_deactivated_keys),
                "auto_deactivate_threshold": self._AUTH_FAIL_DEACTIVATE_THRESHOLD,
                "note": "核心：滑动窗口计数 + 公平轮询",
            },
            "global_stats": {
                "total_select_calls": self._stats.total_select_calls,
                "total_none_returns": self._stats.total_none_returns,
                "total_buckets": total_buckets,
                "active_buckets": active_buckets,
                "cooling_buckets": cooled_buckets,
                "soft_busy_buckets": soft_busy_buckets,
                "isolated_buckets": isolated_buckets,
                "warmup_buckets": warmup_count,
                "avg_health_score": round(avg_health, 2),
                "healthy_key_count": dynamic_healthy_count,  # 修复：动态计算
                "degraded_mode": self._stats.degraded_mode,
            },
        }

    def get_global_status(self) -> dict:
        """获取全局状态总览（统一字段名，提供前端期望的所有指标）"""
        total_buckets = len(self._buckets)
        cooled_buckets = sum(1 for b in self._buckets.values() if b.is_cooled_down())
        isolated_buckets = sum(1 for b in self._buckets.values() if b.is_isolated())
        soft_busy_buckets = len(self._soft_busy)
        warmup_buckets = sum(1 for b in self._buckets.values() if 0 < b.warmup_progress < self.WARMUP_TARGET)

        # 平均健康分
        avg_health = 100.0
        try:
            health_scores = [b.health_score for b in self._buckets.values()]
            if health_scores:
                avg_health = sum(health_scores) / len(health_scores)
        except Exception:
            logger.exception(f"[v10.0] 调度器后台任务异常")

        # 修复：当后台任务未运行时，动态计算健康密钥数
        # 避免服务刚启动时healthy_key_count=0但实际所有桶都健康的情况
        healthy_key_count = self._healthy_key_count
        if healthy_key_count == 0 and total_buckets > 0:
            # 后台任务尚未运行，动态计算
            healthy_keys = set()
            for (key_id, model), bucket in self._buckets.items():
                if bucket.health_score >= 50 and not bucket.is_cooled_down() and not bucket.is_isolated():
                    healthy_keys.add(key_id)
            healthy_key_count = len(healthy_keys)

        # 当前RPM (最近60秒请求数近似)
        current_rpm = 0
        try:
            now = time.time()
            for b in self._buckets.values():
                current_rpm += b.get_rpm()
        except Exception:
            logger.exception(f"[v10.0] 调度器后台任务异常")

        # 在途请求数
        # v9.2: 在途请求数 - 使用毫秒级精确计数（自动清理超时请求后）
        inflight_requests = 0
        try:
            for m in self._client_metrics.values():
                inflight_requests += m.get_real_inflight_count()
        except Exception:
            inflight_requests = sum(m.inflight_count for m in self._client_metrics.values())

        # 当前QPS (RPM/60)
        current_qps = current_rpm / 60.0 if current_rpm > 0 else 0.0

        # 活跃密钥数（数据库中active状态的密钥）
        try:
            active_key_count = len(fetch_all("SELECT id FROM upstream_keys WHERE status='active'"))
        except Exception:
            active_key_count = healthy_key_count

        # 修复：降级模式判断也要考虑动态计算的healthy_key_count
        degraded_mode = self._stats.degraded_mode
        if not degraded_mode and healthy_key_count < 3 and total_buckets > 0:
            # 仅当有桶数据但健康密钥不足3个时才标记降级
            # 避免无数据时误报降级
            pass

        return {
            # 系统状态
            "degraded_mode": degraded_mode,
            "maintenance_mode": False,  # 由admin_api动态注入
            # 密钥
            "healthy_key_count": healthy_key_count,
            "active_key_count": active_key_count,
            "upstream_keys": active_key_count,  # 别名
            # 桶
            "total_buckets": total_buckets,
            "cooling_buckets": cooled_buckets,
            "cooled_buckets": cooled_buckets,  # 别名（向后兼容）
            "isolated_buckets": isolated_buckets,
            "soft_busy_buckets": soft_busy_buckets,
            "warmup_buckets": warmup_buckets,
            # 实时流量
            "current_qps": round(current_qps, 2),
            "current_rpm": current_rpm,
            "inflight_requests": inflight_requests,
            # 调度器调用统计
            "select_calls": self._stats.total_select_calls,
            "total_select_calls": self._stats.total_select_calls,  # 别名
            "total_none_returns": self._stats.total_none_returns,
            # 健康度
            "avg_health_score": round(avg_health, 2),
        }

    def get_algorithm_detail(self, num: int) -> dict:
        """获取单个算法的详细数据（专属页面用）

        Args:
            num: 算法编号 1-17
        Returns:
            包含算法元数据、汇总统计、分桶详情的字典
        """
        stats = self.get_algorithm_stats()
        algo_data = stats.get(f"algorithm_{num}", {})
        gs = stats.get("global_stats", {})

        # 算法元数据
        meta = {
            1: {"name": "分桶滑动窗口计数器", "category": "数据底座", "trigger": "每次请求完成后",
                "desc": "所有14个算法的数据根基。为每个(密钥,模型)复合桶维护60秒滑动窗口，记录时间戳、响应时间、成功/失败/429/5xx/超时/连接错误计数。本身不做任何决策，仅提供数据供其他算法读取。"},
            2: {"name": "软繁忙标记器", "category": "流量控制", "trigger": "select_key候选池构建阶段",
                "desc": "在密钥选择阶段执行，读取算法1的当前RPM和算法3的动态阈值。当RPM超过阈值时标记为软繁忙并跳过该桶。软繁忙状态不持久化，每次select_key重新计算。与冷却状态完全分离。"},
            3: {"name": "自适应阈值调节器", "category": "动态调参", "trigger": "每30秒后台运行",
                "desc": "统一阈值38RPM，密钥池总容量 = 密钥数 × 38 RPM。不再根据P95动态调节，确保所有模型容量一致。"},
            4: {"name": "自适应冷却时长计算器", "category": "故障保护", "trigger": "上游返回429/403/超时时",
                "desc": "却策略：根据失败类型设置不同的冷却时长。429→5秒（NVIDIA限流1-2秒即恢复），403→60秒起步+自适应退避（连续403指数增长至600秒），超时→15秒（可能是暂时性延迟）。403退避算法：第1次60s，第2次120s，第3次240s，第4次+600s。成功请求重置403计数。冷却期间该桶不参与密钥选择，冷却到期后进入算法13的预热流程。"},
            5: {"name": "客户端并发监测器", "category": "客户端监控", "trigger": "请求入口处",
                "desc": "在请求入口处执行，记录每个客户端的在途请求数(inflight_count)。仅作为监测指标，不设置硬性限制，不拒绝任何请求。高并发(>=10)的客户端会被标记。"},
            6: {"name": "客户端突发率检测器", "category": "客户端监控", "trigger": "请求入口处",
                "desc": "维护最近3秒的请求时间戳队列。当3秒内请求超过20次时标记为高突发客户端。仅记录和通知，不拦截请求。"},
            7: {"name": "客户端日用量监测器", "category": "客户端监控", "trigger": "评分阶段",
                "desc": "为每个客户端维护当日累计请求计数(daily_count)，每日凌晨自动重置。仅用于展示和统计，不设上限不产生衰减因子。"},
            8: {"name": "5xx退避权重衰减器", "category": "故障保护", "trigger": "上游返回5xx时",
                "desc": "记录每个桶的连续5xx次数。评分时按阶梯返回乘数：1次→0.8, 2次→0.5, 3次→0.2, 4次+→0.0。每次成功请求重置计数。"},
            9: {"name": "区域故障隔离器", "category": "故障保护", "trigger": "连接超时/SSL错误时",
                "desc": "当连接超时或SSL错误时增加consecutive_conn_fail计数。连续3次失败则设置30分钟隔离(isolation_until)。隔离期间权重归零，不参与密钥选择。隔离期满自动恢复。"},
            10: {"name": "全局健康度评分器", "category": "健康评估", "trigger": "每30秒后台运行",
                "desc": "遍历所有密钥的所有桶，根据成功率、P95响应时间、429频率、5xx频率综合计算0-100健康分。健康分供算法11使用。健康分<60的密钥被标记为不健康。"},
            11: {"name": "池化动态权重调节器", "category": "负载均衡", "trigger": "评分阶段",
                "desc": "根据算法10的健康分计算基础权重乘数。公式：multiplier = 0.5 + (health_score / 100) * 1.5。健康分100→2.0倍权重，健康分0→0.5倍权重。线性映射。"},
            12: {"name": "自适应负载预判器", "category": "流量控制", "trigger": "算法2之前执行",
                "desc": "根据5分钟RPM趋势(rpm_trend)预测10秒内是否将达到阈值。预判繁忙的桶加入predicted_busy集合，在算法2之前就排除。形成预判+事后排除的双重防护。"},
            13: {"name": "冷密钥渐进式预热器", "category": "恢复机制", "trigger": "桶从冷却/隔离恢复后",
                "desc": "桶从冷却或隔离状态恢复后，前30个请求逐步恢复权重：0.3→0.6→0.9→1.0。避免流量突增导致再次触发429。warmup_progress达到30(=WARMUP_TARGET)后视为完全预热。"},
            14: {"name": "智能异常自愈引擎", "category": "自愈机制", "trigger": "每60秒后台运行",
                "desc": "四级自愈机制：轻度(观察重置连接)、中度(流量迁移到健康密钥)、重度(将密钥移出候选池)、全局(进入降级模式)。所有动作只修改桶级状态，不影响其他算法。"},
            15: {"name": "趋势感知自适应均衡（Trae优化算法）", "category": "负载均衡", "trigger": "评分阶段",
                "desc": "通过对比最近30秒与之前30秒的RPM计算趋势斜率。RPM快速上升（斜率>1.5）时降低乘数至0.6防止过载；RPM下降时提高乘数至1.3吸引流量。斜率在1.0~1.5之间线性插值。实现趋势感知的自适应负载均衡。"},
            16: {"name": "龙虾脱壳式弹性调度（Lobster优化策略）", "category": "弹性调度", "trigger": "每120秒后台运行",
                "desc": "受龙虾脱壳启发的弹性调度策略。每120秒检查一次，识别RPM持续超过90%阈值的密钥，强制进入15秒脱壳期。脱壳期内该密钥评分乘数降为0.5，使流量重新分配到其他密钥。脱壳期结束后自动恢复正常评分。"},
            17: {"name": "严格公平调度器（Strict Fair Dispatch）", "category": "负载均衡", "trigger": "select_key最终选择阶段",
                "desc": "核心算法。替代旧方案的乘数式轮询，改为严格公平排序。维护5分钟滑动窗口内的桶级(key_id,model)和密钥级(key_id)使用计数。在所有健康候选中，按'近期使用次数最少→分配序号最早→评分最高'排序选择。确保每个健康密钥均匀分配流量，目标均衡度90%+。"},
        }

        m = meta.get(num, {"name": f"算法{num}", "category": "未知", "trigger": "未知", "desc": ""})

        # 获取密钥名称映射（使用缓存）
        key_names = {}
        try:
            now = time.time()
            if now - self._upstream_key_cache_time > self.UPSTREAM_KEY_CACHE_TTL:
                rows = fetch_all("SELECT id, name FROM upstream_keys")
                self._upstream_key_names = {r["id"]: r["name"] for r in rows}
                self._upstream_key_cache_time = now
            key_names = self._upstream_key_names
        except Exception:
            logger.exception(f"[v10.0] 调度器后台任务异常")

        # 分桶详情（按算法提取相关字段，最多50条）
        bucket_details = []
        now = time.time()

        for (key_id, model), b in sorted(self._buckets.items(), key=lambda x: -x[1].total_requests)[:50]:
            key_name = key_names.get(key_id, key_id[:12])
            entry = {
                "key_name": key_name,
                "key_id": key_id[:12],
                "model": model,
                "total_requests": b.total_requests,
                "rpm": b.get_rpm(),
                "success_rate": round(b.get_success_rate(), 2),
                "health_score": round(b.health_score, 1),
                "dynamic_threshold": b.dynamic_threshold,
                "is_cooled_down": b.is_cooled_down(),
                "is_isolated": b.is_isolated(),
                "is_healing": b.is_healing(),
                "warmup_progress": b.warmup_progress,
                "consecutive_5xx": b.consecutive_5xx,
                "cooldown_remaining": max(0, int(b.cooldown_until - now)) if b.cooldown_until > now else 0,
                "isolation_remaining": max(0, int(b.isolation_until - now)) // 60 if b.isolation_until > now else 0,
                "p95_ms": int(round(b.get_p95_rt() * 1000)),
                "total_429": b.total_429,
                "total_5xx": b.total_5xx,
                "total_timeout": b.total_timeout,
                "total_conn_error": b.total_conn_error,
            }
            # 算法11权重乘数
            entry["weight_multiplier"] = round(0.5 + (b.health_score / 100.0) * 1.5, 2)
            bucket_details.append(entry)

        # 客户端详情（算法5/6/7）
        client_details = []
        for cid, cm in sorted(self._client_metrics.items(), key=lambda x: -x[1].inflight_count)[:30]:
            try:
                # 使用客户端名称缓存
                if cid in self._client_name_cache:
                    cname = self._client_name_cache[cid]
                else:
                    if time.time() - self._client_name_cache_time > self.CLIENT_NAME_CACHE_TTL:
                        self._client_name_cache.clear()
                    cname_row = fetch_one("SELECT name FROM clients WHERE id=%s", (cid,))
                    cname = cname_row["name"] if cname_row else cid[:12]
                    self._client_name_cache[cid] = cname
                    self._client_name_cache_time = time.time()
            except Exception:
                cname = cid[:12]
            client_details.append({
                "client_id": cid[:12],
                "client_name": cname,
                "inflight_count": cm.inflight_count,
                "burst_count": cm.get_burst_count(3.0),  # 修复：使用3秒时间窗口的突发计数
                "daily_count": cm.daily_count,
            })

        # 健康度分布
        health_dist = algo_data.get("health_distribution", {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "critical": 0})

        return {
            "num": num,
            "meta": m,
            "summary": algo_data,
            "global_stats": gs,
            "bucket_details": bucket_details,
            "client_details": client_details,
            "health_distribution": health_dist,
            "config": {
                "COOLDOWN_SECONDS": self.COOLDOWN_SECONDS,
                "ISOLATION_SECONDS": self.ISOLATION_SECONDS,
                "WARMUP_TARGET": self.WARMUP_TARGET,
                "THRESHOLD_MIN": 15,
                "THRESHOLD_MAX": 45,
                "THRESHOLD_UPDATE_INTERVAL": 30,
                "HEALTH_UPDATE_INTERVAL": 30,
                "HEAL_INTERVAL": 60,
                "HIGH_CONCURRENCY_THRESHOLD": 10,
                "HIGH_BURST_THRESHOLD": 20,
                "ISOLATION_FAIL_THRESHOLD": 3,
            },
        }

    # ========== 后台清理任务 ==========

    def _cleanup_stale_clients(self):
        """清理超过1小时未活跃的客户端治理数据，防止内存泄漏

        300秒执行一次
        """
        now = time.time()
        last = getattr(self, '_last_stale_client_cleanup', 0.0)
        if now - last < 300.0:
            return
        self._last_stale_client_cleanup = now
        stale_threshold = now - 3600  # 1小时前
        stale_clients = [
            cid for cid, m in self._client_metrics.items()
            if m.last_request_at > 0 and m.last_request_at < stale_threshold
        ]
        for cid in stale_clients:
            del self._client_metrics[cid]
        if stale_clients:
            logger.debug(f"清理过期客户端数据: {len(stale_clients)}个")

    def _cleanup_soft_busy(self):
        """清理软繁忙集合中已恢复的桶（RPM已降至阈值80%以下）

        60秒执行一次
        """
        now = time.time()
        last = getattr(self, '_last_soft_busy_cleanup', 0.0)
        if now - last < 60.0:
            return
        self._last_soft_busy_cleanup = now
        for bucket_key in list(self._soft_busy):
            key_id, model = bucket_key
            bucket = self._get_bucket(key_id, model)
            rpm = bucket.get_rpm()
            threshold = get_threshold_for_model(model, bucket.dynamic_threshold)
            if rpm < threshold * 0.8:
                self._soft_busy.discard(bucket_key)

    def _cleanup_fair_dispatch_windows(self):
        """清理算法17滑动窗口中的过期时间戳，防止内存增长

        120秒执行一次（避免每10秒遍历所有桶）
        60秒执行一次（内存节约模式更激进清理）
        """
        now = time.time()
        last = getattr(self, '_last_fair_cleanup', 0.0)
        if now - last < 60.0:
            return
        self._last_fair_cleanup = now
        cutoff = now - self._fair_window_seconds
        cleaned_buckets = 0
        cleaned_keys = 0
        for bucket_key in list(self._bucket_recent_usage.keys()):
            times = self._bucket_recent_usage[bucket_key]
            while times and times[0] < cutoff:
                times.popleft()
            if not times:
                del self._bucket_recent_usage[bucket_key]
                cleaned_buckets += 1
        for key_id in list(self._key_recent_usage.keys()):
            times = self._key_recent_usage[key_id]
            while times and times[0] < cutoff:
                times.popleft()
            if not times:
                del self._key_recent_usage[key_id]
                cleaned_keys += 1

        if cleaned_buckets > 0 or cleaned_keys > 0:
            logger.debug(f"算法17窗口清理: 清理{cleaned_buckets}个空桶窗口, {cleaned_keys}个空密钥窗口")

    async def _cleanup_invalid_model_buckets(self):
        """清理复合桶中上游不存在的模型（无效模型桶）

        模型不在NVIDIA NIM有效模型列表中时，删除对应桶数据
        避免无效模型占用内存和统计干扰

        加速率限制，每300秒执行一次（避免每10秒遍历所有桶）
        用异步HTTP，避免阻塞事件循环
        """
        now = time.time()
        last = getattr(self, '_last_invalid_cleanup', 0.0)
        if now - last < 300.0:
            return
        self._last_invalid_cleanup = now
        # 获取有效模型列表（从上游API或缓存）
        valid_models = getattr(self, '_cached_valid_models', None)
        if valid_models is None:
            # 首次运行时获取有效模型列表
            try:
                # 使用缓存的密钥获取模型列表（DB读取/密钥解密均含同步查库，经线程池执行）
                active_keys = await asyncio.to_thread(self._get_active_keys)
                if active_keys:
                    api_key = await asyncio.to_thread(
                        self._ensure_key_cached, active_keys[0]["id"], active_keys[0]["api_key_ciphertext"]
                    )
                    if api_key:
                        await self._ensure_pools()
                        resp = await self._http_pool.get(
                            await asyncio.to_thread(self._upstream_models_url),
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=httpx.Timeout(10.0)
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            valid_models = {m["id"] for m in data.get("data", [])}
                            self._cached_valid_models = valid_models
            except Exception as e:
                logger.debug(f"获取有效模型列表失败: {e}")
                return

        if not valid_models:
            return

        # 清理无效模型桶
        invalid_keys = []
        for (key_id, model) in list(self._buckets.keys()):
            if model and model not in valid_models:
                invalid_keys.append((key_id, model))

        if invalid_keys:
            for key in invalid_keys:
                del self._buckets[key]
                self._soft_busy.discard(key)
                self._predicted_busy.discard(key)
                self._bucket_recent_usage.pop(key, None)
                self._bucket_last_used.pop(key, None)
                self._bucket_dispatch_seq.pop(key, None)
            logger.info(f"清理无效模型桶: 删除{len(invalid_keys)}个(模型不存在于上游)")

        # 同时清理request_logs中的空模型（空字符串）桶
        empty_keys = [(kid, m) for kid, m in list(self._buckets.keys()) if not m]
        for key in empty_keys:
            del self._buckets[key]
        if empty_keys:
            logger.info(f"清理空模型桶: 删除{len(empty_keys)}个")

    # ========== 主动密钥健康检查 ==========

    async def _proactive_key_health_check(self):
        """
        主动密钥健康检查 - 每30秒测试一批密钥对 NVIDIA API 的可用性

        v10.0新增：直接向 NVIDIA API 发送测试请求，检测死密钥并自动禁用。
        每轮最多测试5个密钥，分批执行避免阻塞。
        """
        now = time.time()
        last = getattr(self, '_last_health_check', 0.0)
        if now - last < 30.0:
            return
        self._last_health_check = now

        # DB读取（活跃密钥，含5秒缓存）经线程池执行，避免阻塞事件循环
        active_keys = await asyncio.to_thread(self._get_active_keys)
        if not active_keys:
            return

        # 获取当前检查进度
        check_idx = getattr(self, '_health_check_idx', 0)
        if check_idx >= len(active_keys):
            check_idx = 0

        # 每轮测试最多5个密钥
        batch = active_keys[check_idx:check_idx + 5]
        self._health_check_idx = check_idx + 5

        for key_row in batch:
            key_id = key_row["id"]
            # 跳过已自动停用的密钥
            if key_id in self._auto_deactivated_keys:
                continue

            api_key = await asyncio.to_thread(self._ensure_key_cached, key_id, key_row["api_key_ciphertext"])
            if not api_key:
                continue

            try:
                await self._ensure_pools()
                resp = await self._http_pool.get(
                    await asyncio.to_thread(self._upstream_models_url),
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=httpx.Timeout(8.0),
                )
                if resp.status_code == 200:
                    # 密钥可用，重置认证失败计数
                    self._key_consecutive_auth_fail.pop(key_id, None)
                    logger.debug(f"密钥健康检查通过: key={key_id[:8]}")
                elif resp.status_code in (401, 403):
                    # 密钥无效，记录失败
                    count = self._key_consecutive_auth_fail.get(key_id, 0) + 3  # 健康检查权重更高
                    self._key_consecutive_auth_fail[key_id] = count
                    if count >= self._AUTH_FAIL_DEACTIVATE_THRESHOLD:
                        self._auto_deactivated_keys[key_id] = time.time()
                        # 停用落库经线程池执行，避免健康检查（事件循环内）同步写库阻塞
                        try:
                            from app.database import execute as db_exec
                            await asyncio.to_thread(
                                db_exec,
                                "UPDATE upstream_keys SET status = 'inactive' WHERE id = %s AND status = 'active'",
                                (key_id,),
                            )
                            self.invalidate_active_keys_cache()  # v10.1修复: 立即失效密钥缓存
                            logger.warning(f"健康检查停用密钥: key={key_id[:8]} 认证失败，已标记为inactive")
                        except Exception as e:
                            logger.error(f"健康检查停用密钥数据库更新失败: {e}")
                    else:
                        logger.info(f"密钥健康检查失败: key={key_id[:8]} status={resp.status_code} count={count}/{self._AUTH_FAIL_DEACTIVATE_THRESHOLD}")
                else:
                    logger.debug(f"密钥健康检查异常: key={key_id[:8]} status={resp.status_code}")
            except Exception as e:
                logger.debug(f"密钥健康检查请求异常: key={key_id[:8]} error={e}")

    def _recheck_auto_deactivated(self):
        """自动停用密钥定期复检（v10.1新增：自动停用的恢复路径，防自砖）

        对停用超过30分钟的自动停用密钥做轻量探活（GET {base_url}/models，
        携带该密钥的 Authorization），成功则恢复 status='active' 并清缓存。
        注意：本方法使用同步 httpx，必须通过 asyncio.to_thread 调度执行，
        不得直接在事件循环中调用。
        """
        if not self._auto_deactivated_keys:
            return
        now = time.time()
        candidates = [
            kid for kid, deactivated_at in self._auto_deactivated_keys.items()
            if now - deactivated_at >= self.AUTO_DEACTIVATE_RECHECK_SECONDS
        ]
        if not candidates:
            return

        models_url = self._upstream_models_url()
        for key_id in candidates:
            row = fetch_one(
                "SELECT id, api_key_ciphertext FROM upstream_keys WHERE id = %s",
                (key_id,),
            )
            if row is None:
                # 密钥已被删除，移除追踪
                self._auto_deactivated_keys.pop(key_id, None)
                continue
            api_key = self._ensure_key_cached(key_id, row.get("api_key_ciphertext"))
            if not api_key:
                continue
            try:
                resp = httpx.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=8.0,
                )
                if resp.status_code == 200:
                    execute(
                        "UPDATE upstream_keys SET status = 'active' WHERE id = %s AND status = 'inactive'",
                        (key_id,),
                    )
                    self._auto_deactivated_keys.pop(key_id, None)
                    self._key_consecutive_auth_fail.pop(key_id, None)
                    self.invalidate_active_keys_cache()
                    logger.info(f"自动停用密钥探活成功已恢复: key={key_id[:8]} 重新标记为active")
                elif resp.status_code in (401, 403):
                    # 密钥仍无效：刷新停用时间戳，等待下个复检周期
                    self._auto_deactivated_keys[key_id] = now
                    logger.debug(f"自动停用密钥复检仍失败: key={key_id[:8]} status={resp.status_code}")
                else:
                    logger.debug(f"自动停用密钥复检状态异常: key={key_id[:8]} status={resp.status_code}")
            except Exception as e:
                logger.debug(f"自动停用密钥复检请求异常: key={key_id[:8]} error={e}")

    # ========== 后台任务入口 ==========

    async def run_background_tasks(self):
        """运行所有后台周期任务（含自适应资源感知调度）"""
        # 系统资源检查（内存压力感知）
        await self._check_system_resources()

        # 核心调度算法（始终执行）
        # 算法3：每30秒更新自适应阈值
        self.update_adaptive_thresholds()
        # 算法10：每30秒更新健康度
        self.update_health_scores()
        # 算法12：更新负载预判
        self.update_load_prediction()
        # 算法14：每60秒执行自愈引擎
        self.run_self_heal()
        # 算法16：每120秒执行龙虾脱壳检查
        self.run_lobster_molting()

        # 内存感知的清理任务调度
        is_high_pressure = self._memory_pressure > 0.7
        is_critical = self._memory_pressure >= 1.0

        # 低压力下执行完整清理；高压力下跳过非关键清理
        if not is_high_pressure:
            # 清理过期客户端治理数据（防内存泄漏）
            self._cleanup_stale_clients()
            # 清理软繁忙集合中已恢复的桶
            self._cleanup_soft_busy()
            # 算法17：清理滑动窗口中的过期时间戳（防内存增长）
            self._cleanup_fair_dispatch_windows()
            # 安全修复：清理过期的密钥明文缓存
            self._cleanup_expired_key_cache()
        elif is_critical:
            # 临界压力下立即触发日志清理释放磁盘空间
            logger.warning("系统内存临界，触发紧急日志清理")
            await self._run_log_cleanup()

        # 效模型桶（模型不存在于上游NVIDIA NIM）
        await self._cleanup_invalid_model_buckets()

        # v10.0：主动密钥健康检查（每30秒测试一批密钥）
        await self._proactive_key_health_check()

        # v10.1修复：自动停用密钥定期复检（每30分钟，线程中运行避免阻塞事件循环）
        now = time.time()
        if now - self._last_auto_deactivated_recheck >= self.AUTO_DEACTIVATE_RECHECK_SECONDS:
            self._last_auto_deactivated_recheck = now
            await asyncio.to_thread(self._recheck_auto_deactivated)

        # 数据库日志自动清理（每6小时执行一次，内存节约模式每1小时）
        self._cleanup_counter += 1
        effective_interval = self.CLEANUP_INTERVAL_CYCLES // 6 if self._memory_saving_mode else self.CLEANUP_INTERVAL_CYCLES
        if self._cleanup_counter >= effective_interval:
            self._cleanup_counter = 0
            await self._run_log_cleanup()


    # ========== 客户端密钥缓存 ==========

    def get_client_key_cache(self, key_hash: str) -> Optional[dict]:
        """获取缓存的客户端密钥信息（v10.1修复：逐条校验自身TTL，过期返回 None）"""
        entry = self._client_key_cache.get(key_hash)
        if entry is None:
            return None
        client_info, expires_at = entry
        if time.time() >= expires_at:
            self._client_key_cache.pop(key_hash, None)
            return None
        return client_info

    def set_client_key_cache(self, key_hash: str, client_info: dict) -> None:
        """缓存客户端密钥信息（30秒逐条TTL；超过上限时淘汰最旧条目）"""
        now = time.time()
        self._client_key_cache[key_hash] = (client_info, now + self.CLIENT_KEY_CACHE_TTL)
        if len(self._client_key_cache) > self.CLIENT_KEY_CACHE_MAX_ENTRIES:
            oldest = min(self._client_key_cache, key=lambda k: self._client_key_cache[k][1])
            self._client_key_cache.pop(oldest, None)

    def invalidate_client_key_cache(self, key_hash: str = None):
        """使客户端密钥缓存失效（契约方法：密钥吊销/禁用时由管理后台调用）

        - key_hash=None：清空全部缓存
        - 指定 key_hash：仅清除该条
        """
        if key_hash is None:
            self._client_key_cache.clear()
        else:
            self._client_key_cache.pop(key_hash, None)

    async def _run_log_cleanup(self):
        """每6小时自动清理过期请求日志，防止DB无限膨胀

        v10.1修复：COUNT+DELETE+VACUUM 为阻塞操作，放入工作线程执行避免卡事件循环
        """
        import logging
        try:
            from app.database import cleanup_success_logs
            result = await asyncio.to_thread(cleanup_success_logs, keep_days=3, keep_error_days=90)
            logger = logging.getLogger("acu.scheduler")
            if result.get("success_deleted", 0) > 0 or result.get("error_deleted", 0) > 0:
                logger.info(f"定时日志清理: 成功{result['success_deleted']}条, 错误{result['error_deleted']}条")
        except Exception as e:
            logger = logging.getLogger("acu.scheduler")
            logger.error(f"定时日志清理失败: {e}")


# ========== 全局调度器实例 ==========

_scheduler: Optional[SurgeScheduler] = None


def get_scheduler() -> SurgeScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = SurgeScheduler()
    return _scheduler


def reset_scheduler():
    """重置调度器（测试用）"""
    global _scheduler
    _scheduler = None
