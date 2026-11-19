"""Application policies tested without a desktop, credentials or network."""

import ast
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock, patch

from assistant_app.application.approvals import ApprovalService
from assistant_app.application.controller import ApplicationController
from assistant_app.application.engine import Engine
from assistant_app.application.read_service import ReadService
from assistant_app.application.tools import ToolDefinition, ToolRegistry
from assistant_app.domain.errors import AppError
from assistant_app.domain.models import RuntimeLimits, Settings


class Store:
    def __init__(self):
        self.settings = Settings()
        self.revision = 0
        self.secrets = {}
        self.preferences = []

    def load_settings(self):
        return self.settings

    def save_settings(self, settings):
        self.settings = settings
        self.revision += 1

    def set_secret(self, name, value):
        self.secrets[name] = value
        self.revision += 1

    def load_preferences(self):
        return list(self.preferences)

    def save_preferences(self, values):
        self.preferences = list(values)


class ApplicationTests(unittest.TestCase):
    def test_approval_consume_is_atomic_under_contention(self):
        approvals = ApprovalService(RuntimeLimits())
        proposal = approvals.propose("notion_append", {"text": "once"}, "session", "config")
        barrier = threading.Barrier(8)

        def consume(_):
            barrier.wait(timeout=5)
            try:
                approvals.consume(proposal.id, "session", "config")
                return True
            except AppError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(consume, range(8))), 1)
        approvals.finish(proposal.id, "unknown", "Inspect destination")
        with self.assertRaises(AppError):
            approvals.propose("notion_append", {"text": "repeat"}, "session", "config")

    def test_limits_and_snapshots_are_validated(self):
        for options in (
            {"max_tool_calls": True},
            {"request_seconds": float("nan")},
            {"max_message_chars": 1.5},
            {"max_pending_proposals": 0},
        ):
            with self.assertRaises(ValueError):
                RuntimeLimits(**options)
        with self.assertRaises(FrozenInstanceError):
            Settings().model = "changed"
        approvals = ApprovalService(RuntimeLimits(max_pending_proposals=1))
        first = approvals.propose("write", {"text": "one"}, "s", "c")
        self.assertEqual(approvals.propose("write", {"text": "one"}, "s", "c"), first)
        with self.assertRaises(AppError):
            approvals.propose("write", {"text": "two"}, "s", "c")
        with self.assertRaises(AppError):
            approvals.finish(first.id, "succeeded", "Cannot skip approval")

    def test_registry_read_policy_cannot_be_changed_through_schema(self):
        schema = {
            "name": "write",
            "description": "Test",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }
        handler = Mock()
        registry = ToolRegistry([(ToolDefinition.from_schema(schema, read_only=False), handler)])
        schema["name"] = "read"
        registry.definitions()[0]["input_schema"]["properties"]["other"] = {"type": "string"}
        with self.assertRaises(AppError):
            registry.call("write", {"text": "value"}, read_only=True)
        with self.assertRaises(AppError):
            registry.call("write", {"other": "value"})
        handler.assert_not_called()

    def test_read_service_discards_results_after_scope_change(self):
        store = Store()
        api = Mock()

        def read(query):
            store.save_settings(replace(store.settings, gmail_account="changed@example.invalid"))
            return {"private": query}

        api.search_mail.side_effect = read
        service = ReadService(store, lambda settings: api)
        with self.assertRaisesRegex(AppError, "Discarded stale"):
            service.call("search_mail", {"query": "private data"})
        with self.assertRaises(AppError):
            service.call("propose_notion_append", {"text": "No write"})
        api.prepare.assert_not_called()
        api.execute.assert_not_called()

    def test_mcp_discovery_does_not_construct_storage(self):
        from assistant_app.bootstrap import create_read_service

        with patch("assistant_app.bootstrap.Store", side_effect=AssertionError("No storage")):
            service = create_read_service()
            self.assertEqual(len(service.tool_definitions()), 4)

    def test_controller_validates_before_persisting_credentials(self):
        store = Store()
        controller = ApplicationController(store, Engine(store, integrations=Mock()), Mock(), Mock())
        for settings in (
            Settings(provider="unknown"),
            Settings(notion_page_id="bad"),
            Settings(provider="ollama", local_url="https://remote.invalid"),
        ):
            with self.assertRaises(AppError):
                controller.save_settings(settings, {"anthropic_key": "synthetic"})
        self.assertEqual(store.secrets, {})
        controller.save_settings(Settings(provider="ollama", local_url="http://127.0.0.1:11434/"))
        self.assertEqual(store.settings.local_url, "http://127.0.0.1:11434")
        with self.assertRaises(AppError):
            controller.save_preferences([""] * 31)
        self.assertEqual(store.preferences, [])

    def test_controller_mutations_cannot_race_with_approved_write(self):
        store = Store()
        engine = Engine(store, integrations=Mock())
        controller = ApplicationController(store, engine, Mock(), Mock())
        actions = [
            lambda: controller.save_settings(Settings(), {"notion_token": "replacement"}),
            lambda: controller.connect_google("unused"),
            lambda: controller.select_calendar("selected"),
            controller.disconnect,
            lambda: controller.save_preferences(["new rule"]),
        ]
        with engine.configuration_change():
            for action in actions:
                with self.assertRaisesRegex(AppError, "Another operation"):
                    action()
        self.assertEqual(store.revision, 0)
        self.assertEqual(store.preferences, [])

    def test_reset_requires_rebinding_when_injected_scope_changes(self):
        store = Store()
        adapter = Mock()
        engine = Engine(store, integrations=adapter)
        original_session = engine.session
        store.save_settings(replace(store.settings, notion_page_id="new-selection"))
        with self.assertRaisesRegex(AppError, "Cannot rebind"):
            engine.reset()
        self.assertEqual(engine.session, original_session)
        factory = Mock(side_effect=lambda settings: Mock(settings=settings))
        engine = Engine(store, integration_factory=factory)
        store.save_settings(replace(store.settings, notion_page_id="another-selection"))
        engine.reset()
        self.assertEqual(engine.integrations.settings.notion_page_id, "another-selection")
        self.assertEqual(factory.call_count, 2)

    def test_layers_do_not_import_concrete_adapters(self):
        root = Path(__file__).resolve().parents[1] / "assistant_app"
        allowed = {
            "domain": {"domain"},
            "application": {"application", "domain"},
            "retrieval": {"retrieval", "domain"},
            "infrastructure": {"infrastructure", "domain"},
        }
        for layer, dependencies in allowed.items():
            for path in (root / layer).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    imports = (
                        [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else []
                    )
                    for name in imports:
                        if name.startswith("assistant_app."):
                            self.assertIn(name.split(".")[1], dependencies, str(path))


if __name__ == "__main__":
    unittest.main()
