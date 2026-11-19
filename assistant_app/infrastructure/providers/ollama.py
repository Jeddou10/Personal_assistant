"""Installed local Ollama model adapter; never pulls or falls back."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from assistant_app.domain.models import JsonObject, Settings

from .common import MAX_CALLS, _arguments, _content, _fail, _request, _text

_ENDPOINT = re.compile(r"http://(127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?\Z")


def _local_endpoint(value: Any) -> str:
    match = _ENDPOINT.fullmatch(value) if isinstance(value, str) else None
    if not match or (match[2] is not None and not 1 <= int(match[2]) <= 65535):
        _fail("Local URL must be an HTTP literal loopback endpoint with no path, query, or credentials.")
    return value


def _local_model(model: str) -> None:
    # Reject registry hosts, URLs, traversal and cloud variants before any request.
    if (
        not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?", model)
        or "cloud" in model.lower()
        or ".." in model
        or model.startswith(".")
    ):
        _fail("Choose an already-installed local Ollama model, not a cloud model.")


def _confirm_local(show: JsonObject) -> None:
    # Remote aliases may have innocuous names. Fail closed without GGUF metadata.
    def remote(value: Any) -> bool:
        if isinstance(value, dict):
            return any((str(k).lower().startswith("remote") and bool(v)) or remote(v) for k, v in value.items())
        if isinstance(value, list):
            return any(remote(v) for v in value)
        return False

    if remote(show):
        _fail("Remote Ollama models are not allowed in local mode.")
    details, info = show.get("details"), show.get("model_info")
    capabilities = show.get("capabilities")
    if (
        not isinstance(details, dict)
        or details.get("format") != "gguf"
        or not isinstance(info, dict)
        or not isinstance(info.get("general.architecture"), str)
        or not info["general.architecture"]
        or not isinstance(capabilities, list)
        or "tools" not in capabilities
    ):
        _fail("Select an installed local GGUF model with tool support.")


def complete(
    settings: Settings,
    model: str,
    system: str,
    local_messages: list[JsonObject],
    tools: list[JsonObject],
    names: set[str],
) -> list[JsonObject]:
    endpoint = _local_endpoint(getattr(settings, "local_url", None))
    _local_model(model)
    _confirm_local(_request(endpoint + "/api/show", {"model": model}, local=True))
    data = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 2048},
        "messages": [{"role": "system", "content": system}] + local_messages,
    }
    if tools:
        data["tools"] = [
            {
                "type": "function",
                "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
            }
            for t in tools
        ]
    response = _request(endpoint + "/api/chat", data, local=True)
    message = response.get("message")
    if response.get("done") is not True or not isinstance(message, dict) or message.get("role") != "assistant":
        _fail()
    content = []
    text = _text(message.get("content", ""))
    if text:
        content.append({"type": "text", "text": text})
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list) or len(calls) > MAX_CALLS:
        _fail()
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            _fail()
        content.append(
            {
                "type": "tool_use",
                "id": "toolu_" + uuid4().hex,
                "name": function.get("name"),
                "input": _arguments(function.get("arguments")),
            }
        )
    return _content(content, names)
