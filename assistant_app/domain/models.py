"""Immutable settings snapshots and approval records."""

import json
from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class Settings:
    provider: str = "anthropic"
    model: str = "claude-sonnet-5-5"
    local_url: str = "http://127.0.0.1:11434"
    gmail_account: str = ""
    calendar_id: str = ""
    notion_page_id: str = ""


@dataclass(frozen=True)
class RuntimeLimits:
    proposal_ttl_seconds: float = 600
    max_pending_proposals: int = 12
    # Permit one bounded tool per round plus a final answer. Tool and elapsed
    # budgets still independently cap work, including sequential mail reads.
    max_model_rounds: int = 14
    max_tool_calls: int = 12
    request_seconds: float = 180
    max_message_chars: int = 12000
    max_context_chars: int = 180000
    max_tool_result_chars: int = 40000

    def __post_init__(self):
        import math
        from dataclasses import fields

        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Runtime limits must be finite and positive.")
            if field.name.startswith("max_") and not isinstance(value, int):
                raise ValueError("Count and size limits must be integers.")


@dataclass(frozen=True)
class Proposal:
    id: str
    action: str
    payload_json: str
    session: str
    config: str
    created: float
    state: str = "pending"
    outcome: str = ""

    @property
    def prepared(self) -> JsonObject:
        return json.loads(self.payload_json)
