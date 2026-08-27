# 注意：本模块当前未接入主链路（v10 快照），修复保留待未来接线
"""
Anthropic ↔ OpenAI 协议转换器 - v9.0

借鉴 LiteLLM 和 Metapi 的协议转换设计
将 Anthropic Messages API 格式转换为 OpenAI Chat Completions 格式
"""
import json
import time
import logging
from typing import List, Dict, Optional, AsyncGenerator, Tuple

logger = logging.getLogger("acu.transformers.anthropic")


class AnthropicTransformer:
    """
    Anthropic Messages ↔ OpenAI Chat Completions 协议转换

    支持双向转换：
    - OpenAI → Anthropic（路由到Claude时）
    - Anthropic → OpenAI（路由到其他平台时）
    """

    @staticmethod
    def openai_to_anthropic(body: dict) -> Tuple[dict, dict]:
        """
        将 OpenAI Chat Completions 请求转换为 Anthropic Messages 格式

        映射关系：
        - OpenAI system → Anthropic system (top-level)
        - OpenAI user → Anthropic user
        - OpenAI assistant → Anthropic assistant
        - OpenAI tool → Anthropic tool_use/tool_result

        返回: (anthropic_body, metadata)
        """
        anthropic = {}
        metadata = {"original_model": body.get("model", "")}

        # 提取system消息
        # 修复：多条 system 消息原先后者覆盖前者，改为逐条合并
        system_parts = []
        messages = body.get("messages", [])
        remaining_messages = []

        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                # Anthropic system 是顶层字段
                content = msg.get("content", "")
                if isinstance(content, list):
                    part = " ".join(
                        c.get("text", "") for c in content if c.get("type") == "text"
                    )
                else:
                    part = content
                if part:
                    system_parts.append(str(part))
            else:
                remaining_messages.append(msg)

        if system_parts:
            anthropic["system"] = "\n".join(system_parts)

        # 转换消息
        anthropic_messages = []
        for msg in remaining_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            tool_call_id = msg.get("tool_call_id", "")

            if role == "user":
                if isinstance(content, list):
                    # 多模态内容
                    anthropic_content = []
                    for c in content:
                        if c.get("type") == "text":
                            anthropic_content.append({"type": "text", "text": c.get("text", "")})
                        elif c.get("type") == "image_url":
                            image_url = c.get("image_url", {}).get("url", "")
                            # 修复：media_type 不再写死 image/jpeg，从 data URL 前缀解析
                            media_type = "image/jpeg"
                            base64_data = image_url
                            if image_url.startswith("data:") and "," in image_url:
                                meta, base64_data = image_url.split(",", 1)
                                # 形如 "data:image/png;base64" → image/png
                                if ";" in meta:
                                    media_type = meta[5:].split(";", 1)[0] or media_type
                            anthropic_content.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_data,
                                }
                            })
                    anthropic_messages.append({
                        "role": "user",
                        "content": anthropic_content,
                    })
                else:
                    anthropic_messages.append({"role": "user", "content": str(content)})

            elif role == "assistant":
                if tool_calls:
                    content_blocks = []
                    if content:
                        content_blocks.append({"type": "text", "text": str(content)})
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        args_raw = func.get("arguments", "{}")
                        # 修复：arguments 畸形 JSON 时直接抛异常，改为兜底回退原字符串
                        try:
                            tool_input = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except json.JSONDecodeError:
                            logger.warning(f"tool_call arguments 非法JSON，回退原字符串: {args_raw[:200]}")
                            tool_input = args_raw
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": tool_input,
                        })
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                else:
                    anthropic_messages.append({"role": "assistant", "content": str(content) if content else ""})

            elif role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": str(content) if content else "",
                    }]
                })

        anthropic["messages"] = anthropic_messages

        # 参数映射
        anthropic["model"] = body.get("model", "claude-3-5-sonnet-20241022")
        anthropic["max_tokens"] = body.get("max_tokens", 8192)
        if "temperature" in body:
            anthropic["temperature"] = body["temperature"]
        if "top_p" in body:
            anthropic["top_p"] = body["top_p"]
        if "stream" in body:
            anthropic["stream"] = body["stream"]

        # 工具映射
        tools = body.get("tools", [])
        if tools:
            anthropic_tools = []
            for t in tools:
                func = t.get("function", t)
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            anthropic["tools"] = anthropic_tools

        return anthropic, metadata

    @staticmethod
    def anthropic_to_openai(body: dict) -> dict:
        """
        将 Anthropic Messages 请求转换为 OpenAI Chat Completions 格式
        """
        openai_body = {
            "model": body.get("model", "claude-3-5-sonnet-20241022"),
            "messages": [],
        }

        # 处理system
        system = body.get("system", "")
        messages = body.get("messages", [])

        openai_messages = []
        if system:
            # 修复：system 为 Anthropic content block 数组时 join 成字符串，
            # 不能把数组原样传给 OpenAI（其 system content 只接受字符串）
            if isinstance(system, list):
                system = "\n".join(
                    b.get("text", "") for b in system
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            openai_messages.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, list):
                    # 转换 Anthropic 多模态内容到 OpenAI 格式
                    oa_content = []
                    for c in content:
                        if c.get("type") == "text":
                            oa_content.append({"type": "text", "text": c.get("text", "")})
                        elif c.get("type") == "image":
                            src = c.get("source", {})
                            oa_content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"
                                }
                            })
                        elif c.get("type") == "tool_result":
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": c.get("tool_use_id", ""),
                                "content": c.get("content", ""),
                            })
                            continue
                    # 修复：tool_result-only 的 user 消息（oa_content 为空）不追加空 content 消息
                    if oa_content:
                        openai_messages.append({"role": "user", "content": oa_content})
                else:
                    openai_messages.append({"role": "user", "content": str(content)})

            elif role == "assistant":
                if isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for c in content:
                        if c.get("type") == "text":
                            text_parts.append(c.get("text", ""))
                        elif c.get("type") == "tool_use":
                            tool_calls.append({
                                "id": c.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": c.get("name", ""),
                                    "arguments": json.dumps(c.get("input", {})),
                                }
                            })
                    msg_entry = {"role": "assistant", "content": "".join(text_parts)}
                    if tool_calls:
                        msg_entry["tool_calls"] = tool_calls
                    openai_messages.append(msg_entry)
                else:
                    openai_messages.append({"role": "assistant", "content": str(content) if content else ""})

        openai_body["messages"] = openai_messages

        # 参数映射
        if "max_tokens" in body:
            openai_body["max_tokens"] = body["max_tokens"]
        if "temperature" in body:
            openai_body["temperature"] = body["temperature"]
        if "stream" in body:
            openai_body["stream"] = body["stream"]

        return openai_body

    @staticmethod
    def stream_chunk_anthropic_to_openai(chunk: str) -> str:
        """
        将 Anthropic 流式 chunk 转换为 OpenAI SSE 格式
        """
        try:
            data = json.loads(chunk)
            event_type = data.get("type", "")

            if event_type == "message_start":
                msg = data.get("message", {})
                return f"data: {json.dumps({'id': msg.get('id', ''), 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    return f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]})}\n\n"

            elif event_type == "message_delta":
                delta = data.get("delta", {})
                stop_reason = delta.get("stop_reason", "")
                usage = data.get("usage", {})
                finish = "stop" if stop_reason == "end_turn" else None
                chunk_data = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
                if usage:
                    chunk_data["usage"] = {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                    }
                return f"data: {json.dumps(chunk_data)}\n\n"

            elif event_type == "message_stop":
                return "data: [DONE]\n\n"

        except json.JSONDecodeError:
            pass

        return ""
