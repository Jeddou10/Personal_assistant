"""Bounded provider adapters. Local inference never installs or falls back.

The loopback transport must disable proxies and redirects (net's contract).
The Ollama daemon is trusted local software; metadata checks reject remote
model aliases, but cannot attest a compromised daemon or concurrent mutation.
"""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject

from .. import net

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOOLS = 32
MAX_CALLS = 12
MAX_RESPONSE_BYTES = 262144
MAX_CONTEXT_BYTES = 1048576
MAX_ARGUMENT_BYTES = 32768
_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _fail(message: Any = "The model returned an invalid or oversized response.") -> NoReturn:
    raise AppError(message)


def _bounded(value: Any, limit: int) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > limit:
            _fail()
        # Copy to avoid retaining or mutating caller-owned dictionaries.
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _fail()


def _arguments(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        _fail()
    return _bounded(value, MAX_ARGUMENT_BYTES)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        _fail()
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        _fail()
    return value


def _tools(tools: list[JsonObject]) -> list[JsonObject]:
    tools = _bounded(tools, 65536)
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        _fail("Invalid or oversized tool definitions.")
    names = set()
    for tool in tools:
        if not isinstance(tool, dict):
            _fail()
        name = tool.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names:
            _fail()
        names.add(name)
        _text(tool.get("description"))
        schema = tool.get("input_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            _fail()
    return [{k: t[k] for k in ("name", "description", "input_schema")} for t in tools]


def _content(content: Any, names: set[str]) -> list[JsonObject]:
    """Validate generated content atomically before exposing any tool call."""
    if not isinstance(content, list) or not content or len(content) > 128:
        _fail()
    result, ids = [], set()
    for block in content:
        if not isinstance(block, dict):
            _fail()
        kind = block.get("type")
        if kind == "text":
            result.append({"type": "text", "text": _text(block.get("text"))})
        elif kind in ("thinking", "redacted_thinking"):
            # Signatures/encrypted data are opaque, not tool arguments or UI text.
            # Empty thinking text is normal when the provider omits its summary.
            fields = ("thinking", "signature") if kind == "thinking" else ("data",)
            preserved = {"type": kind}
            for field in fields:
                value = _text(block.get(field))
                if field != "thinking" and not value:
                    _fail()
                preserved[field] = value
            result.append(preserved)
        elif kind == "tool_use":
            ident = _identifier(block.get("id"))
            name = block.get("name")
            if not isinstance(name, str) or name not in names or ident in ids or len(ids) >= MAX_CALLS:
                _fail()
            ids.add(ident)
            result.append({"type": kind, "id": ident, "name": name, "input": _arguments(block.get("input"))})
        else:
            _fail()
    return _bounded(result, MAX_RESPONSE_BYTES)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        _fail()
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            _fail()
        parts.append(_text(block.get("text")))
    return "\n".join(parts)


def _messages(messages: list[JsonObject], names: set[str]) -> tuple[list[JsonObject], list[JsonObject]]:
    messages = _bounded(messages, MAX_CONTEXT_BYTES)
    if not isinstance(messages, list) or not messages or len(messages) > 256:
        _fail("Invalid or oversized conversation.")
    normalized, local, calls, answered = [], [], {}, set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            _fail()
        role, content = message["role"], message.get("content")
        if isinstance(content, str):
            normalized.append({"role": role, "content": content})
            local.append({"role": role, "content": content})
            continue
        if not isinstance(content, list) or not content or len(content) > 128:
            _fail()
        if role == "assistant":
            blocks = _content(content, names)
            item = {"role": role, "content": "\n".join(b["text"] for b in blocks if b["type"] == "text")}
            functions = []
            for block in blocks:
                if block["type"] == "tool_use":
                    if block["id"] in calls:
                        _fail()
                    calls[block["id"]] = block["name"]
                    functions.append({"function": {"name": block["name"], "arguments": block["input"]}})
            if functions:
                item["tool_calls"] = functions
            local.append(item)
        else:
            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    _fail()
                if block.get("type") == "text":
                    text = _text(block.get("text"))
                    blocks.append({"type": "text", "text": text})
                    local.append({"role": "user", "content": text})
                elif block.get("type") == "tool_result":
                    ident = _identifier(block.get("tool_use_id"))
                    if ident not in calls or ident in answered:
                        _fail()
                    answered.add(ident)
                    text = _result_text(block.get("content"))
                    result = {"type": "tool_result", "tool_use_id": ident, "content": block["content"]}
                    if "is_error" in block:
                        if not isinstance(block["is_error"], bool):
                            _fail()
                        result["is_error"] = block["is_error"]
                    blocks.append(result)
                    local.append(
                        {
                            "role": "tool",
                            "tool_name": calls[ident],
                            "content": ("Tool error: " if block.get("is_error") else "") + text,
                        }
                    )
                else:
                    _fail()
        normalized.append({"role": role, "content": blocks})
    return normalized, local


def _request(url: str, data: Any, *, local: bool = False, headers: dict[str, str] | None = None) -> JsonObject:
    try:
        response = net.request_json("POST", url, headers=headers, data=data, timeout=60, local=local)
    except AppError as exc:
        status = getattr(exc, "status", None)
        message = (
            "Local model request failed. Check Ollama and the installed model."
            if local
            else "Anthropic request failed. Check credentials and model settings."
        )
        raise AppError(message, status=status, uncertain=getattr(exc, "uncertain", False)) from None
    except (OSError, ValueError, TypeError):
        raise AppError("The model service could not complete the request.") from None
    response = _bounded(response, MAX_RESPONSE_BYTES)
    if not isinstance(response, dict) or "error" in response:
        _fail("The model service reported an error.")
    return response
