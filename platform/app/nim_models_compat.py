"""Platform端模型兼容层 - 从Gateway的NIM模型目录获取模型能力标签和排序优先级"""

import sys
import os

# 将gateway目录加入sys.path以导入nim_models
_gw_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "gateway")
if _gw_path not in sys.path:
    sys.path.insert(0, _gw_path)

try:
    from app.nim_models import NIM_MODEL_CATALOG, get_model_sort_priority
    _HAS_NIM_MODELS = True
except ImportError:
    _HAS_NIM_MODELS = False
    NIM_MODEL_CATALOG = {}
    get_model_sort_priority = lambda *a, **kw: {}


# 能力标签映射（基于NIM_MODEL_CATALOG中的字段）
_CAPABILITY_MAP = {
    "supports_tools": "工具调用",
    "supports_images": "视觉",
    "supports_streaming": "流式",
}


def get_model_capabilities(model_id: str) -> list:
    """从NIM模型目录获取能力标签"""
    if not _HAS_NIM_MODELS:
        return []

    info = NIM_MODEL_CATALOG.get(model_id)
    if not info:
        return []

    caps = []

    # 基于模型描述和标签推断能力
    tags = info.tags or []
    desc_lower = (info.description or "").lower()
    tag_lower = [t.lower() for t in tags]

    # 推理
    if any(kw in tag_lower for kw in ["reasoning", "agent", "advanced reasoning"]):
        caps.append("推理")
    elif info.model_family in ("deepseek", "glm", "qwen", "kimi", "nemotron", "gpt-oss", "step"):
        caps.append("推理")

    # 视觉
    if info.supports_images:
        caps.append("视觉")
    elif any(kw in tag_lower for kw in ["multimodal", "vision", "image", "video"]):
        caps.append("视觉")

    # 工具调用
    if info.supports_tools:
        caps.append("工具调用")
    elif any(kw in tag_lower for kw in ["tool-use", "tool calling", "agent"]):
        caps.append("工具调用")

    # 代码
    if any(kw in tag_lower for kw in ["coding", "code generation", "code"]):
        if "代码" not in caps:
            caps.append("代码")

    # 嵌入
    if info.model_family in ("embed",) or any(kw in tag_lower for kw in ["embedding", "embed"]):
        caps.append("嵌入")

    # 安全
    if any(kw in tag_lower for kw in ["safety", "guard", "guardrails"]):
        caps.append("安全")

    # 代码
    if any(kw in tag_lower for kw in ["code generation", "coding"]):
        if "代码" not in caps:
            caps.append("代码")

    # OCR
    if any(kw in tag_lower for kw in ["ocr", "table extraction", "doc intelligence"]):
        caps.append("OCR")

    # 语音
    if any(kw in tag_lower for kw in ["asr", "tts", "speech", "voice"]):
        caps.append("语音")

    # 翻译
    if any(kw in tag_lower for kw in ["translation", "translate", "nmt"]):
        caps.append("翻译")

    # Deprecated
    if "deprecated" in tag_lower:
        caps.append("已弃用")

    # 1M上下文
    if info.context_length >= 1000000:
        caps.append("1M上下文")

    return caps


def get_model_sort_priority_compat(model_id: str) -> int | None:
    """从NIM模型目录获取排序优先级"""
    if not _HAS_NIM_MODELS:
        return None
    priorities = get_model_sort_priority()
    return priorities.get(model_id)
