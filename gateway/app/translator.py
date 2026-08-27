"""
多协议转换模块 - Anthropic / Gemini / Ollama 协议适配

将不同协议的请求转换为OpenAI格式，调用上游后再转换回原协议格式。
"""
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("acu.translator")


# ========== Anthropic 协议转换 ==========

def anthropic_to_openai(body: dict) -> dict:
    """Anthropic Messages API → OpenAI Chat Completions"""
    messages = []

    # system字段转换
    system = body.get("system", "")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # Anthropic的system可以是content block数组
            text_parts = [b.get("text", "") for b in system if b.get("type") == "text"]
            if text_parts:
                messages.append({"role": "system", "content": "\n".join(text_parts)})

    # messages转换
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Anthropic的content可以是字符串或content block数组
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            content = "\n".join(text_parts)

        messages.append({"role": role, "content": content})

    result = {
        "model": body.get("model", ""),
        "messages": messages,
        "stream": body.get("stream", False),
    }

    # 参数映射
    if "max_tokens" in body:
        result["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        result["temperature"] = body["temperature"]
    if "top_p" in body:
        result["top_p"] = body["top_p"]
    if "top_k" in body:
        result["top_k"] = body["top_k"]
    if "stop_sequences" in body:
        result["stop"] = body["stop_sequences"]

    return result


def openai_to_anthropic(data: dict, model: str) -> dict:
    """OpenAI响应 → Anthropic格式"""
    choices = data.get("choices", [])
    content_text = ""
    if choices:
        content_text = choices[0].get("message", {}).get("content", "")

    usage = data.get("usage", {})

    return {
        "id": data.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content_text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def openai_stream_to_anthropic_stream(line: str, model: str) -> list:
    """OpenAI流式行 → Anthropic流式事件"""
    events = []
    if not line.startswith("data: ") or line == "data: [DONE]":
        if line == "data: [DONE]":
            events.append({
                "type": "message_stop",
            })
        return events

    try:
        data = json.loads(line[6:])
    except json.JSONDecodeError:
        return events

    choices = data.get("choices", [])
    if choices:
        delta = choices[0].get("delta", {})

        # 首个消息开始事件
        if data.get("choices", [{}])[0].get("finish_reason") is None:
            if "role" in delta:
                events.append({
                    "type": "message_start",
                    "message": {
                        "id": data.get("id", ""),
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                })

        # 内容块
        if "content" in delta and delta["content"]:
            events.append({
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": delta["content"]},
            })

        # 结束
        finish_reason = choices[0].get("finish_reason")
        if finish_reason:
            events.append({"type": "content_block_stop", "index": 0})
            events.append({
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
            })

    return events


# ========== Gemini 协议转换 ==========

def gemini_to_openai(body: dict, model: str) -> dict:
    """Gemini generateContent → OpenAI Chat Completions"""
    messages = []

    # systemInstruction转换
    sys_inst = body.get("systemInstruction", {})
    if sys_inst:
        parts = sys_inst.get("parts", [])
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        if text_parts:
            messages.append({"role": "system", "content": "\n".join(text_parts)})

    # contents转换
    for content in body.get("contents", []):
        role = content.get("role", "user")
        # Gemini的role: user/model → OpenAI的role: user/assistant
        if role == "model":
            role = "assistant"

        parts = content.get("parts", [])
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        messages.append({"role": role, "content": "\n".join(text_parts)})

    result = {
        "model": model,
        "messages": messages,
        "stream": body.get("stream", False),
    }

    # generationConfig映射
    gen_config = body.get("generationConfig", {})
    if "temperature" in gen_config:
        result["temperature"] = gen_config["temperature"]
    if "topP" in gen_config:
        result["top_p"] = gen_config["topP"]
    if "topK" in gen_config:
        result["top_k"] = gen_config["topK"]
    if "maxOutputTokens" in gen_config:
        result["max_tokens"] = gen_config["maxOutputTokens"]
    if "stopSequences" in gen_config:
        result["stop"] = gen_config["stopSequences"]

    return result


def openai_to_gemini(data: dict, model: str) -> dict:
    """OpenAI响应 → Gemini格式"""
    choices = data.get("choices", [])
    content_text = ""
    if choices:
        content_text = choices[0].get("message", {}).get("content", "")

    usage = data.get("usage", {})

    return {
        "candidates": [{
            "content": {
                "parts": [{"text": content_text}],
                "role": "model",
            },
            "finishReason": "STOP",
            "index": 0,
        }],
        "usageMetadata": {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens", 0),
        },
        "modelVersion": model,
    }


# ========== Ollama 协议转换 ==========

def ollama_to_openai(body: dict) -> dict:
    """Ollama /api/chat → OpenAI Chat Completions"""
    messages = []
    for msg in body.get("messages", []):
        messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })

    result = {
        "model": body.get("model", ""),
        "messages": messages,
        "stream": body.get("stream", False),
    }

    options = body.get("options", {})
    if "temperature" in options:
        result["temperature"] = options["temperature"]
    if "top_p" in options:
        result["top_p"] = options["top_p"]
    if "num_predict" in options:
        result["max_tokens"] = options["num_predict"]

    return result


def openai_to_ollama(data: dict, model: str) -> dict:
    """OpenAI响应 → Ollama格式"""
    choices = data.get("choices", [])
    content_text = ""
    if choices:
        content_text = choices[0].get("message", {}).get("content", "")

    usage = data.get("usage", {})

    return {
        "model": model,
        "created_at": data.get("created", ""),
        "message": {"role": "assistant", "content": content_text},
        "done": True,
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": usage.get("prompt_tokens", 0),
        "eval_count": usage.get("completion_tokens", 0),
    }


def openai_stream_to_ollama_stream(line: str, model: str) -> list:
    """OpenAI流式行 → Ollama流式格式"""
    results = []
    if not line.startswith("data: ") or line == "data: [DONE]":
        return results

    try:
        data = json.loads(line[6:])
    except json.JSONDecodeError:
        return results

    choices = data.get("choices", [])
    if not choices:
        # 边界情况: choices为空列表时跳过
        return results

    delta = choices[0].get("delta", {})
    if not isinstance(delta, dict):
        # 边界情况: delta不是字典时跳过
        return results

    finish_reason = choices[0].get("finish_reason")
    content = delta.get("content")

    # 边界情况: content为None或空字符串时不输出内容块（但finish_reason仍需处理）
    if content:
        results.append({
            "model": model,
            "created_at": data.get("created", ""),
            "message": {"role": "assistant", "content": content},
            "done": False,
        })

    if finish_reason:
        usage = data.get("usage") or {}
        results.append({
            "model": model,
            "created_at": data.get("created", ""),
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "total_duration": 0,
            "prompt_eval_count": usage.get("prompt_tokens", 0),
            "eval_count": usage.get("completion_tokens", 0),
        })

    return results


# ========== Gemini 流式输出转换 ==========

def openai_stream_to_gemini_stream(line: str, model: str) -> list:
    """OpenAI SSE流式行 → Gemini streamingGenerateContent 格式

    Gemini流式输出格式为 chunks，每个chunk包含candidates数组。
    返回列表因为一行OpenAI SSE可能对应多个Gemini chunk（如首token需要额外元信息）。
    """
    chunks = []
    if not line.startswith("data: ") or line == "data: [DONE]":
        return chunks

    try:
        data = json.loads(line[6:])
    except json.JSONDecodeError:
        return chunks

    choices = data.get("choices", [])
    if not choices:
        return chunks

    delta = choices[0].get("delta", {})
    if not isinstance(delta, dict):
        return chunks

    finish_reason = choices[0].get("finish_reason")

    # 内容chunk
    content = delta.get("content")
    if content:
        chunks.append({
            "candidates": [{
                "content": {
                    "parts": [{"text": content}],
                    "role": "model",
                },
                "finishReason": "STOP" if finish_reason else None,
                "index": 0,
            }],
        })

    # 结束chunk
    if finish_reason:
        usage = data.get("usage") or {}
        chunks.append({
            "candidates": [{
                "content": {
                    "parts": [],
                    "role": "model",
                },
                "finishReason": "STOP",
                "index": 0,
            }],
            "usageMetadata": {
                "promptTokenCount": usage.get("prompt_tokens", 0),
                "candidatesTokenCount": usage.get("completion_tokens", 0),
                "totalTokenCount": usage.get("total_tokens", 0),
            },
            "modelVersion": model,
        })

    return chunks


# ========== 工具调用 / Function Calling 转换 ==========

def anthropic_tools_to_openai(tools: list) -> list:
    """Anthropic tool定义 → OpenAI function calling 格式

    Anthropic格式:
        { "name": "...", "description": "...", "input_schema": { ... } }
    OpenAI格式:
        { "type": "function", "function": { "name": "...", "description": "...", "parameters": { ... } } }
    """
    openai_tools = []
    for tool in tools:
        func = {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
        }
        input_schema = tool.get("input_schema")
        if input_schema:
            func["parameters"] = input_schema
        openai_tools.append({"type": "function", "function": func})
    return openai_tools


def openai_to_anthropic_tools(tool_calls: list) -> list:
    """OpenAI tool_call响应 → Anthropic tool_use content block 格式

    OpenAI格式:
        { "id": "call_xxx", "type": "function", "function": { "name": "...", "arguments": "..." } }
    Anthropic格式:
        { "type": "tool_use", "id": "...", "name": "...", "input": { ... } }
    """
    anthropic_tools = []
    for tc in tool_calls:
        func = tc.get("function", {})
        arguments_str = func.get("arguments", "{}")
        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError:
            arguments = {}

        anthropic_tools.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "input": arguments,
        })
    return anthropic_tools


