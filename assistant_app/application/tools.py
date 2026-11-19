"""Explicit tool routing and argument validation shared by app and MCP reads."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    schema_json: str
    read_only: bool

    @classmethod
    def from_schema(cls, schema: JsonObject, *, read_only: bool):
        return cls(schema["name"], schema["description"], json.dumps(schema["input_schema"], sort_keys=True), read_only)

    def as_schema(self) -> JsonObject:
        return {"name": self.name, "description": self.description, "input_schema": json.loads(self.schema_json)}

    def validate(self, arguments: Any) -> JsonObject:
        schema = json.loads(self.schema_json)
        properties = schema["properties"]
        if not isinstance(arguments, dict) or set(arguments) - set(properties):
            raise AppError("Tool arguments contain unsupported fields.")
        if set(schema.get("required", ())) - set(arguments):
            raise AppError("Required tool arguments are missing.")
        for key, value in arguments.items():
            if properties[key].get("type") != "string" or not isinstance(value, str) or len(value) > 10000:
                raise AppError("Tool arguments have invalid types or exceed their size limit.")
        return dict(arguments)


class ToolRegistry:
    """Only trusted application code registers handlers; tools cannot register tools."""

    def __init__(self, entries: list[tuple[ToolDefinition, Callable[..., JsonObject]]]):
        self._entries = {}
        for definition, handler in entries:
            if definition.name in self._entries or not callable(handler):
                raise ValueError("Tool names must be unique and handlers callable.")
            self._entries[definition.name] = (definition, handler)

    def definitions(self, *, read_only: bool = False) -> list[JsonObject]:
        return [
            definition.as_schema() for definition, _ in self._entries.values() if not read_only or definition.read_only
        ]

    def call(self, name: str, arguments: JsonObject, *, read_only: bool = False) -> JsonObject:
        if not isinstance(name, str) or name not in self._entries:
            raise AppError("Unknown tool. Approval, memory writes and email sending are not model tools.")
        definition, handler = self._entries[name]
        if read_only and not definition.read_only:
            raise AppError("This interface allows read-only tools only.")
        return handler(**definition.validate(arguments))


def read_handlers(integrations) -> Mapping[str, Callable[..., JsonObject]]:
    """Explicitly name every allowed read, never dispatch with arbitrary getattr."""
    return {
        "search_mail": lambda query: integrations().search_mail(query),
        "read_mail": lambda message_id: integrations().read_mail(message_id),
        "list_events": lambda time_min, time_max: integrations().list_events(time_min, time_max),
        "read_notion": lambda: integrations().read_notion(),
    }
