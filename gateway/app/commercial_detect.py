"""
商用行为识别与机器人检测模块 - v9.2（新增 UA 分析 + 时序分析 + 指纹检测）

核心维度（十一维度）：
1.  请求间隔分布（标准差+变异系数）- 12%
2.  模型切换频率（10分钟内切换次数）- 8%
3.  并发连接数（平均并发）- 8%
4.  语义相似度（TF-IDF指纹+Jaccard相似度）- 12%
5.  IP分布分析（24小时内唯一IP数）- 8%
6.  单Key突发检测（5分钟窗口请求数）- 8%
7.  蒸馏行为检测（高输出Token比+重复prompt模式）- 12%  【社会工程学】
8.  时间窗口异常（非人类作息模式）- 5%  【社会工程学】
9.  账号农场特征（多Key协同+注册模式）- 8%  【社会工程学】
10. 浏览器指纹与UA异常（自动化工具/无头浏览器检测）- 12%  【v9.2 新增】
11. 请求头模式与协议异常（脚本/爬虫特征检测）- 7%  【v9.2 新增】

置信度评分：0~100
- 0~30: 正常用户
- 31~60: 可疑
- 61~100: 高度疑似商用/机器

v9.2 新增特性：
【维度10】浏览器指纹与UA异常：
  - 无头浏览器/自动化工具UA检测（HeadlessChrome, PhantomJS, Selenium, Puppeteer等）
  - 不合理的UA/OS组合检测（如Mac OS + Edge）
  - 缺少关键浏览器特征头（Accept-Language, Sec-Fetch-*等）
  - WebDriver/自动化标识检测

【维度11】请求头模式与协议异常：
  - HTTP/1.0 而非 HTTP/1.1 或 HTTP/2（爬虫特征）
  - Accept 头过窄或缺失（非浏览器行为）
  - 重复固定 User-Agent（脚本特征）
  - 缺少 Referer/Origin（CSRF合规性检查）

接线状态说明（v10.1，避免下个维护者再踩坑）：
- record_content / record_headers / check_rate_limit 目前**没有任何调用方**（未接线）：
  维度4(语义相似度)、维度10(浏览器指纹)、维度11(请求头异常)因此恒无数据。
- 置信度已按"有数据维度权重归一化"计算：无数据的维度不参与计分，
  不会因恒0维度拉低数学上限（原先即使其余维度满分也无法超过69%）。
- 特殊监控名单不再硬编码真实用户名，改读环境变量 AQUA_WATCHLIST
  （逗号分隔的客户端名称，默认为空=不监控任何指定用户）。
"""
import json
import math
import os
import re
import time
import logging
from collections import defaultdict, deque
from typing import Optional, Set, Dict

from app.database import fetch_one, fetch_all, execute, utcnow, insert_audit

logger = logging.getLogger("acu.commercial")

# 特殊监控名单（v10.1修复：原代码硬编码真实用户名监控名单，已删除）
# 通过环境变量 AQUA_WATCHLIST 配置，逗号分隔客户端名称，默认为空
WATCHLIST = [w.strip().lower() for w in os.environ.get("AQUA_WATCHLIST", "").split(",") if w.strip()]