def gemini_tools_to_openai(function_declarations: list) -> list:
    """Gemini functionDeclarations → OpenAI function calling 格式

    Gemini格式:
        { "name": "...", "description": "...", "parameters": { ... } }
    OpenAI格式:
        { "type": "function", "function": { "name": "...", "description": "...", "parameters": { ... } } }
    """
    openai_tools = []
    for fd in function_declarations:
        func = {
            "name": fd.get("name", ""),
            "description": fd.get("description", ""),
        }
        parameters = fd.get("parameters")
        if parameters:
            func["parameters"] = parameters
        openai_tools.append({"type": "function", "function": func})
    return openai_tools


def openai_to_gemini_tools(tool_calls: list) -> list:
    """OpenAI tool_call响应 → Gemini functionCall 格式

    OpenAI格式:
        { "id": "call_xxx", "type": "function", "function": { "name": "...", "arguments": "..." } }
    Gemini格式:
        { "functionCall": { "name": "...", "args": { ... } } }
    """
    gemini_parts = []
    for tc in tool_calls:
        func = tc.get("function", {})
        arguments_str = func.get("arguments", "{}")
        try:
            args = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError:
            args = {}

        gemini_parts.append({
            "functionCall": {
                "name": func.get("name", ""),
                "args": args,
            },
        })
    return gemini_parts


