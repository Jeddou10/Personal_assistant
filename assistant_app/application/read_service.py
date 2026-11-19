"""Read-only application facade for explicitly launched local MCP hosts."""

import json
from dataclasses import asdict

from assistant_app.domain.errors import AppError
from assistant_app.domain.ports import IntegrationFactory, StorePort

from .builtin_tools import READ_TOOL_NAMES, TOOLS
from .tools import ToolDefinition, ToolRegistry, read_handlers


class ReadService:
    def __init__(self, store: StorePort, integration_factory: IntegrationFactory):
        self.store = store
        self.integration_factory = integration_factory

    @staticmethod
    def tool_definitions():
        return [
            ToolDefinition.from_schema(t, read_only=True).as_schema() for t in TOOLS if t["name"] in READ_TOOL_NAMES
        ]

    def call(self, name, arguments):
        settings = self.store.load_settings()
        fingerprint = json.dumps((asdict(settings), self.store.revision), sort_keys=True)
        integrations = self.integration_factory(settings)
        handlers = read_handlers(lambda: integrations)
        registry = ToolRegistry(
            [(ToolDefinition.from_schema(t, read_only=True), handlers[t["name"]]) for t in self.tool_definitions()]
        )
        result = registry.call(name, arguments, read_only=True)
        current = json.dumps((asdict(self.store.load_settings()), self.store.revision), sort_keys=True)
        if current != fingerprint:
            raise AppError("Settings changed during the read. Discarded stale results; retry with current selections.")
        return result
