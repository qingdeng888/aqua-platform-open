"""
NVIDIA NIM 模型目录（仅包含实测可用模型）

基于实时 /v1/models + chat/completions 探活（2026-08-27）筛选，
仅保留可通过 chat/completions 正常调用的 Live 模型，剔除 EOL/404 旧 ID。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class NIMModelInfo:
    """NVIDIA NIM 模型信息"""
    model_id: str
    display_name: str
    publisher: str
    context_length: int
    max_output_tokens: int
    supports_streaming: bool
    supports_tools: bool
    supports_images: bool
    model_family: str
    description: str = ""
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


# NVIDIA NIM 实测可用模型目录 (chat/completions 探活通过)
NIM_MODEL_CATALOG: Dict[str, NIMModelInfo] = {
    'deepseek-ai/deepseek-v4-pro-0813': NIMModelInfo(
        model_id='deepseek-ai/deepseek-v4-pro-0813',
        display_name='DeepSeek V4 Pro',
        publisher='deepseek-ai',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='deepseek',
        description='Live NIM model: deepseek-ai/deepseek-v4-pro-0813',
        tags=[],
    ),

    'google/diffusiongemma-26b-a4b-it': NIMModelInfo(
        model_id='google/diffusiongemma-26b-a4b-it',
        display_name='DiffusionGemma-26B',
        publisher='google',
        context_length=8192,
        max_output_tokens=4096,
        supports_streaming=True,
        supports_tools=False,
        supports_images=False,
        model_family='gemma',
        description='Diffusion-based 26B parameter LLM enabling parallel token generation.',
        tags=['diffusion-llm'],
    ),

    'google/gemma-4-31b-it': NIMModelInfo(
        model_id='google/gemma-4-31b-it',
        display_name='Gemma-4-31B',
        publisher='google',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='gemma',
        description='Dense 31B model delivering frontier reasoning for coding, agentic workflows.',
        tags=['reasoning'],
    ),

    'meta/llama-3.2-11b-vision-instruct': NIMModelInfo(
        model_id='meta/llama-3.2-11b-vision-instruct',
        display_name='Llama-3.2-11B-Vision',
        publisher='meta',
        context_length=131072,
        max_output_tokens=8192,
        supports_streaming=True,
        supports_tools=True,
        supports_images=True,
        model_family='llama',
        description='Cutting-edge vision-language model excelling in high-quality reasoning from images.',
        tags=['image-text-retrieval'],
    ),

    'meta/llama-3.2-90b-vision-instruct': NIMModelInfo(
        model_id='meta/llama-3.2-90b-vision-instruct',
        display_name='Llama-3.2-90B-Vision',
        publisher='meta',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=True,
        model_family='llama',
        description='Cutting-edge vision-language model excelling in high-quality reasoning from images.',
        tags=['image-text-retrieval'],
    ),

    'meta/muse-glimmer-30b': NIMModelInfo(
        model_id='meta/muse-glimmer-30b',
        display_name='Muse Glimmer 30B',
        publisher='meta',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='meta',
        description='Live NIM model: meta/muse-glimmer-30b',
        tags=[],
    ),

    'minimaxai/minimax-m3': NIMModelInfo(
        model_id='minimaxai/minimax-m3',
        display_name='MiniMax-M3',
        publisher='minimaxai',
        context_length=1048576,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=True,
        model_family='minimaxai',
        description='Multimodal MoE vision-language model with strong reasoning, coding, and tool-calling.',
        tags=['coding'],
    ),

    'moonshotai/kimi-k3': NIMModelInfo(
        model_id='moonshotai/kimi-k3',
        display_name='Kimi K3',
        publisher='moonshotai',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='moonshotai',
        description='Live NIM model: moonshotai/kimi-k3',
        tags=[],
    ),

    'nvidia/ising-calibration-1.5-31b': NIMModelInfo(
        model_id='nvidia/ising-calibration-1.5-31b',
        display_name='Ising Calibration 1.5 31B',
        publisher='nvidia',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='nvidia',
        description='Live NIM model: nvidia/ising-calibration-1.5-31b',
        tags=[],
    ),

    'nvidia/llama-3.1-nemoguard-8b-content-safety': NIMModelInfo(
        model_id='nvidia/llama-3.1-nemoguard-8b-content-safety',
        display_name='Llama-3.1-NemoGuard-8B-Content-Safety',
        publisher='nvidia',
        context_length=131072,
        max_output_tokens=4096,
        supports_streaming=True,
        supports_tools=False,
        supports_images=False,
        model_family='llama',
        description='Content safety guard model based on Llama 3.1 8B for detecting unsafe content.',
        tags=['safety'],
    ),

    'nvidia/llama-3.1-nemotron-safety-guard-8b-v3': NIMModelInfo(
        model_id='nvidia/llama-3.1-nemotron-safety-guard-8b-v3',
        display_name='Llama-3.1-Nemotron-Safety-Guard-8B-v3',
        publisher='nvidia',
        context_length=131072,
        max_output_tokens=4096,
        supports_streaming=True,
        supports_tools=False,
        supports_images=False,
        model_family='llama',
        description='Safety guard model based on Llama 3.1 Nemotron 8B v3 for content moderation.',
        tags=['safety'],
    ),

    'nvidia/nemotron-3-nano-30b-a3b': NIMModelInfo(
        model_id='nvidia/nemotron-3-nano-30b-a3b',
        display_name='Nemotron-3-Nano-30B',
        publisher='nvidia',
        context_length=1048576,
        max_output_tokens=32768,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='nemotron',
        description='Open efficient MoE model with 1M context, excelling in coding, reasoning, instruction following, tool calling.',
        tags=['moe'],
    ),

    'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning': NIMModelInfo(
        model_id='nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',
        display_name='Nemotron-3-Nano-Omni-30B-Reasoning',
        publisher='nvidia',
        context_length=256000,
        max_output_tokens=20480,
        supports_streaming=True,
        supports_tools=True,
        supports_images=True,
        model_family='nemotron',
        description='Omni-modal reasoning model that understands images, video, speech, text.',
        tags=['image-to-text', 'reasoning'],
    ),

    'nvidia/nemotron-3-super-120b-a12b': NIMModelInfo(
        model_id='nvidia/nemotron-3-super-120b-a12b',
        display_name='Nemotron-3-Super-120B',
        publisher='nvidia',
        context_length=1048576,
        max_output_tokens=32768,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='nemotron',
        description='Hybrid Mamba-Transformer MoE with Multi-Token Prediction, 1M context.',
        tags=['moe'],
    ),

    'nvidia/nemotron-3.5-content-safety': NIMModelInfo(
        model_id='nvidia/nemotron-3.5-content-safety',
        display_name='Nemotron-3.5-Content-Safety',
        publisher='nvidia',
        context_length=131072,
        max_output_tokens=4096,
        supports_streaming=True,
        supports_tools=False,
        supports_images=True,
        model_family='nemotron',
        description='Multilingual multimodal model for detecting unsafe and toxic content.',
        tags=['llm-safety'],
    ),

    'nvidia/nemotron-3.5-lightning-30b-a3b': NIMModelInfo(
        model_id='nvidia/nemotron-3.5-lightning-30b-a3b',
        display_name='Nemotron 3.5 Lightning 30B A3B',
        publisher='nvidia',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='nemotron',
        description='Live NIM model: nvidia/nemotron-3.5-lightning-30b-a3b',
        tags=[],
    ),

    'nvidia/riva-translate-4b-instruct-v1.1': NIMModelInfo(
        model_id='nvidia/riva-translate-4b-instruct-v1.1',
        display_name='Riva-Translate-4B-v1.1',
        publisher='nvidia',
        context_length=4096,
        max_output_tokens=4096,
        supports_streaming=True,
        supports_tools=False,
        supports_images=False,
        model_family='riva',
        description='Riva Translate 4B instruct v1.1 model for translation tasks.',
        tags=['translation'],
    ),

    'nvidia/riva-translate-4b-instruct-v2': NIMModelInfo(
        model_id='nvidia/riva-translate-4b-instruct-v2',
        display_name='Riva Translate 4B Instruct',
        publisher='nvidia',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='riva',
        description='Live NIM model: nvidia/riva-translate-4b-instruct-v2',
        tags=[],
    ),

    'openai/gpt-oss-120b': NIMModelInfo(
        model_id='openai/gpt-oss-120b',
        display_name='GPT-OSS-120B',
        publisher='openai',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=False,
        supports_images=False,
        model_family='openai',
        description='MoE reasoning LLM (text-only) designed to fit within 80GB GPU.',
        tags=['reasoning'],
    ),

    'openai/gpt-oss-20b': NIMModelInfo(
        model_id='openai/gpt-oss-20b',
        display_name='GPT-OSS-20B',
        publisher='openai',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=False,
        supports_images=False,
        model_family='openai',
        description='Smaller MoE text-only LLM for efficient AI reasoning and math.',
        tags=['reasoning'],
    ),

    'poolside/laguna-xs-2.1': NIMModelInfo(
        model_id='poolside/laguna-xs-2.1',
        display_name='Laguna-XS-2.1',
        publisher='poolside',
        context_length=32768,
        max_output_tokens=8192,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='poolside',
        description='Poolside Laguna XS 2.1 code model for software engineering tasks.',
        tags=['coding'],
    ),

    'stepfun-ai/step-3.7-flash': NIMModelInfo(
        model_id='stepfun-ai/step-3.7-flash',
        display_name='Step-3.7-Flash',
        publisher='stepfun-ai',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=True,
        model_family='stepfun-ai',
        description='Sparse MoE multimodal reasoning model for enterprise, agentic and coding.',
        tags=['coding'],
    ),

    'mistralai/mistral-nemotron': NIMModelInfo(
        model_id='mistralai/mistral-nemotron',
        display_name='Mistral-Nemotron',
        publisher='mistralai',
        context_length=131072,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='nemotron',
        description='Built for agentic workflows, excels in coding, instruction following, and function calling.',
        tags=['language-generation'],
    ),

    'nvidia/nemotron-3-ultra-550b-a55b': NIMModelInfo(
        model_id='nvidia/nemotron-3-ultra-550b-a55b',
        display_name='Nemotron-3-Ultra-550B',
        publisher='nvidia',
        context_length=1048576,
        max_output_tokens=32768,
        supports_streaming=True,
        supports_tools=True,
        supports_images=False,
        model_family='nemotron',
        description='Hybrid Mamba-Transformer MoE with 1M context, agentic reasoning, coding, planning, tool calling.',
        tags=['agent'],
    ),

}


def get_model_info(model_id: str) -> Optional[NIMModelInfo]:
    """获取模型信息"""
    return NIM_MODEL_CATALOG.get(model_id)


def list_models(publisher: str = None, family: str = None, supports_tools: bool = None) -> List[NIMModelInfo]:
    """列出可用模型"""
    models = list(NIM_MODEL_CATALOG.values())
    if publisher:
        models = [m for m in models if m.publisher == publisher]
    if family:
        models = [m for m in models if m.model_family == family]
    if supports_tools is not None:
        models = [m for m in models if m.supports_tools == supports_tools]
    return models


def get_openai_models_list() -> list:
    """生成 OpenAI /v1/models 兼容格式的模型列表"""
    return [
        {
            "id": model.model_id,
            "object": "model",
            "created": 1700000000,
            "owned_by": model.publisher,
            "permission": [],
        }
        for model in NIM_MODEL_CATALOG.values()
    ]


def get_model_sort_priority() -> dict:
    """模型排序优先级 (用于前端展示排序, 仅包含实测可用的热门模型)"""
    order = [
        "deepseek-ai/deepseek-v4-pro-0813",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-super-120b-a12b",
        "minimaxai/minimax-m3",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "moonshotai/kimi-k3",
        "mistralai/mistral-nemotron",
        "stepfun-ai/step-3.7-flash",
        "google/gemma-4-31b-it",
        "meta/llama-3.2-90b-vision-instruct",
        "meta/llama-3.2-11b-vision-instruct",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/riva-translate-4b-instruct-v1.1",
        "nvidia/riva-translate-4b-instruct-v2",
        "google/diffusiongemma-26b-a4b-it",
        "meta/muse-glimmer-30b",
        "poolside/laguna-xs-2.1",
        "nvidia/ising-calibration-1.5-31b",
        "nvidia/llama-3.1-nemoguard-8b-content-safety",
        "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
        "nvidia/nemotron-3.5-content-safety",
    ]
    return {m: i for i, m in enumerate(order)}