# ========== 协议检测 ==========

def detect_protocol(headers: dict, body: dict) -> str:
    """根据请求头和请求体检测传入协议类型

    返回值: "openai" | "anthropic" | "gemini" | "ollama" | "unknown"

    检测策略（按优先级）:
    1. 通过特定请求头判定（如 anthropic-version → anthropic）
    2. 通过请求体字段特征判定（如 contents + candidates → gemini）
    """
    # ---- 头部特征检测 ----
    # Anthropic: 带有 anthropic-version 头
    if "anthropic-version" in headers:
        return "anthropic"

    # Gemini: 路径中通常含 /v1/models/ 但通过头不易区分，留给body检测
    # Ollama: 可能带有特定标识头
    ollama_header_markers = ("x-ollama",)
    for marker in ollama_header_markers:
        if marker in headers:
            return "ollama"

    # ---- 请求体特征检测 ----
    if not body or not isinstance(body, dict):
        return "unknown"

    # Anthropic特征: messages + 可选 system 顶层字段，且无 model 字段在顶层（或可选）
    # 更强特征: 存在 top_k 或 stop_sequences（OpenAI不用这些字段名）
    if "messages" in body:
        if "stop_sequences" in body:
            return "anthropic"
        if "top_k" in body and "top_p" not in body:
            return "anthropic"
        # 存在system顶层字段且不是字符串（Anthropic允许数组形式）
        if isinstance(body.get("system"), list):
            return "anthropic"

    # Gemini特征: contents 字段 + systemInstruction / generationConfig / candidates
    if "contents" in body:
        return "gemini"
    if "systemInstruction" in body:
        return "gemini"
    if "generationConfig" in body:
        return "gemini"

    # Ollama特征: options / template / done 等字段
    if "options" in body and "messages" in body:
        if "template" in body or "done" in body:
            return "ollama"
        # options 内有 num_predict 等 Ollama 特有参数
        options = body.get("options", {})
        if isinstance(options, dict) and "num_predict" in options:
            return "ollama"

    # OpenAI特征: messages + model 顶层字段 + 可选 stream
    if "messages" in body and "model" in body:
        return "openai"

    return "unknown"


