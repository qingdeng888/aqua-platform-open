"""
行为检测模块 v9.2

替代传统人机验证，通过分析用户行为判断是否为真人。
仅对明显非人类行为进行管控。
"""
import time
import logging
from typing import Optional

logger = logging.getLogger("acu.behavior")

# 行为评分阈值（0-100，越低越像机器人）
_BOT_THRESHOLD = 15  # 低于此值判定为机器人
_SUSPICIOUS_THRESHOLD = 40  # 低于此值标记为可疑


def analyze_behavior(behavior_data: Optional[dict]) -> dict:
    """
    分析用户行为数据，返回评分和判定结果

    参数:
        behavior_data: {
            "mouse_moves": int,       # 鼠标移动次数
            "clicks": int,            # 点击次数
            "scrolls": int,           # 滚动次数
            "time_on_page_ms": int,   # 页面停留时间(毫秒)
            "keyboard_events": int,   # 键盘事件次数
            "touch_events": int,      # 触屏事件次数(移动端)
            "page_visibility": int,   # 页面可见性变化次数
            "viewport_width": int,    # 视口宽度
            "viewport_height": int,   # 视口高度
        }

    返回:
        {
            "score": int,        # 人类相似度评分 0-100
            "is_bot": bool,      # 是否判定为机器人
            "is_suspicious": bool, # 是否可疑
            "reason": str,       # 判定原因
        }
    """
    if not behavior_data:
        return {
            "score": 50,
            "is_bot": False,
            "is_suspicious": False,
            "reason": "无行为数据，默认放行",
        }

    score = 50  # 初始中性分
    reasons = []

    # 1. 鼠标移动检测（真人必有鼠标移动，除非移动端）
    mouse_moves = behavior_data.get("mouse_moves", 0)
    touch_events = behavior_data.get("touch_events", 0)
    viewport_width = behavior_data.get("viewport_width", 0)

    is_mobile = viewport_width < 768 or touch_events > 0

    if not is_mobile and mouse_moves == 0:
        score -= 30
        reasons.append("桌面端无鼠标移动")
    elif mouse_moves > 5:
        score += 15
    elif mouse_moves > 0:
        score += 5

    # 2. 页面停留时间（机器人通常瞬间提交）
    time_on_page = behavior_data.get("time_on_page_ms", 0)
    if time_on_page < 500:  # 小于500ms
        score -= 40
        reasons.append("页面停留时间过短")
    elif time_on_page < 2000:
        score -= 15
        reasons.append("页面停留时间偏短")
    elif time_on_page > 5000:
        score += 10  # 停留足够长，加分

    # 3. 点击/触屏事件
    clicks = behavior_data.get("clicks", 0)
    if clicks == 0 and not is_mobile:
        score -= 10
    elif clicks > 0:
        score += 5

    # 4. 滚动行为
    scrolls = behavior_data.get("scrolls", 0)
    if scrolls > 2:
        score += 10
    elif scrolls > 0:
        score += 3

    # 5. 键盘输入
    keyboard = behavior_data.get("keyboard_events", 0)
    if keyboard > 3:
        score += 10
    elif keyboard > 0:
        score += 5

    # 6. 页面可见性（真人切换标签页是正常的）
    visibility = behavior_data.get("page_visibility", 0)
    if visibility > 0:
        score += 5

    # 边界限制
    score = max(0, min(100, score))

    # 判定
    is_bot = score <= _BOT_THRESHOLD
    is_suspicious = score <= _SUSPICIOUS_THRESHOLD and not is_bot

    if is_bot:
        reason = "、".join(reasons) if reasons else "行为评分过低"
        logger.warning(f"行为检测: 判定为机器人 score={score} reasons={reasons}")
    elif is_suspicious:
        reason = "、".join(reasons) if reasons else "行为略异常"
        logger.info(f"行为检测: 标记为可疑 score={score} reasons={reasons}")
    else:
        reason = "行为正常"
        logger.debug(f"行为检测: 通过 score={score}")

    return {
        "score": score,
        "is_bot": is_bot,
        "is_suspicious": is_suspicious,
        "reason": reason,
    }
