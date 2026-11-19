"""Anthropic message adapter."""

from __future__ import annotations

from assistant_app.domain.models import JsonObject
from assistant_app.domain.ports import StorePort

from .common import _content, _fail, _request


def complete(
    store: StorePort, model: str, system: str, messages: list[JsonObject], tools: list[JsonObject], names: set[str]
) -> list[JsonObject]:
    key = store.get_secret("anthropic_key")
    if not isinstance(key, str) or not key.strip():
        _fail("Add an Anthropic API key in settings.")
    if any(ord(c) < 33 or ord(c) > 126 for c in key):
        _fail("Invalid Anthropic API key. Update it in settings.")
    data = {"model": model, "max_tokens": 2048, "system": system, "messages": messages}
    if tools:
        data["tools"] = tools
    response = _request(
        "https://api.anthropic.com/v1/messages",
        data,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    return _content(response.get("content"), names)