# ========== 会话上下文管理 ==========

class ConversationContext:
    """多轮对话上下文管理器

    跟踪 conversation_id 在不同协议间的映射、消息历史及token计数。
    """

    # 简易token估算: 英文约4字符≈1token，中文约1.5字符≈1token
    # 这里用粗略估算: 平均3字符≈1token
    CHARS_PER_TOKEN = 3

    def __init__(self, conversation_id: str | None = None, max_history_tokens: int = 8192):
        """初始化会话上下文

        Args:
            conversation_id: 内部会话ID，若为None则自动生成
            max_history_tokens: 上下文窗口最大token数，超出时截断最早的消息
        """
        self.conversation_id: str = conversation_id or f"conv_{uuid.uuid4().hex[:16]}"
        self.max_history_tokens: int = max_history_tokens
        # 协议特定conversation_id映射: protocol_name → protocol_conversation_id
        self._protocol_ids: dict[str, str] = {}
        # 消息历史: [{"role": "...", "content": "...", "token_count": int}, ...]
        self._history: list[dict] = []

    def map_conversation_id(self, protocol: str, protocol_conversation_id: str) -> None:
        """将协议特定的会话ID映射到内部conversation_id

        Args:
            protocol: 协议名称，如 "anthropic", "gemini", "ollama", "openai"
            protocol_conversation_id: 该协议的会话ID
        """
        self._protocol_ids[protocol] = protocol_conversation_id

    def get_protocol_conversation_id(self, protocol: str) -> str | None:
        """获取指定协议的会话ID

        Args:
            protocol: 协议名称

        Returns:
            该协议的会话ID，未映射则返回None
        """
        return self._protocol_ids.get(protocol)

    def get_all_protocol_ids(self) -> dict[str, str]:
        """获取所有已映射的协议会话ID"""
        return dict(self._protocol_ids)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算文本的token数

        使用字符数除以平均比率来近似token计数。
        """
        if not text:
            return 0
        return max(1, len(text) // ConversationContext.CHARS_PER_TOKEN)

    def add_message(self, role: str, content: str) -> None:
        """向历史中添加一条消息

        Args:
            role: 消息角色 (system/user/assistant/tool)
            content: 消息内容
        """
        token_count = self._estimate_tokens(content)
        self._history.append({
            "role": role,
            "content": content,
            "token_count": token_count,
        })
        self._trim_history()

    def _trim_history(self) -> None:
        """当历史token数超过上限时，从最早的消息开始截断

        保留 system 角色消息不被截断，只截断 user/assistant 消息。
        """
        while self._total_tokens() > self.max_history_tokens:
            # 找到最早的非system消息并移除
            for i, msg in enumerate(self._history):
                if msg["role"] != "system":
                    self._history.pop(i)
                    break
            else:
                # 如果全是system消息，不再截断
                break

    def _total_tokens(self) -> int:
        """计算历史消息的总token数"""
        return sum(msg.get("token_count", 0) for msg in self._history)

    def get_history(self, max_tokens: int | None = None) -> list[dict]:
        """获取消息历史

        Args:
            max_tokens: 可选限制，返回不超过此token数的最近消息

        Returns:
            消息列表，每项包含 role/content/token_count
        """
        if max_tokens is None:
            return list(self._history)

        result: list[dict] = []
        total = 0
        # 从最新消息往前取，确保不超过max_tokens
        for msg in reversed(self._history):
            msg_tokens = msg.get("token_count", 0)
            if total + msg_tokens > max_tokens:
                break
            result.append(msg)
            total += msg_tokens

        result.reverse()
        return result

    def get_openai_messages(self, max_tokens: int | None = None) -> list[dict]:
        """获取OpenAI格式的消息列表（仅含role和content字段）

        Args:
            max_tokens: 可选token上限

        Returns:
            OpenAI格式消息列表
        """
        history = self.get_history(max_tokens)
        return [{"role": m["role"], "content": m["content"]} for m in history]

    def token_count(self) -> int:
        """返回当前历史消息的总token估算数"""
        return self._total_tokens()

    def clear_history(self) -> None:
        """清空消息历史"""
        self._history.clear()


# ========== IDE / 开发者工具 协议适配 ==========

# ---- Cursor IDE 协议适配 ----

def cursor_to_openai(request_body: dict) -> dict:
    """将 Cursor IDE 请求转换为标准 OpenAI 格式。

    Cursor 使用 OpenAI 格式，但额外包含 'cursor_context' 字段，
    其中含有文件 URI 和选区范围等信息。
    转换时移除 Cursor 特有字段，并将 cursor_context 注入为 system 消息。
    """
    result = dict(request_body)
    cursor_context = result.pop("cursor_context", None)
    if cursor_context:
        context_parts = []
        file_uris = cursor_context.get("file_uris", [])
        if file_uris:
            context_parts.append("Files: " + ", ".join(file_uris))
        selection = cursor_context.get("selection_ranges", [])
        if selection:
            context_parts.append("Selections: " + json.dumps(selection))
        if context_parts:
            system_msg = {"role": "system", "content": "[Cursor Context] " + "; ".join(context_parts)}
            messages = result.get("messages", [])
            result["messages"] = [system_msg] + messages
    # 移除其他 Cursor 特有顶层字段
    for key in list(result.keys()):
        if key.startswith("cursor_"):
            result.pop(key, None)
    return result


def openai_to_cursor(response_body: dict) -> dict:
    """标准 OpenAI 响应兼容 Cursor，直接返回。"""
    return response_body


# ---- Cline / Continue 协议适配 ----

def cline_to_openai(request_body: dict) -> dict:
    """将 Cline/Continue 请求转换为标准 OpenAI 格式。

    Cline 在 OpenAI 格式基础上添加了 'task' 和 'workspace_files' 字段，
    用于携带任务上下文和工作区文件列表。
    """
    result = dict(request_body)
    task = result.pop("task", None)
    workspace_files = result.pop("workspace_files", None)
    context_parts = []
    if task:
        context_parts.append(f"Task: {task}")
    if workspace_files:
        if isinstance(workspace_files, list):
            context_parts.append("Workspace files: " + ", ".join(str(f) for f in workspace_files))
        else:
            context_parts.append("Workspace files: " + json.dumps(workspace_files))
    if context_parts:
        system_msg = {"role": "system", "content": "[Cline Context] " + "; ".join(context_parts)}
        messages = result.get("messages", [])
        result["messages"] = [system_msg] + messages
    # 移除其他 Cline 特有顶层字段
    for key in list(result.keys()):
        if key.startswith("cline_") or key.startswith("continue_"):
            result.pop(key, None)
    return result


def openai_to_cline(response_body: dict) -> dict:
    """将 OpenAI 响应转换回 Cline 格式。

    Cline 兼容 OpenAI 响应格式，直接返回即可。
    """
    return response_body


# ---- Claude Code 协议适配 ----

def claude_code_to_openai(request_body: dict) -> dict:
    """将 Claude Code 请求转换为 OpenAI 格式。

    Claude Code 发送 Anthropic Messages API 格式（含 tool_use），
    并附带 'claude_code_version' 和 'ide_type' 等专有字段。
    复用 anthropic_to_openai 进行核心转换，剥离 Claude Code 特有字段。
    """
    # 提取并剥离 Claude Code 特有字段
    _cc_version = request_body.get("claude_code_version")
    _ide_type = request_body.get("ide_type")
    # 构建纯 Anthropic 格式请求体
    anthropic_body = {k: v for k, v in request_body.items()
                      if k not in ("claude_code_version", "ide_type")}
    result = anthropic_to_openai(anthropic_body)
    # 在 system 消息中注入 Claude Code 上下文
    context_parts = []
    if _cc_version:
        context_parts.append(f"Claude Code version: {_cc_version}")
    if _ide_type:
        context_parts.append(f"IDE type: {_ide_type}")
    if context_parts:
        system_msg = {"role": "system", "content": "[Claude Code] " + "; ".join(context_parts)}
        result["messages"] = [system_msg] + result.get("messages", [])
    return result


def openai_to_claude_code(response_body: dict) -> dict:
    """将 OpenAI 响应转换回 Claude Code 格式。

    复用 openai_to_anthropic 进行核心转换，并添加 Claude Code 特有字段。
    """
    model = response_body.get("model", "")
    result = openai_to_anthropic(response_body, model)
    result["claude_code_version"] = "1.0"
    return result


# ---- Cherry Studio 协议适配 ----

def cherry_studio_to_openai(request_body: dict) -> dict:
    """将 Cherry Studio 请求转换为标准 OpenAI 格式。

    Cherry Studio 使用标准 OpenAI 格式，额外包含 'plugin_context' 字段。
    """
    result = dict(request_body)
    plugin_context = result.pop("plugin_context", None)
    if plugin_context:
        system_msg = {"role": "system", "content": "[Cherry Studio] " + json.dumps(plugin_context)}
        messages = result.get("messages", [])
        result["messages"] = [system_msg] + messages
    # 移除其他 Cherry Studio 特有顶层字段
    for key in list(result.keys()):
        if key.startswith("cherry_"):
            result.pop(key, None)
    return result


def openai_to_cherry_studio(response_body: dict) -> dict:
    """标准 OpenAI 响应兼容 Cherry Studio，直接返回。"""
    return response_body


# ---- 通用 IDE 协议适配 ----

# 已知的 IDE 类型及其特有顶层字段前缀
_IDE_TYPE_FIELD_PREFIXES: dict[str, list[str]] = {
    "vscode": ["vscode_", "vs_"],
    "goose": ["goose_"],
    "roo_code": ["roo_", "roo_code_"],
    "kilo_code": ["kilo_", "kilo_code_"],
    "factory_droid": ["factory_", "droid_"],
    "crush": ["crush_"],
    "grok_cli": ["grok_"],
    "gemini_cli": ["gemini_cli_"],
}


def ide_generic_to_openai(request_body: dict, ide_type: str = "") -> dict:
    """通用 IDE 协议适配器。

    剥离 IDE 特有元数据字段，将请求规范化为 OpenAI 格式。
    支持的 IDE 类型: vscode, goose, roo_code, kilo_code, factory_droid, crush, grok_cli, gemini_cli

    Args:
        request_body: 原始请求体
        ide_type: IDE 类型标识，用于确定需要剥离的字段前缀
    """
    result = dict(request_body)
    # 剥离 IDE 特有顶层字段
    prefixes = _IDE_TYPE_FIELD_PREFIXES.get(ide_type, [])
    for key in list(result.keys()):
        for prefix in prefixes:
            if key.startswith(prefix):
                result.pop(key, None)
                break
    # 注入 IDE 上下文到 system 消息
    ide_meta = result.pop("ide_metadata", None)
    if ide_type or ide_meta:
        context_parts = []
        if ide_type:
            context_parts.append(f"IDE: {ide_type}")
        if ide_meta:
            context_parts.append(json.dumps(ide_meta))
        system_msg = {"role": "system", "content": "[IDE Context] " + "; ".join(context_parts)}
        messages = result.get("messages", [])
        result["messages"] = [system_msg] + messages
    return result


def openai_to_ide_generic(response_body: dict) -> dict:
    """通用 IDE 响应 — 标准 OpenAI 格式，直接返回。"""
    return response_body


# ========== IDE 协议检测与路由 ==========

# IDE 类型 user-agent 关键词映射
_IDE_USER_AGENT_PATTERNS: dict[str, list[str]] = {
    "cursor": ["cursor", "cursoride"],
    "cline": ["cline", "continue-dev"],
    "claude_code": ["claude-code", "claudecode"],
    "cherry_studio": ["cherry-studio", "cherrystudio"],
    "vscode": ["vscode", "vs-code"],
    "goose": ["goose"],
    "roo_code": ["roo-code", "roocode"],
    "kilo_code": ["kilo-code", "kilocode"],
    "factory_droid": ["factory-droid", "factorydroid"],
    "crush": ["crush"],
    "grok_cli": ["grok-cli", "grokcli"],
    "gemini_cli": ["gemini-cli", "geminicli"],
}


def _detect_ide_type(headers: dict, body: dict) -> str | None:
    """根据请求头和请求体检测 IDE 类型。

    检测优先级:
    1. x-ide-type 请求头
    2. 请求体中 ide_type 字段
    3. 请求体特征字段 (cursor_context, task+workspace_files, claude_code_version, plugin_context)
    4. User-Agent 中的 IDE 关键词

    Returns:
        IDE 类型字符串，无法识别时返回 None
    """
    # 1. x-ide-type 头
    ide_type = headers.get("x-ide-type", "").strip().lower()
    if ide_type:
        return ide_type

    # 2. 请求体 ide_type 字段
    if body and isinstance(body, dict):
        ide_type = body.get("ide_type", "").strip().lower() if isinstance(body.get("ide_type"), str) else ""
        if ide_type:
            return ide_type

        # 3. 请求体特征字段
        if "cursor_context" in body:
            return "cursor"
        if "task" in body and "workspace_files" in body:
            return "cline"
        if "claude_code_version" in body:
            return "claude_code"
        if "plugin_context" in body:
            return "cherry_studio"

    # 4. User-Agent 关键词
    user_agent = headers.get("user-agent", "").lower()
    if user_agent:
        for ide_name, patterns in _IDE_USER_AGENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in user_agent:
                    return ide_name

    return None


def detect_protocol_with_ide(headers: dict, body: dict) -> str:
    """增强版协议检测 — 在原有协议检测基础上增加 IDE 协议识别。

    返回值: "cursor" | "cline" | "claude_code" | "cherry_studio" |
            "openai" | "anthropic" | "gemini" | "ollama" | "unknown"

    IDE 类型检测优先于传统协议检测，因为 IDE 请求通常基于 OpenAI/Anthropic
    格式但带有专有扩展字段。
    """
    ide_type = _detect_ide_type(headers, body)
    if ide_type:
        # claude_code 基于 Anthropic 协议，其他 IDE 基于 OpenAI 协议
        # 但在路由层面统一返回 IDE 类型
        return ide_type
    return detect_protocol(headers, body)


@dataclass
class IDERoutingInfo:
    """IDE 路由端点信息"""
    ide_type: str       # cursor, cline, claude_code, cherry_studio, etc.
    protocol: str       # openai, anthropic
    supports_streaming: bool
    supports_tools: bool
    supports_images: bool


# IDE 类型 → 底层协议及能力映射
_IDE_CAPABILITIES: dict[str, dict] = {
    "cursor": {"protocol": "openai", "streaming": True, "tools": True, "images": True},
    "cline": {"protocol": "openai", "streaming": True, "tools": True, "images": False},
    "claude_code": {"protocol": "anthropic", "streaming": True, "tools": True, "images": True},
    "cherry_studio": {"protocol": "openai", "streaming": True, "tools": False, "images": True},
    "vscode": {"protocol": "openai", "streaming": True, "tools": True, "images": True},
    "goose": {"protocol": "openai", "streaming": True, "tools": True, "images": False},
    "roo_code": {"protocol": "openai", "streaming": True, "tools": True, "images": False},
    "kilo_code": {"protocol": "openai", "streaming": True, "tools": True, "images": False},
    "factory_droid": {"protocol": "openai", "streaming": True, "tools": True, "images": False},
    "crush": {"protocol": "openai", "streaming": True, "tools": False, "images": False},
    "grok_cli": {"protocol": "openai", "streaming": True, "tools": True, "images": False},
    "gemini_cli": {"protocol": "openai", "streaming": True, "tools": True, "images": True},
}


def detect_ide_routing(headers: dict, body: dict) -> IDERoutingInfo:
    """检测 IDE 类型并返回路由信息。

    根据请求头和请求体识别 IDE 类型，然后查找对应的能力映射，
    返回 IDERoutingInfo 实例供路由层使用。

    Args:
        headers: HTTP 请求头字典（小写键）
        body: 请求体字典

    Returns:
        IDERoutingInfo 实例，包含 ide_type、protocol、supports_streaming、
        supports_tools、supports_images 等路由信息
    """
    ide_type = _detect_ide_type(headers, body)
    if not ide_type:
        ide_type = "unknown"

    caps = _IDE_CAPABILITIES.get(ide_type, {
        "protocol": "openai", "streaming": True, "tools": True, "images": True
    })

    return IDERoutingInfo(
        ide_type=ide_type,
        protocol=caps["protocol"],
        supports_streaming=caps["streaming"],
        supports_tools=caps["tools"],
        supports_images=caps["images"],
    )
