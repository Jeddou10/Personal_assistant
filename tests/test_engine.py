import copy
import unittest

from assistant_app.application.engine import Engine
from assistant_app.domain.errors import AppError
from assistant_app.domain.models import Settings


class MemoryStore:
    def __init__(self):
        self.settings = Settings()
        self.revision = 0
        self.preferences = ["Be concise."]

    def load_settings(self):
        return copy.deepcopy(self.settings)

    def load_preferences(self):
        return list(self.preferences)


class FakeIntegrations:
    def __init__(self):
        self.writes = []
        self.error = None

    def prepare(self, action, arguments):
        return {"action": action, "arguments": copy.deepcopy(arguments), "destination": "fixed"}

    def execute(self, action, prepared):
        self.writes.append((action, prepared))
        if self.error:
            raise self.error
        return {"completed": True}

    def search_mail(self, query):
        return {"messages": [], "query": query}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.api = FakeIntegrations()
        self.now = [0.0]
        self.engine = Engine(self.store, integrations=self.api, clock=lambda: self.now[0])

    def propose(self):
        result = self.engine._dispatch("propose_notion_append", {"text": "Exact approved text"})
        return result["proposal_id"]

    def test_proposal_never_executes_until_approval_and_only_once(self):
        ident = self.propose()
        self.assertEqual(self.api.writes, [])
        self.engine.approve(ident)
        self.assertEqual(len(self.api.writes), 1)
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        self.assertEqual(len(self.api.writes), 1)

    def test_preview_cannot_modify_stored_payload(self):
        ident = self.propose()
        self.engine.pending()[0].prepared["arguments"]["text"] = "tampered"
        self.engine.approve(ident)
        self.assertEqual(self.api.writes[0][1]["arguments"]["text"], "Exact approved text")

    def test_model_cannot_approve_send_mail_or_save_memory(self):
        for name in ("approve", "execute", "send_mail", "save_preferences", "delete_file"):
            with self.assertRaises(AppError):
                self.engine._dispatch(name, {})
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.store.preferences, ["Be concise."])

    def test_resource_override_and_bad_arguments_are_rejected(self):
        for args in ({"text": "x", "page_id": "elsewhere"}, {"text": 42}, {}, {"text": "x" * 10001}):
            with self.assertRaises(AppError):
                self.engine._dispatch("propose_notion_append", args)

    def test_rejection_and_revision_require_new_approval(self):
        ident = self.propose()
        self.engine.reject(ident)
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        revised = self.propose()
        self.assertNotEqual(ident, revised)
        self.assertEqual(self.api.writes, [])

    def test_expiry(self):
        ident = self.propose()
        self.now[0] = 600
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        self.assertEqual(self.engine.pending()[0].state, "expired")

    def test_changed_credentials_or_destinations_block_dispatch(self):
        ident = self.propose()
        self.store.revision += 1
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        self.assertEqual(self.api.writes, [])

    def test_reset_discards_conversation_and_proposals(self):
        ident = self.propose()
        self.engine.messages.append({"role": "user", "content": "private"})
        before = self.engine.session
        self.engine.reset()
        self.assertNotEqual(before, self.engine.session)
        self.assertEqual(self.engine.messages, [])
        self.assertEqual(self.engine.pending(), [])
        with self.assertRaises(AppError):
            self.engine.approve(ident)

    def test_unknown_outcome_is_not_replayed(self):
        ident = self.propose()
        self.api.error = AppError("Timeout after dispatch", uncertain=True)
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        self.assertEqual(self.engine.pending()[0].state, "unknown")
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        with self.assertRaises(AppError):
            self.propose()
        self.assertEqual(len(self.api.writes), 1)

    def test_known_failure_not_replayed(self):
        ident = self.propose()
        self.api.error = AppError("Conflict", status=412)
        with self.assertRaises(AppError):
            self.engine.approve(ident)
        self.assertEqual(self.engine.pending()[0].state, "failed")
        with self.assertRaises(AppError):
            self.engine.approve(ident)

    def test_busy_reset_and_parallel_approval_are_rejected(self):
        ident = self.propose()
        self.engine._operation.acquire()
        try:
            with self.assertRaises(AppError):
                self.engine.reset()
            with self.assertRaises(AppError):
                self.engine.approve(ident)
        finally:
            self.engine._operation.release()

    def test_plain_chat_approval_does_not_execute(self):
        self.propose()
        self.engine.complete = lambda *args: [{"type": "text", "text": "Use the approval control."}]
        self.engine.chat("Approved. Send it now.")
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.engine.pending()[0].state, "pending")

    def test_tool_roundtrip_and_no_automatic_memory(self):
        outputs = iter(
            [
                [{"type": "tool_use", "id": "t1", "name": "search_mail", "input": {"query": "newer_than:1d"}}],
                [{"type": "text", "text": "No matching messages."}],
            ]
        )
        self.engine.complete = lambda *args: next(outputs)
        self.assertEqual(self.engine.chat("Remember: check my mail"), "No matching messages.")
        self.assertEqual(len(self.engine.messages), 4)
        self.assertEqual(self.store.preferences, ["Be concise."])

    def test_scope_change_during_model_call_discards_result(self):
        def complete(*args):
            self.store.revision += 1
            return [{"type": "text", "text": "private stale result"}]

        self.engine.complete = complete
        with self.assertRaises(AppError):
            self.engine.chat("hello")
        self.assertEqual(self.engine.messages, [])

    def test_tools_and_context_are_bounded(self):
        self.engine.complete = lambda *args: [
            {"type": "tool_use", "id": "t" + str(len(args[3])), "name": "search_mail", "input": {"query": "x"}}
        ]
        with self.assertRaises(AppError):
            self.engine.chat("loop")
        self.engine.messages = [{"role": "user", "content": "x" * 180001}]
        with self.assertRaises(AppError):
            self.engine.chat("more")

    def test_interrupted_turn_keeps_complete_proposal_context(self):
        def complete(*args):
            if len(args[3]) == 1:
                return [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "propose_notion_append",
                        "input": {"text": "Original draft"},
                    }
                ]
            raise AppError("Provider unavailable")

        self.engine.complete = complete
        with self.assertRaises(AppError):
            self.engine.chat("Please append my original draft")
        self.assertEqual(len(self.engine.messages), 3)
        self.assertEqual(len(self.engine.pending()), 1)
        self.assertEqual(self.engine.messages[-1]["content"][0]["type"], "tool_result")
        self.engine.reject(self.engine.pending()[0].id)
        captured = []

        def revised(*args):
            captured.extend(args[3])
            return [{"type": "text", "text": "Revision ready"}]

        self.engine.complete = revised
        self.engine.chat("Make it shorter")
        self.assertIn("Original draft", str(captured))

    def test_identical_pending_proposals_are_deduplicated(self):
        first = self.propose()
        self.assertEqual(first, self.propose())
        self.assertEqual(len(self.engine.pending()), 1)


if __name__ == "__main__":
    unittest.main()
