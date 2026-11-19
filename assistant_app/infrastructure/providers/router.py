"""Validate common inputs and select exactly one provider."""

from __future__ import annotations

import re

from assistant_app.domain.models import JsonObject, Settings
from assistant_app.domain.ports import StorePort

from . import anthropic, ollama
from .common import DEFAULT_MODEL, _bounded, _fail, _messages, _text, _tools


def complete(
    settings: Settings, store: StorePort, system: str, messages: list[JsonObject], tools: list[JsonObject]
) -> list[JsonObject]:
    """Return validated content, including opaque thinking history, without retries."""
    provider = getattr(settings, "provider", "anthropic")
    if provider not in ("anthropic", "ollama"):
        _fail("Unknown model provider.")
    model = getattr(settings, "model", None) or DEFAULT_MODEL
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", model):
        _fail("Invalid model setting.")
    system = _bounded(_text(system), 65536)
    tools = _tools(tools)
    names = {tool["name"] for tool in tools}
    messages, local_messages = _messages(messages, names)
    if provider == "anthropic":
        return anthropic.complete(store, model, system, messages, tools, names)
    return ollama.complete(settings, model, system, local_messages, tools, names)
