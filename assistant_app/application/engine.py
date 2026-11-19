"""Conversation use case with injected adapters and isolated approval policy."""

from __future__ import annotations

import copy
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject, Proposal, RuntimeLimits, Settings
from assistant_app.domain.ports import Completion, IntegrationFactory, IntegrationsPort, StorePort

from .approvals import ApprovalService
from .builtin_tools import READ_TOOL_NAMES, TOOLS
from .prompts import SYSTEM
from .tools import ToolDefinition, ToolRegistry, read_handlers


def missing_provider(*_args):
    raise AppError("No AI provider has been configured.")


class Engine:
    def __init__(
        self,
        store: StorePort,
        settings: Settings | None = None,
        integrations: IntegrationsPort | None = None,
        complete: Completion | None = None,
        clock=time.monotonic,
        *,
        limits: RuntimeLimits | None = None,
        integration_factory: IntegrationFactory | None = None,
    ):
        if integrations is None and integration_factory is None:
            raise ValueError("Inject integrations or an integration factory into the application engine.")
        self.store = store
        self.settings = copy.deepcopy(settings or store.load_settings())
        self._integration_factory = integration_factory
        self.integrations = integrations if integrations is not None else integration_factory(self.settings)
        self.complete = complete or missing_provider
        self.clock = clock
        self.limits = limits or RuntimeLimits()
        self._operation = threading.Lock()
        self.approvals = ApprovalService(self.limits, clock)
        self.messages: list[JsonObject] = []
        self.session = uuid4().hex
        self._config = self._fingerprint()
        self.registry = self._build_registry()

    def _build_registry(self) -> ToolRegistry:
        handlers = dict(read_handlers(lambda: self.integrations))
        for action in ("calendar_create", "calendar_update", "calendar_delete", "notion_append"):
            handlers["propose_" + action] = lambda _action=action, **arguments: self._propose(_action, arguments)
        return ToolRegistry(
            [
                (
                    ToolDefinition.from_schema(schema, read_only=schema["name"] in READ_TOOL_NAMES),
                    handlers[schema["name"]],
                )
                for schema in TOOLS
            ]
        )

    @contextmanager
    def _exclusive(self):
        if not self._operation.acquire(False):
            raise AppError("Another operation is running. Wait before changing the session or approvals.")
        try:
            yield
        finally:
            self._operation.release()

    def _fingerprint(self) -> str:
        return json.dumps(
            {"settings": asdict(self.store.load_settings()), "revision": self.store.revision}, sort_keys=True
        )

    @contextmanager
    def configuration_change(self):
        """Serialize controller mutations with chat and approval.

        The UI resets after its worker finishes. Changed configuration invalidates
        the old session through its fingerprint until that reset occurs.
        """
        with self._exclusive():
            yield

    def _check_scope(self) -> None:
        if self._fingerprint() != self._config:
            raise AppError("Settings or credentials changed. Start a new session before continuing.")

    def reset(self) -> None:
        with self._exclusive():
            fingerprint = self._fingerprint()
            if self._integration_factory is None and fingerprint != self._config:
                raise AppError(
                    "Cannot rebind this injected adapter. Construct a new engine or supply an integration factory."
                )
            settings = copy.deepcopy(self.store.load_settings())
            integrations = self._integration_factory(settings) if self._integration_factory else self.integrations
            self.messages.clear()
            self.approvals.reset()
            self.session = uuid4().hex
            self.settings = settings
            self.integrations = integrations
            self._config = fingerprint

    def pending(self) -> list[Proposal]:
        return self.approvals.records()

    def read_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """Trusted UI read without model inference or conversation mutation."""
        with self._exclusive():
            self._check_scope()
            result = self.registry.call(name, arguments, read_only=True)
            self._check_scope()
            return result

    def reject(self, proposal_id: str) -> None:
        with self._exclusive():
            proposal = self.approvals.reject(proposal_id)
            self.messages.append(
                {
                    "role": "user",
                    "content": f"Application result: proposal {proposal_id} ({proposal.action}) was rejected. Nothing was written.",
                }
            )

    def approve(self, proposal_id: str) -> str:
        with self._exclusive():
            self._check_scope()
            proposal = self.approvals.consume(proposal_id, self.session, self._fingerprint())
            try:
                self._check_scope()
                result = self.integrations.execute(proposal.action, proposal.prepared)
                outcome = "Approved change completed. " + json.dumps(result, ensure_ascii=False)[:3000]
            except AppError as exc:
                self.approvals.finish(proposal_id, "unknown" if exc.uncertain else "failed", str(exc))
                raise
            except Exception:
                outcome = "Outcome unknown. Inspect the destination; do not repeat this write."
                self.approvals.finish(proposal_id, "unknown", outcome)
                raise AppError(outcome, uncertain=True) from None
            self.approvals.finish(proposal_id, "succeeded", outcome)
            self.messages.append({"role": "user", "content": "Application execution result: " + outcome})
            return outcome

    def _propose(self, action: str, arguments: JsonObject) -> JsonObject:
        self.approvals.ensure_available()
        prepared = self.integrations.prepare(action, arguments)
        self._check_scope()
        proposal = self.approvals.propose(action, prepared, self.session, self._config)
        return {
            "status": "awaiting_ui_approval",
            "proposal_id": proposal.id,
            "message": "Nothing has been written. Review the pending change in the app.",
        }

    def _dispatch(self, name: str, arguments: JsonObject) -> JsonObject:
        self._check_scope()
        return self.registry.call(name, arguments)

    def _tool_results(self, uses: list[JsonObject], started: float, calls: int):
        results = []
        for block in uses:
            calls += 1
            try:
                if calls > self.limits.max_tool_calls or self.clock() - started > self.limits.request_seconds:
                    raise AppError("Tool/time limit reached. Narrow the request.")
                result = self._dispatch(block.get("name"), block.get("input"))
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(encoded) > self.limits.max_tool_result_chars:
                    encoded = json.dumps(
                        {"truncated": True, "content_excerpt": encoded[: self.limits.max_tool_result_chars // 2]}
                    )
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": encoded})
            except AppError as exc:
                results.append(
                    {"type": "tool_result", "tool_use_id": block["id"], "content": str(exc), "is_error": True}
                )
            except Exception:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "is_error": True,
                        "content": "The tool could not complete the request. Check the connection settings.",
                    }
                )
        return results, calls

    def chat(self, text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > self.limits.max_message_chars:
            raise AppError(f"Enter a message between 1 and {self.limits.max_message_chars} characters.")
        with self._exclusive():
            self._check_scope()
            messages = copy.deepcopy(self.messages)
            messages.append({"role": "user", "content": text})
            started, calls = self.clock(), 0
            system = SYSTEM + "\nCurrent local time: " + datetime.now().astimezone().isoformat()
            system += "\nExplicit saved preferences: " + json.dumps(self.store.load_preferences(), ensure_ascii=False)
            for _ in range(self.limits.max_model_rounds):
                if self.clock() - started > self.limits.request_seconds:
                    raise AppError("This request reached its time limit. Narrow your request and try again.")
                if len(json.dumps(messages)) > self.limits.max_context_chars:
                    raise AppError(
                        "This session reached its context limit. Start a new session or narrow your request."
                    )
                self._check_scope()
                content = self.complete(self.settings, self.store, system, messages, self.registry.definitions())
                if (
                    not isinstance(content, list)
                    or not content
                    or any(not isinstance(block, dict) for block in content)
                ):
                    raise AppError("The AI returned an empty or invalid response.")
                uses = [block for block in content if block.get("type") == "tool_use"]
                if any(not isinstance(block.get("id"), str) or not block["id"] for block in uses):
                    raise AppError("The AI returned an invalid tool identifier.")
                messages.append({"role": "assistant", "content": content})
                if not uses:
                    self._check_scope()
                    self.messages = messages
                    return (
                        "\n".join(block.get("text", "") for block in content if block.get("type") == "text")
                        or "No text response was returned."
                    )
                results, calls = self._tool_results(uses, started, calls)
                messages.append({"role": "user", "content": results})
                self._check_scope()
                self.messages = copy.deepcopy(messages)
                if calls > self.limits.max_tool_calls:
                    break
            raise AppError("This request reached its tool limit. Existing proposals remain available for review.")
