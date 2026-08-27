"""Platform端模型兼容层 - 模型能力标签/排序优先级

网关目录由网关侧富化（gateway/app/nim_models.py 仅在网关进程内可用），平台侧仅关键词粗判。
历史上的 sys.path hack（把 gateway 目录插进搜索路径再 import app.nim_models）从未生效：
平台自身就是 app 包，import 永远解析回平台目录，故已删除；
能力标签/排序的精确数据请从网关 API 获取，本模块恒走 fallback 路径。
"""

# 平台侧无网关模型目录：目录相关能力恒为“不可用”，调用方（routes/chat.py）落到关键词粗判
_HAS_NIM_MODELS = False
NIM_MODEL_CATALOG = {}


def get_model_sort_priority(*_args, **_kwargs) -> dict:
    """兼容占位：平台侧无目录，恒返回空映射"""
    return {}


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