class CommercialDetector:
    """商用行为识别引擎"""

    # 管理控制开关
    detection_enabled: bool = True
    confidence_threshold: int = 70

    def __init__(self):
        # 请求内容缓存（用于模板化检测）
        self._content_cache: dict = defaultdict(lambda: deque(maxlen=100))
        # 语义指纹缓存
        self._fingerprint_cache: dict = defaultdict(lambda: deque(maxlen=50))
        # 模型切换记录
        self._model_switches: dict = defaultdict(lambda: deque(maxlen=50))
        # IP分布记录：client_id -> set of IPs
        self._ip_history: Dict[str, set] = defaultdict(set)
        # IP时间戳记录（用于清理）：client_id -> deque of (timestamp, ip)
        self._ip_timestamps: dict = defaultdict(lambda: deque(maxlen=500))
        # 单Key突发检测：api_key -> deque of timestamps
        self._key_timestamps: dict = defaultdict(lambda: deque(maxlen=500))
        # 白名单
        self.whitelist: Set[str] = set()
        # 上次清理时间
        self._last_cleanup: float = time.time()

        # ===== v2.0 社会工程学新增缓存 =====
        # 蒸馏行为：Token比率记录 client_id -> deque of (timestamp, prompt_tokens, completion_tokens)
        self._token_ratio_cache: dict = defaultdict(lambda: deque(maxlen=200))
        # 时间窗口：请求时间分布 client_id -> deque of (timestamp, hour_of_day)
        self._request_time_cache: dict = defaultdict(lambda: deque(maxlen=500))
        # 账号农场：IP->client_id映射（检测多账号共享IP）
        self._ip_client_map: Dict[str, set] = defaultdict(set)
        # 限速状态：client_id -> {limited_until, reason, rate_limit}
        self._rate_limits: dict = {}

        # ===== v9.2 浏览器指纹与请求头缓存 =====
        # UA历史记录：client_id -> deque of user_agent strings
        self._ua_history: dict = defaultdict(lambda: deque(maxlen=20))
        # 请求头快照：client_id -> deque of (timestamp, header_dict)
        self._header_history: dict = defaultdict(lambda: deque(maxlen=50))
        # HTTP版本记录：client_id -> deque of http_version
        self._http_version_history: dict = defaultdict(lambda: deque(maxlen=20))

    # ==================== 管理控制方法 ====================

    def enable_detection(self):
        """启用商用检测"""
        self.detection_enabled = True
        logger.info("商用检测已启用")

    def disable_detection(self):
        """禁用商用检测"""
        self.detection_enabled = False
        logger.info("商用检测已禁用")

    def set_confidence_threshold(self, threshold: int):
        """设置置信度阈值"""
        if 0 <= threshold <= 100:
            self.confidence_threshold = threshold
            logger.info(f"置信度阈值已设置为 {threshold}")
        else:
            raise ValueError(f"阈值必须在0-100之间，当前值: {threshold}")

    def add_to_whitelist(self, client_id: str):
        """将客户端添加到白名单"""
        self.whitelist.add(client_id)
        logger.info(f"客户端 {client_id} 已添加到白名单")

    def remove_from_whitelist(self, client_id: str):
        """将客户端从白名单移除"""
        self.whitelist.discard(client_id)
        logger.info(f"客户端 {client_id} 已从白名单移除")

    # ==================== 数据记录方法 ====================

    def record_content(self, client_id: str, content: str):
        """记录请求内容（使用语义指纹替代简单截断）"""
        if not content:
            return
        fingerprint = self._semantic_fingerprint(content)
        if fingerprint:
            self._fingerprint_cache[client_id].append(fingerprint)
            # 同时保留旧的内容缓存用于模板化检测的兼容性
            self._content_cache[client_id].append(content[:200])

    def record_ip(self, client_id: str, ip_address: str):
        """记录客户端IP地址"""
        if not ip_address:
            return
        self._ip_history[client_id].add(ip_address)
        self._ip_timestamps[client_id].append((time.time(), ip_address))
        # v2.0: IP->client映射（用于账号农场检测）
        self._ip_client_map[ip_address].add(client_id)

    def record_key_request(self, api_key: str):
        """记录API Key请求时间戳（用于突发检测）"""
        if api_key:
            self._key_timestamps[api_key].append(time.time())

    # ==================== v2.0 社会工程学数据记录 ====================

    def record_token_usage(self, client_id: str, prompt_tokens: int, completion_tokens: int):
        """记录Token使用情况（用于蒸馏行为检测）"""
        self._token_ratio_cache[client_id].append((time.time(), prompt_tokens, completion_tokens))

    def record_request_time(self, client_id: str):
        """记录请求时间（用于时间窗口异常检测）"""
        now = time.time()
        # 转换为CST小时 (UTC+8)
        cst_hour = int((now + 28800) % 86400 // 3600)
        self._request_time_cache[client_id].append((now, cst_hour))

    # ==================== v9.2 浏览器指纹与请求头记录 ====================

    def record_headers(self, client_id: str, headers: dict, http_version: str = ""):
        """
        记录请求头信息（用于浏览器指纹和UA异常检测）

        参数:
            client_id: 客户端ID
            headers: 请求头字典（包含 user-agent, accept, accept-language, sec-fetch-* 等）
            http_version: HTTP协议版本
        """
        ua = (headers.get("user-agent") or headers.get("User-Agent") or "").strip()
        if ua:
            self._ua_history[client_id].append(ua)
        self._header_history[client_id].append((time.time(), headers))
        if http_version:
            self._http_version_history[client_id].append(http_version)

    def _analyze_browser_fingerprint(self, client_id: str) -> float:
        """
        分析浏览器指纹与UA异常（维度10）

        检测点：
        - 无头浏览器UA标识（HeadlessChrome, PhantomJS等）
        - 自动化工具UA标识（Selenium, Puppeteer, Playwright）
        - 不合理的UA/OS组合
        - 缺少关键浏览器特征头
        - WebDriver标识检测
        - UA频繁切换（脚本特征）
        """
        score = 0.0

        # --- UA 自动化工具检测 ---
        uas = list(self._ua_history.get(client_id, []))
        if uas:
            # 最新UA
            latest_ua = uas[-1].lower()

            # 无头浏览器 / 自动化工具关键词
            automation_keywords = [
                "headless", "phantomjs", "selenium", "puppeteer",
                "playwright", "chromium-browser", "electron",
                "python-requests", "python-httpx", "aiohttp",
                "curl", "wget", "ruby", "go-http-client",
                "axios", "node-fetch", "scrapy", "bot",
                "httpclient", "okhttp", "java",
            ]
            for kw in automation_keywords:
                if kw in latest_ua:
                    score += 25.0
                    logger.debug(f"UA自动化工具检测: client={client_id} keyword={kw} ua={latest_ua[:80]}")
                    break

            # 空UA或过短UA
            if len(latest_ua) < 20:
                score += 15.0

            # UA不一致检测（如果同一个client_id使用多个不同的UA）
            unique_uas = set(uas)
            if len(unique_uas) > 3:
                score += 20.0
            elif len(unique_uas) > 1:
                score += 5.0

        # --- 浏览器特征头检测 ---
        headers_list = list(self._header_history.get(client_id, []))
        if headers_list:
            _, latest_headers = headers_list[-1]

            # 安全检查：缺少Accept-Language是异常特征
            has_accept_lang = any(
                k.lower() == "accept-language" for k in latest_headers
            )
            if not has_accept_lang:
                score += 10.0

            # 缺少Sec-Fetch-*头（现代浏览器会自动发送）
            has_sec_fetch = any(k.lower().startswith("sec-fetch-") for k in latest_headers)
            if not has_sec_fetch:
                score += 8.0

            # 缺少Referer（API直接调用特征）
            has_referer = any(k.lower() == "referer" for k in latest_headers)
            if not has_referer:
                score += 5.0

        return min(100.0, score)

    def _analyze_header_pattern(self, client_id: str) -> float:
        """
        分析请求头模式与协议异常（维度11）

        检测点：
        - HTTP/1.0 使用（爬虫特征）
        - Accept头过窄或缺失
        - 重复固定UA（脚本特征）
        - 异常Connection头模式
        - 缺少标准浏览器头
        """
        score = 0.0

        # --- HTTP版本检测 ---
        http_versions = list(self._http_version_history.get(client_id, []))
        if http_versions:
            # HTTP/1.0 是爬虫/旧脚本特征
            http_1_0_count = sum(1 for v in http_versions if "1.0" in str(v))
            if http_1_0_count > len(http_versions) * 0.5:
                score += 20.0

        # --- Accept头分析 ---
        headers_list = list(self._header_history.get(client_id, []))
        if headers_list:
            _, latest_headers = headers_list[-1]

            # 查找Accept头
            accept_value = ""
            for k, v in latest_headers.items():
                if k.lower() == "accept":
                    accept_value = v
                    break

            if not accept_value:
                score += 15.0  # 缺少Accept头
            elif "*/*" in accept_value:
                pass  # 宽泛Accept正常
            elif len(accept_value) < 20:
                score += 5.0  # Accept过窄

            # 查找Connection头
            conn_value = ""
            for k, v in latest_headers.items():
                if k.lower() == "connection":
                    conn_value = v.lower()
                    break

            # Connection: close 每次都关闭连接（非HTTP/1.1 keep-alive特征）
            if conn_value == "close":
                score += 5.0

        return min(100.0, score)

    # ==================== 语义指纹方法 ====================

    def _semantic_fingerprint(self, content: str) -> set:
        """
        创建TF-IDF-like语义指纹

        从消息内容中提取关键术语，构建特征集合。
        使用简单的停用词过滤和词频归一化。
        """
        if not content:
            return set()

        # 中文和英文分词：按空格、标点、中文字符分割
        # 提取中文字符序列和英文单词
        tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]{2,}', content.lower())

        # 简单停用词表（中英文常见停用词）
        stopwords = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'can', 'shall',
            'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
            'as', 'into', 'through', 'during', 'before', 'after', 'and',
            'but', 'or', 'not', 'no', 'if', 'then', 'than', 'that',
            'this', 'it', 'its', 'my', 'your', 'his', 'her', 'our',
            'their', 'what', 'which', 'who', 'whom', 'how', 'when',
            'where', 'why', 'all', 'each', 'every', 'both', 'few',
            'more', 'most', 'other', 'some', 'such', 'only', 'own',
            'same', 'so', 'too', 'very', 'just', 'because', 'about',
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
            '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去',
            '你', '会', '着', '没有', '看', '好', '自己', '这',
        }

        # 过滤停用词并返回关键术语集合
        key_terms = set()
        for token in tokens:
            if token not in stopwords and len(token) > 1:
                key_terms.add(token)

        return key_terms

    def _analyze_semantic_similarity(self, client_id: str) -> float:
        """
        分析语义相似度

        比较新消息与近期消息的Jaccard相似度。
        如果平均相似度>0.6，说明内容高度重复，疑似商用。
        """
        fingerprints = self._fingerprint_cache.get(client_id, deque())
        if len(fingerprints) < 5:
            return 0.0

        fingerprint_list = list(fingerprints)
        # 过滤掉空指纹
        fingerprint_list = [fp for fp in fingerprint_list if fp]
        if len(fingerprint_list) < 5:
            return 0.0

        # 计算最近消息之间的平均Jaccard相似度
        similarities = []
        # 比较最近的指纹与之前的指纹
        recent_count = min(10, len(fingerprint_list) - 1)
        for i in range(len(fingerprint_list) - recent_count, len(fingerprint_list)):
            fp_current = fingerprint_list[i]
            # 与之前的指纹比较
            for j in range(max(0, i - recent_count), i):
                fp_other = fingerprint_list[j]
                if not fp_current or not fp_other:
                    continue
                # Jaccard相似度
                intersection = len(fp_current & fp_other)
                union = len(fp_current | fp_other)
                if union > 0:
                    similarity = intersection / union
                    similarities.append(similarity)

        if not similarities:
            return 0.0

        avg_similarity = sum(similarities) / len(similarities)

        # 平均相似度>0.6 高度重复（商用特征）
        if avg_similarity > 0.8:
            return 90.0
        elif avg_similarity > 0.6:
            return 60.0
        elif avg_similarity > 0.4:
            return 30.0
        elif avg_similarity > 0.2:
            return 10.0
        return 0.0

    # ==================== IP分布分析 ====================

    def _analyze_ip_distribution(self, client_id: str) -> float:
        """
        分析IP分布

        商用特征：单一客户端在24小时内使用大量不同IP（>10）
        正常用户：IP数量有限
        """
        # 先清理过期的IP记录
        now = time.time()
        ip_timestamps = self._ip_timestamps.get(client_id, deque())
        if ip_timestamps:
            # 只保留24小时内的IP
            recent_ips = set()
            for ts, ip in ip_timestamps:
                if now - ts < 86400:  # 24小时
                    recent_ips.add(ip)
            unique_ip_count = len(recent_ips)
        else:
            unique_ip_count = len(self._ip_history.get(client_id, set()))

        # 单一客户端使用>10个唯一IP，疑似商用
        if unique_ip_count > 20:
            return 90.0
        elif unique_ip_count > 10:
            return 60.0
        elif unique_ip_count > 5:
            return 30.0
        elif unique_ip_count > 3:
            return 10.0
        return 0.0

    # ==================== 单Key突发检测 ====================

    def _analyze_burst(self, api_key: Optional[str] = None) -> float:
        """
        分析单Key突发请求

        商用特征：单个API Key在5分钟窗口内>50个请求
        正常用户：请求频率较低且不集中
        """
        if not api_key:
            return 0.0

        timestamps = self._key_timestamps.get(api_key, deque())
        if len(timestamps) < 10:
            return 0.0

        now = time.time()
        # 5分钟窗口
        window = 300
        recent_count = sum(1 for t in timestamps if now - t < window)

        if recent_count > 100:
            return 90.0
        elif recent_count > 50:
            return 70.0
        elif recent_count > 30:
            return 40.0
        elif recent_count > 15:
            return 15.0
        return 0.0

    # ==================== 内存清理 ====================

    def _cleanup_stale_data(self):
        """
        清理超过24小时的陈旧数据

        防止内存无限增长，定期清理不再活跃的客户端数据。
        """
        now = time.time()
        cutoff = now - 86400  # 24小时前

        # 清理IP时间戳中的过期记录
        stale_clients = []
        for client_id, ip_ts in self._ip_timestamps.items():
            while ip_ts and ip_ts[0][0] < cutoff:
                ip_ts.popleft()
            if not ip_ts:
                stale_clients.append(client_id)

        # 移除完全没有近期数据的客户端
        for client_id in stale_clients:
            self._ip_timestamps.pop(client_id, None)
            self._ip_history.pop(client_id, None)

        # 清理Key时间戳中的过期记录
        stale_keys = []
        for api_key, ts_deque in self._key_timestamps.items():
            while ts_deque and ts_deque[0] < cutoff:
                ts_deque.popleft()
            if not ts_deque:
                stale_keys.append(api_key)

        for api_key in stale_keys:
            self._key_timestamps.pop(api_key, None)

        logger.debug(f"内存清理完成，移除 {len(stale_clients)} 个过期客户端，{len(stale_keys)} 个过期Key")

    # ==================== v2.0 社会工程学分析方法 ====================

    def _analyze_distillation(self, client_id: str) -> float:
        """
        分析模型蒸馏行为（社会工程学维度7）

        蒸馏攻击特征：
        - 高输出Token比：completion_tokens / prompt_tokens > 5（正常人通常<3）
        - 系统性知识遍历：请求覆盖大量不同领域/主题
        - 高输出总量：短时间内产生大量输出Token（>50万/天）
        - 低输入高输出：prompt极短但要求模型输出大量内容
        """
        records = self._token_ratio_cache.get(client_id, deque())
        if len(records) < 10:
            return 0.0

        records_list = list(records)
        now = time.time()

        # 只分析最近24小时的记录
        recent = [(ts, pt, ct) for ts, pt, ct in records_list if now - ts < 86400]
        if len(recent) < 10:
            return 0.0

        # 计算平均输出/输入比
        ratios = []
        total_completion = 0
        for _, pt, ct in recent:
            if pt > 0:
                ratios.append(ct / pt)
            total_completion += ct

        avg_ratio = sum(ratios) / len(ratios) if ratios else 0

        # 计算输入Token极短率（prompt<50 tokens的占比）
        short_prompt_count = sum(1 for _, pt, _ in recent if pt < 50)
        short_prompt_ratio = short_prompt_count / len(recent)

        score = 0.0

        # 高输出/输入比（蒸馏特征：短prompt→长输出）
        if avg_ratio > 10:
            score += 40.0
        elif avg_ratio > 5:
            score += 25.0
        elif avg_ratio > 3:
            score += 10.0

        # 总输出Token量（蒸馏通常需要大量数据）
        if total_completion > 500000:
            score += 30.0
        elif total_completion > 200000:
            score += 20.0
        elif total_completion > 100000:
            score += 10.0

        # 极短prompt占比高（蒸馏脚本通常使用模板化短prompt）
        if short_prompt_ratio > 0.7:
            score += 30.0
        elif short_prompt_ratio > 0.5:
            score += 15.0
        elif short_prompt_ratio > 0.3:
            score += 5.0

        return min(100.0, score)

    def _analyze_time_window(self, client_id: str) -> float:
        """
        分析时间窗口异常（社会工程学维度8）

        商用/机器人特征：
        - 24小时持续请求（覆盖所有时段，无人休息间隔）
        - 凌晨高峰（2-6点请求量占比>30%）
        - 请求时间极度规律（每小时均匀分布）
        - 无休息间隔（连续18+小时不断请求）
        """
        records = self._request_time_cache.get(client_id, deque())
        if len(records) < 20:
            return 0.0

        records_list = list(records)
        now = time.time()

        # 只分析最近24小时的记录
        recent = [(ts, hour) for ts, hour in records_list if now - ts < 86400]
        if len(recent) < 20:
            return 0.0

        # 统计各时段请求分布
        hour_counts = [0] * 24
        for _, hour in recent:
            hour_counts[hour] += 1

        total_requests = len(recent)

        # 计算覆盖的小时数
        active_hours = sum(1 for c in hour_counts if c > 0)

        # 凌晨时段（0-6点CST）请求占比
        midnight_requests = sum(hour_counts[0:6])
        midnight_ratio = midnight_requests / total_requests if total_requests > 0 else 0

        # 工作时段（9-22点CST）请求占比
        workday_requests = sum(hour_counts[9:22])
        workday_ratio = workday_requests / total_requests if total_requests > 0 else 0

        # 检查连续活跃时长（无2小时以上间隔）
        timestamps = sorted([ts for ts, _ in recent])
        max_gap = 0
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            max_gap = max(max_gap, gap)

        score = 0.0

        # 24小时几乎持续活跃（正常用户有休息时间）
        if active_hours >= 22:
            score += 40.0
        elif active_hours >= 18:
            score += 25.0
        elif active_hours >= 14:
            score += 10.0

        # 凌晨高活跃（正常用户凌晨基本不使用）
        if midnight_ratio > 0.4:
            score += 35.0
        elif midnight_ratio > 0.25:
            score += 20.0
        elif midnight_ratio > 0.15:
            score += 8.0

        # 无休息间隔（连续18+小时不断请求）
        if max_gap < 7200 and total_requests > 100:  # 最大间隔<2小时且请求>100
            score += 25.0
        elif max_gap < 14400 and total_requests > 50:  # 最大间隔<4小时且请求>50
            score += 10.0

        return min(100.0, score)

    def _analyze_account_farm(self, client_id: str) -> float:
        """
        分析账号农场特征（社会工程学维度9）

        账号农场特征：
        - 多个client_id共享同一IP（>3个不同账号）
        - 同IP下的账号使用模式高度同步
        - 密钥创建时间集中（短时间内批量注册）
        """
        # 获取该客户端使用的所有IP
        client_ips = self._ip_history.get(client_id, set())
        if not client_ips:
            return 0.0

        # 检查每个IP关联了多少个不同的client_id
        max_shared_clients = 0
        shared_ips = 0
        for ip in client_ips:
            clients_on_ip = self._ip_client_map.get(ip, set())
            count = len(clients_on_ip)
            if count > 1:
                shared_ips += 1
            max_shared_clients = max(max_shared_clients, count)

        # 共享IP占比
        shared_ratio = shared_ips / len(client_ips) if client_ips else 0

        score = 0.0

        # 同IP下大量不同账号（农场特征：一台服务器运行多个账号）
        if max_shared_clients > 10:
            score += 50.0
        elif max_shared_clients > 5:
            score += 35.0
        elif max_shared_clients > 3:
            score += 20.0
        elif max_shared_clients > 2:
            score += 5.0

        # 大部分IP都共享（非家庭/个人网络特征）
        if shared_ratio > 0.7:
            score += 30.0
        elif shared_ratio > 0.4:
            score += 15.0
        elif shared_ratio > 0.2:
            score += 5.0

        # 尝试从数据库检查注册时间集中度
        try:
            client_keys = fetch_all(
                "SELECT created_at FROM client_api_keys WHERE client_id = %s ORDER BY created_at",
                (client_id,),
            )
            if len(client_keys) >= 3:
                # 检查密钥创建时间是否集中在短时间内
                from datetime import datetime
                create_times = []
                for k in client_keys:
                    try:
                        create_times.append(datetime.fromisoformat(k["created_at"].replace("Z", "+00:00")).timestamp())
                    except Exception:
                        pass
                if len(create_times) >= 3:
                    time_span = max(create_times) - min(create_times)
                    # 3+个密钥在1小时内创建 = 批量注册
                    if time_span < 3600:
                        score += 20.0
                    elif time_span < 86400:
                        score += 8.0
        except Exception:
            pass

        return min(100.0, score)

    # ==================== 核心分析方法 ====================

    def analyze_client(self, client_id: str, metrics, api_key: Optional[str] = None) -> dict:
        """
        分析客户端商用行为

        参数：
        - client_id: 客户端ID
        - metrics: ClientMetrics对象（来自调度器）
        - api_key: 可选的API Key（用于突发检测）
        """
        # 管理控制开关检查
        if not self.detection_enabled:
            return {
                "client_id": client_id,
                "confidence_score": 0,
                "interval_stddev": 0,
                "interval_cv": 0,
                "model_switch_count": 0,
                "avg_concurrent": 0,
                "template_ratio": 0,
                "semantic_score": 0,
                "ip_distribution_score": 0,
                "burst_score": 0,
                "scores": {},
            }

        # 白名单检查
        if client_id in self.whitelist:
            return {
                "client_id": client_id,
                "confidence_score": 0,
                "interval_stddev": 0,
                "interval_cv": 0,
                "model_switch_count": 0,
                "avg_concurrent": 0,
                "template_ratio": 0,
                "semantic_score": 0,
                "ip_distribution_score": 0,
                "burst_score": 0,
                "scores": {},
            }

        # 定期清理陈旧数据（每10分钟）
        now = time.time()
        if now - self._last_cleanup > 600:
            self._cleanup_stale_data()
            self._last_cleanup = now

        scores = {}

        # 维度1: 请求间隔分布 (15%)
        interval_score = self._analyze_intervals(metrics.request_intervals)
        scores["interval"] = interval_score

        # 维度2: 模型切换频率 (10%)
        switch_score = self._analyze_model_switches(metrics.model_switches)
        scores["model_switch"] = switch_score

        # 维度3: 并发连接数 (10%)
        concurrent_score = self._analyze_concurrency(metrics)
        scores["concurrent"] = concurrent_score

        # 维度4: 语义相似度 (15%)
        semantic_score = self._analyze_semantic_similarity(client_id)
        scores["semantic"] = semantic_score

        # 维度5: IP分布分析 (10%)
        ip_distribution_score = self._analyze_ip_distribution(client_id)
        scores["ip_distribution"] = ip_distribution_score

        # 维度6: 单Key突发检测 (10%)
        burst_score = self._analyze_burst(api_key)
        scores["burst"] = burst_score

        # 维度7: 蒸馏行为检测 (15%) 【v2.0新增-社会工程学】
        distillation_score = self._analyze_distillation(client_id)
        scores["distillation"] = distillation_score

        # 维度8: 时间窗口异常 (5%) 【v2.0新增-社会工程学】
        time_window_score = self._analyze_time_window(client_id)
        scores["time_window"] = time_window_score

        # 维度9: 账号农场特征 (8%) 【v2.0新增-社会工程学】
        account_farm_score = self._analyze_account_farm(client_id)
        scores["account_farm"] = account_farm_score

        # 维度10: 浏览器指纹与UA异常 (12%) 【v9.2新增】
        browser_fp_score = self._analyze_browser_fingerprint(client_id)
        scores["browser_fingerprint"] = browser_fp_score

        # 维度11: 请求头模式与协议异常 (7%) 【v9.2新增】
        header_pattern_score = self._analyze_header_pattern(client_id)
        scores["header_pattern"] = header_pattern_score

        # 综合置信度（十一维加权，v10.1修复：按"有数据维度"的权重归一化）
        # 无数据的维度（如 record_content/record_headers 未接线导致恒0的维度）
        # 不参与计分，confidence = weighted_sum / sum(available_weights)，
        # 修复原先无数据维度权重恒0把数学上限压到69%的问题
        dimension_weights = {
            "interval": 0.12,
            "switch": 0.08,
            "concurrent": 0.08,
            "semantic": 0.12,
            "ip_distribution": 0.08,
            "burst": 0.08,
            "distillation": 0.12,
            "time_window": 0.05,
            "account_farm": 0.08,
            "browser_fingerprint": 0.12,
            "header_pattern": 0.07,
        }
        dimension_scores = {
            "interval": interval_score,
            "switch": switch_score,
            "concurrent": concurrent_score,
            "semantic": semantic_score,
            "ip_distribution": ip_distribution_score,
            "burst": burst_score,
            "distillation": distillation_score,
            "time_window": time_window_score,
            "account_farm": account_farm_score,
            "browser_fingerprint": browser_fp_score,
            "header_pattern": header_pattern_score,
        }
        # 各维度数据可用性（阈值与对应分析器的最小样本要求一致）
        available = {
            "interval": len(metrics.request_intervals) >= 10,
            "switch": len(metrics.model_switches) > 0,
            "concurrent": True,  # 调度器实时计数，恒有数据
            "semantic": len(self._fingerprint_cache.get(client_id, ())) >= 5,
            "ip_distribution": bool(self._ip_timestamps.get(client_id)) or bool(self._ip_history.get(client_id)),
            "burst": bool(api_key) and len(self._key_timestamps.get(api_key, ())) >= 10,
            "distillation": len(self._token_ratio_cache.get(client_id, ())) >= 10,
            "time_window": len(self._request_time_cache.get(client_id, ())) >= 20,
            "account_farm": bool(self._ip_history.get(client_id)),
            "browser_fingerprint": bool(self._ua_history.get(client_id)) or bool(self._header_history.get(client_id)),
            "header_pattern": bool(self._header_history.get(client_id)) or bool(self._http_version_history.get(client_id)),
        }
        weighted_sum = sum(dimension_scores[d] * dimension_weights[d] for d in dimension_weights if available[d])
        available_weight = sum(dimension_weights[d] for d in dimension_weights if available[d])
        confidence = int(min(100, max(0, weighted_sum / available_weight))) if available_weight > 0 else 0

        # === v9.2: 高危行为详细日志 ===
        if confidence >= 70:
            # 获取客户端名称
            client_name = ""
            try:
                row = fetch_one("SELECT name FROM clients WHERE id=%s", (client_id,))
                if row:
                    client_name = row.get("name", "")
            except Exception:
                pass

            # 找出贡献最大的维度（得分最高的3个）
            dims = [
                ("请求间隔分布", interval_score, 0.12),
                ("模型切换频率", switch_score, 0.08),
                ("并发连接数", concurrent_score, 0.08),
                ("语义相似度", semantic_score, 0.12),
                ("IP分布", ip_distribution_score, 0.08),
                ("突发请求", burst_score, 0.08),
                ("蒸馏行为", distillation_score, 0.12),
                ("时间窗口异常", time_window_score, 0.05),
                ("账号农场", account_farm_score, 0.08),
                ("浏览器指纹", browser_fp_score, 0.12),
                ("请求头异常", header_pattern_score, 0.07),
            ]
            dims.sort(key=lambda x: -x[1] * x[2])
            top_dims = dims[:3]

            risk_level = "极高" if confidence >= 90 else ("高危" if confidence >= 80 else "可疑")
            dim_detail = " | ".join(f"{name}(得分{dscore:.0f}*权重{w:.2f}={dscore*w:.1f})" for name, dscore, w in top_dims)

            logger.warning(
                f"【商业检测-{risk_level}】client={client_id[:12]} name={client_name or 'unknown'} "
                f"置信度={confidence} 维度详情: {dim_detail}"
            )

            # v10.1修复: 特殊监控用户改为环境变量 AQUA_WATCHLIST 配置（文件头有说明），默认为空
            if WATCHLIST and any(kw in (client_name or "").lower() for kw in WATCHLIST):
                logger.error(
                    f"【特殊监控-高危】client={client_id[:12]} name={client_name} "
                    f"置信度={confidence} 风险等级={risk_level} "
                    f"已记录完整维度数据待审查"
                )

        # 计算间隔统计
        intervals = list(metrics.request_intervals)
        if intervals:
            mean = sum(intervals) / len(intervals)
            variance = sum((x - mean) ** 2 for x in intervals) / len(intervals)
            stddev = math.sqrt(variance)
            cv = stddev / mean if mean > 0 else 0
        else:
            mean = 0
            stddev = 0
            cv = 0

        # 最近10分钟模型切换次数
        recent_switches = sum(1 for t, _ in metrics.model_switches if now - t < 600)

        result = {
            "client_id": client_id,
            "confidence_score": confidence,
            "interval_stddev": round(stddev, 4),
            "interval_cv": round(cv, 4),
            "model_switch_count": recent_switches,
            "avg_concurrent": round(metrics.inflight_count, 2),
            "template_ratio": round(semantic_score / 100, 4),
            "semantic_score": round(semantic_score, 2),
            "ip_distribution_score": round(ip_distribution_score, 2),
            "burst_score": round(burst_score, 2),
            "distillation_score": round(distillation_score, 2),
            "time_window_score": round(time_window_score, 2),
            "account_farm_score": round(account_farm_score, 2),
            "browser_fingerprint_score": round(browser_fp_score, 2),
            "header_pattern_score": round(header_pattern_score, 2),
            "scores": scores,
            "request_intervals": intervals,  # 传给_save_to_db用于持久化
        }

        # 持久化到数据库
        self._save_to_db(result)

        return result

    def _analyze_intervals(self, intervals: deque) -> float:
        """
        分析请求间隔分布

        商用特征：间隔非常规律（标准差小，变异系数低）
        正常用户：间隔随机（标准差大，变异系数高）
        """
        if len(intervals) < 10:
            return 0.0

        intervals_list = list(intervals)
        mean = sum(intervals_list) / len(intervals_list)
        if mean == 0:
            return 0.0

        variance = sum((x - mean) ** 2 for x in intervals_list) / len(intervals_list)
        stddev = math.sqrt(variance)
        cv = stddev / mean  # 变异系数

        # CV<0.1 高度规律（商用特征），CV>1.0 随机（正常）
        if cv < 0.1:
            return 90.0
        elif cv < 0.3:
            return 60.0
        elif cv < 0.5:
            return 30.0
        elif cv < 1.0:
            return 10.0
        return 0.0

    def _analyze_model_switches(self, switches: deque) -> float:
        """
        分析模型切换频率

        商用特征：频繁切换模型（10分钟>10次）
        正常用户：偶尔切换
        """
        now = time.time()
        recent = sum(1 for t, _ in switches if now - t < 600)

        if recent > 20:
            return 90.0
        elif recent > 10:
            return 60.0
        elif recent > 5:
            return 30.0
        elif recent > 2:
            return 10.0
        return 0.0

    def _analyze_concurrency(self, metrics) -> float:
        """
        分析并发连接数

        商用特征：持续高并发（平均>3）
        正常用户：低并发（平均<2）
        """
        avg = metrics.inflight_count

        if avg > 5:
            return 80.0
        elif avg > 3:
            return 50.0
        elif avg > 2:
            return 20.0
        return 0.0

    def _analyze_templates(self, client_id: str) -> float:
        """
        分析请求模板化程度

        商用特征：大量重复或高度相似的请求
        正常用户：请求内容多样化
        """
        contents = self._content_cache.get(client_id, deque())
        if len(contents) < 10:
            return 0.0

        contents_list = list(contents)
        # 计算重复率
        unique = len(set(contents_list))
        total = len(contents_list)
        repeat_ratio = 1 - (unique / total)

        # 重复率>30% 触发模板化标记
        if repeat_ratio > 0.5:
            return 80.0
        elif repeat_ratio > 0.3:
            return 50.0
        elif repeat_ratio > 0.1:
            return 20.0
        return 0.0

    def _save_to_db(self, result: dict):
        """保存识别结果到数据库"""
        try:
            execute(
                "INSERT INTO commercial_detection "
                "(client_id, confidence_score, interval_stddev, interval_cv, "
                "model_switch_count, avg_concurrent, template_ratio, "
                "request_intervals, last_updated, admin_confirmed, false_positive) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "COALESCE((SELECT admin_confirmed FROM commercial_detection WHERE client_id=%s), 0), "
                "COALESCE((SELECT false_positive FROM commercial_detection WHERE client_id=%s), 0)) "
                "ON CONFLICT (client_id) DO UPDATE SET "
                "confidence_score = EXCLUDED.confidence_score, "
                "interval_stddev = EXCLUDED.interval_stddev, "
                "interval_cv = EXCLUDED.interval_cv, "
                "model_switch_count = EXCLUDED.model_switch_count, "
                "avg_concurrent = EXCLUDED.avg_concurrent, "
                "template_ratio = EXCLUDED.template_ratio, "
                "request_intervals = EXCLUDED.request_intervals, "
                "last_updated = EXCLUDED.last_updated",
                (
                    result["client_id"],
                    result["confidence_score"],
                    result["interval_stddev"],
                    result["interval_cv"],
                    result["model_switch_count"],
                    result["avg_concurrent"],
                    result["template_ratio"],
                    json.dumps(result.get("request_intervals", [])),
                    utcnow(),
                    result["client_id"],
                    result["client_id"],
                ),
            )
        except Exception as e:
            logger.error(f"保存商用识别结果失败: {e}")

    def get_all_detections(self) -> list:
        """获取所有客户端的商用识别结果"""
        return fetch_all(
            "SELECT cd.*, c.name as client_name "
            "FROM commercial_detection cd "
            "LEFT JOIN clients c ON cd.client_id = c.id "
            "ORDER BY cd.confidence_score DESC"
        )

    def update_detection(self, client_id: str, admin_confirmed: bool, false_positive: bool):
        """管理员更新商用标记"""
        execute(
            "UPDATE commercial_detection SET admin_confirmed = %s, false_positive = %s, last_updated = %s "
            "WHERE client_id = %s",
            (1 if admin_confirmed else 0, 1 if false_positive else 0, utcnow(), client_id),
        )
        # 如果管理员确认为误报，加入白名单
        if false_positive:
            self.whitelist.add(client_id)
            # 清除限速
            self._rate_limits.pop(client_id, None)

    # ==================== v2.0 限速管控 ====================

    def check_rate_limit(self, client_id: str) -> Optional[dict]:
        """
        检查客户端是否需要限速

        返回：
        - None: 不限速（正常用户）
        - dict: 限速信息 {"rate_limit": RPM上限, "reason": 限速原因, "until": 限速截止时间}
        """
        if not self.detection_enabled:
            return None

        # 白名单用户不限速
        if client_id in self.whitelist:
            return None

        # 检查已有的限速状态
        limit_info = self._rate_limits.get(client_id)
        if limit_info:
            now = time.time()
            if now < limit_info.get("until", 0):
                return limit_info
            else:
                # 限速到期，移除
                self._rate_limits.pop(client_id, None)

        # 从数据库获取最新的置信度
        try:
            row = fetch_one(
                "SELECT confidence_score, admin_confirmed FROM commercial_detection WHERE client_id = %s",
                (client_id,),
            )
            if not row:
                return None

            confidence = row["confidence_score"] or 0
            admin_confirmed = row["admin_confirmed"] or 0

            # 管理员确认的商用用户：严格限速
            if admin_confirmed and confidence >= 61:
                limit_info = {
                    "rate_limit": 5,  # 5 RPM 严格限速
                    "reason": f"管理员确认商用(置信度{confidence})",
                    "until": time.time() + 86400,  # 24小时限速
                }
                self._rate_limits[client_id] = limit_info
                return limit_info

            # 高危疑似商用（置信度>=80）：严格限速，等待人工审核
            if confidence >= 80:
                limit_info = {
                    "rate_limit": 3,  # 3 RPM 极严格限速
                    "reason": f"高危疑似商用(置信度{confidence})",
                    "until": time.time() + 86400,
                }
                self._rate_limits[client_id] = limit_info
                logger.warning(f"商用检测触发严格限速: client={client_id[:8]} confidence={confidence} rate=3RPM")
                return limit_info

            # 可疑用户（置信度61-79）：轻度限速
            if confidence >= 61:
                limit_info = {
                    "rate_limit": 10,  # 10 RPM 轻度限速
                    "reason": f"疑似商用(置信度{confidence})",
                    "until": time.time() + 43200,  # 12小时限速
                }
                self._rate_limits[client_id] = limit_info
                logger.info(f"商用检测触发轻度限速: client={client_id[:8]} confidence={confidence} rate=10RPM")
                return limit_info

        except Exception as e:
            logger.error(f"检查商用限速失败: {e}")

        return None

    def block_client(self, client_id: str, reason: str = "商用行为封禁"):
        """
        封禁客户端（拉黑账户与网关密钥）

        需要管理员手动调用，不会自动触发
        """
        try:
            # 禁用客户端
            execute("UPDATE clients SET status = 'blocked' WHERE id = %s", (client_id,))
            # 禁用该客户端的所有密钥
            execute("UPDATE client_api_keys SET status = 'revoked' WHERE client_id = %s", (client_id,))
            # 更新商用检测记录
            execute(
                "UPDATE commercial_detection SET admin_confirmed = 1, last_updated = %s WHERE client_id = %s",
                (utcnow(), client_id),
            )
            # 设置极端限速
            self._rate_limits[client_id] = {
                "rate_limit": 0,  # 0 RPM = 完全封禁
                "reason": reason,
                "until": time.time() + 86400 * 365,  # 1年
            }
            insert_audit("block", "client", client_id, reason)
            logger.warning(f"客户端已封禁: client={client_id[:8]} reason={reason}")
        except Exception as e:
            logger.error(f"封禁客户端失败: {e}")

    def unblock_client(self, client_id: str):
        """解封客户端"""
        try:
            execute("UPDATE clients SET status = 'active' WHERE id = %s", (client_id,))
            execute("UPDATE client_api_keys SET status = 'active' WHERE client_id = %s", (client_id,))
            self._rate_limits.pop(client_id, None)
            self.whitelist.add(client_id)
            insert_audit("unblock", "client", client_id, "解封客户端")
            logger.info(f"客户端已解封: client={client_id[:8]}")
        except Exception as e:
            logger.error(f"解封客户端失败: {e}")

    def run_periodic_analysis(self) -> list:
        """
        对所有有活跃请求记录的客户端运行商用检测分析。
        由后台周期任务每5分钟调用一次，确保检测持续运行。

        返回:
            list: 所有分析结果列表
        """
        try:
            from app.scheduler import get_scheduler
            scheduler = get_scheduler()
            results = []
            for client_id in list(scheduler._client_metrics.keys()):
                metrics = scheduler.get_client_metrics(client_id)
                if metrics.daily_count > 0 or metrics.inflight_count > 0:
                    result = self.analyze_client(client_id, metrics)
                    results.append(result)
            if results:
                logger.info(f"商用检测周期分析完成: 分析了 {len(results)} 个活跃客户端")
            return results
        except Exception as e:
            logger.error(f"商用检测周期分析失败: {e}")
            return []


# 全局实例
_detector: Optional[CommercialDetector] = None


def get_detector() -> CommercialDetector:
    global _detector
    if _detector is None:
        _detector = CommercialDetector()
    return _detector
