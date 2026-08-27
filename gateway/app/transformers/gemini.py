# 注意：本模块当前未接入主链路（v10 快照），修复保留待未来接线
"""
Google Gemini ↔ OpenAI 协议转换器 - v9.0

借鉴 LiteLLM 的协议转换设计
将 Gemini GenerateContent API 格式与 OpenAI Chat Completions 格式互转
"""
import json
import time
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("acu.transformers.gemini")


class GeminiTransformer:
    """
    Gemini GenerateContent ↔ OpenAI Chat Completions 协议转换
    """

    @staticmethod
    def openai_to_gemini(body: dict) -> Tuple[dict, dict]:
        """
        OpenAI Chat → Gemini GenerateContent 格式转换

        映射关系：
        - system → system_instruction
        - user → user (contents)
        - assistant → model (contents)
        - tools → tools (function_declarations)
        """
        gemini = {}
        metadata = {"original_model": body.get("model", "")}

        # 提取system
        system_content = None
        messages = body.get("messages", [])
        remaining_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                system_content = content if isinstance(content, str) else json.dumps(content)
            else:
                remaining_messages.append(msg)

        if system_content:
            gemini["system_instruction"] = {
                "parts": [{"text": system_content}]
            }

        # 转换消息
        contents = []
        # 修复：Gemini 要求 functionResponse.name 与 functionCall.name 一致，
        # 不能用 tool_call_id 充当 name —— 维护 tool_call_id → 函数名 的映射回填
        tool_id_to_name: Dict[str, str] = {}
        for msg in remaining_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            gemini_role = "user" if role == "user" or role == "tool" else "model"

            # 处理内容
            if isinstance(content, list):
                parts = []
                for c in content:
                    if c.get("type") == "text":
                        parts.append({"text": c.get("text", "")})
                    elif c.get("type") == "image_url":
                        url = c.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            import re
                            match = re.match(r'data:(image/\w+);base64,(.+)', url)
                            if match:
                                parts.append({
                                    "inline_data": {
                                        "mime_type": match.group(1),
                                        "data": match.group(2),
                                    }
                                })
                        else:
                            parts.append({"text": f"[Image: {url}]"})
            else:
                parts = [{"text": str(content)}]

            # 处理工具调用（assistant角色）
            if role == "assistant" and tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    args_raw = func.get("arguments", "{}")
                    # 修复：arguments 畸形 JSON 时直接抛异常，改为兜底（args 必须是对象，回退空对象）
                    try:
                        fc_args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except json.JSONDecodeError:
                        logger.warning(f"tool_call arguments 非法JSON，回退空对象: {args_raw[:200]}")
                        fc_args = {}
                    parts.append({
                        "functionCall": {
                            "name": func.get("name", ""),
                            "args": fc_args,
                        }
                    })
                    # 记录 tool_call_id → 函数名 映射，供后续 tool 消息回填 name
                    if tc.get("id"):
                        tool_id_to_name[tc["id"]] = func.get("name", "")

            if role == "tool":
                gemini_role = "user"
                tool_call_id = msg.get("tool_call_id", "")
                # 回填真实函数名（Gemini 要求与 functionCall.name 一致）；映射缺失时回退 tool_call_id
                tool_name = tool_id_to_name.get(tool_call_id)
                if not tool_name:
                    tool_name = tool_call_id
                    logger.debug(f"openai_to_gemini: tool_call_id={tool_call_id} 无对应函数名，回退用作 functionResponse.name")
                parts = [{
                    "functionResponse": {
                        "name": tool_name,
                        "response": {
                            "name": tool_name,
                            "content": content if isinstance(content, str) else json.dumps(content),
                        }
                    }
                }]

            contents.append({
                "role": gemini_role,
                "parts": parts,
            })

        gemini["contents"] = contents

        # 参数映射
        if "temperature" in body:
            gemini["generationConfig"] = gemini.get("generationConfig", {})
            gemini["generationConfig"]["temperature"] = body["temperature"]
        if "max_tokens" in body:
            gemini.setdefault("generationConfig", {})["maxOutputTokens"] = body["max_tokens"]
        if "top_p" in body:
            gemini.setdefault("generationConfig", {})["topP"] = body["top_p"]

        # 工具映射
        tools = body.get("tools", [])
        if tools:
            gemini_tools = []
            for t in tools:
                func = t.get("function", t)
                gemini_tools.append({
                    "functionDeclarations": [{
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    }]
                })
            gemini["tools"] = gemini_tools

        return gemini, metadata

    @staticmethod
    def gemini_to_openai(body: dict) -> dict:
        """
        Gemini → OpenAI Chat 格式转换
        """
        openai = {
            "model": body.get("model", "gemini-2.0-pro"),
            "messages": [],
        }

        # system_instruction
        si = body.get("system_instruction", {})
        if si:
            parts = si.get("parts", [])
            text = " ".join(p.get("text", "") for p in parts if "text" in p)
            if text:
                openai["messages"].append({"role": "system", "content": text})

        # contents
        contents = body.get("contents", [])
        for c in contents:
            role = c.get("role", "user")
            parts = c.get("parts", [])

            # Gemini user → OpenAI user
            if role == "user":
                text_parts = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    if "text" in p:
                        text_parts.append(p["text"])
                    elif "functionResponse" in p:
                        fr = p["functionResponse"]
                        openai["messages"].append({
                            "role": "tool",
                            "tool_call_id": fr.get("name", ""),
                            "content": json.dumps(fr.get("response", {})),
                        })
                    elif "inlineData" in p or "inline_data" in p:
                        # 修复：inlineData → OpenAI image_url base64 data URL（兼容 camelCase/snake_case）
                        inline = p.get("inlineData") or p.get("inline_data") or {}
                        mime = inline.get("mimeType", inline.get("mime_type", "image/jpeg"))
                        openai["messages"].append({
                            "role": "user",
                            "content": [{
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{inline.get('data', '')}"},
                            }],
                        })
                    else:
                        logger.debug(f"gemini_to_openai: 丢弃 user part 类型: {list(p.keys())}")
                if text_parts:
                    openai["messages"].append({
                        "role": "user",
                        "content": " ".join(text_parts),
                    })

            # Gemini model → OpenAI assistant
            elif role == "model":
                text_parts = []
                function_calls = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    if "text" in p:
                        text_parts.append(p["text"])
                    elif "functionCall" in p:
                        fc = p["functionCall"]
                        function_calls.append({
                            "id": fc.get("name", ""),
                            "type": "function",
                            "function": {
                                "name": fc.get("name", ""),
                                "arguments": json.dumps(fc.get("args", {})),
                            }
                        })
                    else:
                        logger.debug(f"gemini_to_openai: 丢弃 model part 类型: {list(p.keys())}")
                msg = {"role": "assistant"}
                if text_parts:
                    msg["content"] = " ".join(text_parts)
                else:
                    msg["content"] = ""
                if function_calls:
                    msg["tool_calls"] = function_calls
                openai["messages"].append(msg)

            else:
                # 修复：未知 role 静默丢弃，记录日志便于排查
                logger.debug(f"gemini_to_openai: 丢弃未知 role 的 content: {role}")

        return openai

    @staticmethod
    def stream_chunk_gemini_to_openai(chunk: str) -> str:
        """
        将 Gemini 流式 chunk 转换为 OpenAI SSE 格式
        """
        try:
            data = json.loads(chunk)
            candidates = data.get("candidates", [])
            if not candidates:
                return ""

            candidate = candidates[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            finish_reason = candidate.get("finishReason", "")

            text = " ".join(p.get("text", "") for p in parts if "text" in p)
            if not text and not finish_reason:
                return ""

            oa_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": None}]}
            if text:
                oa_chunk["choices"][0]["delta"]["content"] = text

            finish_map = {
                "STOP": "stop",
                "MAX_TOKENS": "length",
                "SAFETY": "content_filter",
                "RECITATION": "content_filter",
                "OTHER": "stop",
            }
            if finish_reason:
                oa_chunk["choices"][0]["finish_reason"] = finish_map.get(finish_reason, finish_reason.lower())

            # usage
            usage_meta = data.get("usageMetadata", {})
            if usage_meta:
                oa_chunk["usage"] = {
                    "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                    "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                    "total_tokens": usage_meta.get("totalTokenCount", 0),
                }

            return f"data: {json.dumps(oa_chunk)}\n\n"

        except (json.JSONDecodeError, KeyError, IndexError):
            return ""
