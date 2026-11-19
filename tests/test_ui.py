"""Native Tk smoke tests with temporary private storage and no network access."""

import gc
import os
import tempfile
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from assistant_app.application.controller import ApplicationController
from assistant_app.engine import Engine
from assistant_app.frontend.app import AssistantUI
from assistant_app.storage import Store


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@unittest.skipUnless(os.name == "nt", "Native app targets Windows")
class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ["LOCALAPPDATA"])
        self.root = tk.Tk()
        self.root.withdraw()
        original_init = tk.Toplevel.__init__

        def hidden_init(window, *args, **kwargs):
            original_init(window, *args, **kwargs)
            window.withdraw()

        self.hidden_windows = patch.object(tk.Toplevel, "__init__", hidden_init)
        self.hidden_show = patch.object(tk.Toplevel, "deiconify")
        self.hidden_windows.start()
        self.show_mock = self.hidden_show.start()
        self.addCleanup(self.hidden_windows.stop)
        self.addCleanup(self.hidden_show.stop)
        store = Store(self.temp.name)
        controller = ApplicationController(store, Engine(store), Mock(), Mock())
        self.app = AssistantUI(self.root, controller=controller)
        self.root.update()

    def tearDown(self):
        # Cancel callbacks explicitly to avoid Tcl warnings between test interpreters.
        for ident in self.root.tk.call("after", "info"):
            self.root.after_cancel(ident)
        self.root.destroy()
        self.app = None
        self.root = None
        # Tk interpreters must be finalized on their creating thread, never by
        # cyclic garbage collection triggered by a later network/test worker.
        gc.collect()
        self.temp.cleanup()

    def button(self, name):
        return next(w for w in descendants(self.root) if w.winfo_class() == "TButton" and w.cget("text") == name)

    def pump(self):
        deadline = time.monotonic() + 3
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.busy)

    def test_launcher_and_chat(self):
        self.assertTrue(self.root.attributes("-topmost"))
        self.assertEqual(int(next(w for w in self.root.winfo_children() if isinstance(w, tk.Canvas)).cget("width")), 60)
        self.app.chat_window.withdraw()
        self.app.show()
        self.root.update()
        self.show_mock.assert_called()
        self.assertEqual(self.app.chat_window.state(), "withdrawn")

    def test_settings_controls_and_no_accidental_save(self):
        self.app.settings()
        self.root.update()
        self.assertEqual(self.button("Save settings").winfo_manager(), "pack")
        self.button("Cancel").invoke()
        self.assertEqual(self.app.store.revision, 0)

    def test_preference_save_cancel_delete_and_restart(self):
        self.app.preferences(draft="Be concise")
        self.button("Cancel").invoke()
        self.assertEqual(self.app.store.load_preferences(), [])
        self.app.preferences(draft="Be concise")
        self.button("Save preferences").invoke()
        self.assertEqual(Store(self.temp.name).load_preferences(), ["Be concise"])
        self.app.preferences()
        texts = [
            w
            for w in descendants(self.root)
            if isinstance(w, tk.Text) and w != self.app.transcript and w != self.app.input
        ]
        self.assertEqual(len(texts), 1)
        texts[0].delete("1.0", "end")
        self.button("Save preferences").invoke()
        self.assertEqual(Store(self.temp.name).load_preferences(), [])

    def test_failed_preference_save_does_not_claim_success(self):
        from assistant_app.domain.errors import AppError

        self.app.preferences(draft="Be concise")
        with (
            patch.object(self.app.controller, "save_preferences", side_effect=AppError("Storage unavailable")),
            patch("assistant_app.frontend.preferences.messagebox.showerror") as error,
        ):
            self.button("Save preferences").invoke()
            error.assert_called_once()
        self.assertNotIn("Explicit preferences saved", self.app.transcript.get("1.0", "end"))
        self.assertEqual(self.app.store.load_preferences(), [])

    def test_failed_preference_read_does_not_open_partial_modal(self):
        from assistant_app.domain.errors import AppError

        with (
            patch.object(self.app.controller, "load_preferences", side_effect=AppError("Storage unavailable")),
            patch("assistant_app.frontend.preferences.messagebox.showerror") as error,
            patch.object(self.app, "_dialog") as dialog,
        ):
            self.app.preferences()
        error.assert_called_once()
        dialog.assert_not_called()

    def test_worker_updates_chat_and_never_saves_transcript(self):
        self.app.engine.complete = lambda *args: [{"type": "text", "text": "Test answer"}]
        self.app.input.insert("1.0", "Private test question")
        self.app.send()
        deadline = time.monotonic() + 3
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.busy)
        self.assertIn("Test answer", self.app.transcript.get("1.0", "end"))
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_chat_keeps_only_essential_buttons_and_conditional_approval(self):
        labels = {w.cget("text") for w in descendants(self.app.chat_window) if w.winfo_class() == "TButton"}
        self.assertIn("Send", labels)
        self.assertIn("New chat", labels)
        self.assertFalse(labels & {"Preferences", "Remember last message", "Quit", "Summarize my inbox"})
        self.assertEqual(self.app.approval_view.panel.winfo_manager(), "")
        self.prepare_ui_proposal()
        self.button("Reject").invoke()
        self.assertEqual(self.app.approval_view.panel.winfo_manager(), "pack")
        self.assertIn("Rejected", self.app.proposal_list.get(0))

    def test_start_failure_restores_draft_without_claiming_it_was_sent(self):
        text = "A draft that must survive"
        self.app.input.insert("1.0", text)
        with patch("assistant_app.frontend.tasks.threading.Thread.start", side_effect=RuntimeError("private-detail")):
            self.app.send()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.input.get("1.0", "end-1c"), text)
        self.assertNotIn("YOU\n", self.app.transcript.get("1.0", "end"))
        self.assertNotIn("private-detail", self.app.transcript.get("1.0", "end"))

    def test_failed_chat_keeps_newer_draft(self):
        import threading

        from assistant_app.domain.errors import AppError

        release = threading.Event()

        def fail(*_):
            release.wait(3)
            raise AppError("Connection unavailable")

        self.app.engine.complete = fail
        self.app.input.insert("1.0", "First request")
        self.app.send()
        self.app.input.insert("1.0", "Next draft")
        release.set()
        self.pump()
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "Next draft")
        self.assertEqual(self.app.last_user, "First request")
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_failed_chat_does_not_resurrect_a_deliberately_cleared_draft(self):
        import threading

        from assistant_app.domain.errors import AppError

        release = threading.Event()

        def fail(*_):
            release.wait(3)
            raise AppError("Connection unavailable")

        self.app.engine.complete = fail
        self.app.input.insert("1.0", "First request")
        self.app.send()
        self.app.input.insert("1.0", "Changed my mind")
        self.app.input.delete("1.0", "end")
        release.set()
        self.pump()
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "")

    def test_failed_chat_restores_unchanged_empty_composer(self):
        from assistant_app.domain.errors import AppError

        self.app.engine.complete = Mock(side_effect=AppError("Connection unavailable"))
        self.app.input.insert("1.0", "Recover this request")
        self.app.send()
        self.pump()
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "Recover this request")
        self.app.engine.complete.assert_called_once()

    def test_draft_replacement_and_new_chat_require_confirmation(self):
        self.app.input.insert("1.0", "Unsent work")
        with patch("assistant_app.frontend.app.messagebox.askyesno", return_value=False):
            self.app._suggest("Another prompt")
            self.app.new_session()
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "Unsent work")
        self.app._configuration_changed()
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "Unsent work")

    def test_revision_cancel_preserves_draft_and_pending_proposal(self):
        self.prepare_ui_proposal()
        self.app.input.insert("1.0", "Unsent work")
        with patch("assistant_app.frontend.approval.messagebox.askyesno", return_value=False):
            self.button("Request changes").invoke()
        self.assertEqual(self.app.engine.pending()[0].state, "pending")
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "Unsent work")

    def test_new_session_clears_last_message_and_history(self):
        self.app.last_user = "old"
        self.app.engine.messages = [{"role": "user", "content": "old"}]
        with patch("assistant_app.frontend.app.messagebox.askyesno", return_value=True):
            self.app.new_session()
        self.assertEqual(self.app.last_user, "")
        self.assertEqual(self.app.engine.messages, [])

    def test_find_is_literal_bounded_and_cleared_with_session(self):
        view = self.app.chat_view
        view.reset_session()
        self.app._append("assistant", "ZIRCON.* and zircon.* and zircon12")
        view.show_find()
        view.find_query.set("zircon.*")
        self.assertEqual(len(view._find_matches), 2)
        view.find_next()
        self.assertEqual(view.find_status.get(), "1 of 2")
        view.find_next()
        view.find_next()
        self.assertEqual(view.find_status.get(), "1 of 2")
        self.app._append("assistant", " x" * 700)
        view.find_query.set("x")
        self.assertEqual(len(view._find_matches), 500)
        view.reset_session()
        self.assertEqual(view._find_matches, [])
        self.assertEqual(view.find_query.get(), "")
        self.assertEqual(view.find_bar.winfo_manager(), "")

    def test_copy_is_explicit_last_answer_only_and_reset_safe(self):
        view = self.app.chat_view
        with (
            patch.object(self.app.chat_window, "clipboard_clear") as clear,
            patch.object(self.app.chat_window, "clipboard_append") as copy,
        ):
            self.app._append("user", "Private user request")
            self.app._append("assistant", "Only this answer")
            copy.assert_not_called()
            view.copy_last_answer()
            clear.assert_called_once()
            copy.assert_called_once_with("Only this answer")
            view.reset_session()
            view.copy_last_answer()
            self.assertEqual(copy.call_count, 1)

    def test_quick_question_drafts_only_and_composer_is_bounded(self):
        view = self.app.chat_view
        with patch.object(self.app, "send") as send:
            view.quick_menu.invoke("Brief my day")
            send.assert_not_called()
        self.assertIn("read-only briefing", self.app.input.get("1.0", "end-1c"))
        self.assertEqual(self.app.engine.messages, [])
        self.app.input.insert("end", "\nline" * 20)
        view.capture_draft_revision()
        self.assertEqual(int(self.app.input.cget("height")), 6)
        self.assertIn("12,000", view.character_count.get())
        self.app.input.delete("1.0", "end")
        view.capture_draft_revision()
        self.assertEqual(int(self.app.input.cget("height")), 2)
        with patch.object(self.app, "send") as send:
            self.assertEqual(view._send_shortcut(), "break")
            send.assert_called_once()
        self.assertEqual(self.app.input.bind("<Return>"), "")

    def test_important_mail_button_submits_summary_and_preserves_draft(self):
        captured = []

        def answer(settings, store, system, messages, tools):
            captured.append(messages[-1]["content"])
            return [{"type": "text", "text": "Important mail summary"}]

        self.app.engine.complete = answer
        self.app.input.insert("1.0", "My unsent draft")
        self.button("Important mail today").invoke()
        self.pump()
        self.assertEqual(len(captured), 1)
        self.assertIn("is:important after:", captured[0])
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "My unsent draft")
        self.assertIn("Important mail summary", self.app.transcript.get("1.0", "end"))
        self.assertNotIn("is:important after:", self.app.transcript.get("1.0", "end"))
        self.assertNotIn("YOU\n", self.app.transcript.get("1.0", "end"))
        self.assertEqual(self.app.last_user, "")

    def test_failed_mail_shortcut_never_restores_internal_prompt(self):
        from assistant_app.domain.errors import AppError

        self.app.last_user = "Previous typed message"
        self.app.engine.complete = Mock(side_effect=AppError("Connection unavailable"))
        self.button("Important mail today").invoke()
        self.pump()
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "")
        self.assertEqual(self.app.last_user, "Previous typed message")
        self.assertNotIn("is:important after:", self.app.transcript.get("1.0", "end"))
        self.app.engine.complete.assert_called_once()

    def test_calendar_button_uses_direct_read_not_chat_and_keeps_draft(self):
        api = Mock()
        api.list_events.return_value = {"events": []}
        self.app.engine.integrations = api
        self.app.engine.complete = Mock(side_effect=AssertionError("Calendar must not call AI"))
        before = self.app.transcript.get("1.0", "end")
        self.app.input.insert("1.0", "My unsent draft")
        with patch.object(self.app.calendar_view, "show") as show:
            self.button("Calendar: today & tomorrow").invoke()
            self.pump()
        show.assert_called_once()
        self.assertEqual(len(show.call_args.args[0].days), 2)
        self.app.engine.complete.assert_not_called()
        self.assertEqual(self.app.engine.messages, [])
        self.assertEqual(self.app.transcript.get("1.0", "end"), before)
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "My unsent draft")

    def test_calendar_grid_details_and_reset_lifecycle(self):
        from datetime import date, timezone

        from assistant_app.application.calendar import calendar_snapshot

        snapshot = calendar_snapshot(
            {
                "events": [
                    {
                        "summary": "Synthetic meeting",
                        "start": {"dateTime": "2000-01-15T09:00:00Z"},
                        "end": {"dateTime": "2000-01-15T10:00:00Z"},
                    },
                    {"summary": "All day", "start": {"date": "2000-01-16"}, "end": {"date": "2000-01-17"}},
                ]
            },
            date(2000, 1, 15),
            zone=timezone.utc,
        )
        window = self.app.calendar_view.show(snapshot)
        self.root.update()
        self.assertEqual(window.title(), "Your calendar")
        self.assertTrue(self.app.calendar_view.canvas.find_all())
        registered = len(self.app.calendar_view.canvas._tclCommands)
        for _ in range(10):
            self.app.calendar_view._draw()
        self.assertEqual(len(self.app.calendar_view.canvas._tclCommands), registered)
        self.app.calendar_view._show_detail(snapshot.days[0].entries[0])
        texts = [w for w in descendants(window) if isinstance(w, tk.Text)]
        self.assertIn("Synthetic meeting", texts[0].get("1.0", "end"))
        self.assertEqual(str(texts[0].cget("state")), "disabled")
        from dataclasses import replace

        all_day = snapshot.days[1].entries[0]
        for entry in (all_day, replace(all_day, continues_before=True, continues_after=True)):
            self.app.calendar_view._show_detail(entry, snapshot.days[1].date)
            texts = [w for w in descendants(window) if isinstance(w, tk.Text)]
            detail = texts[0].get("1.0", "end")
            self.assertIn("16 January 2000", detail)
            self.assertNotIn("Time unavailable", detail)
            self.assertNotIn("clipped", detail)
        self.app.new_session()
        self.assertIsNone(self.app.calendar_view.window)
        self.assertEqual(self.app.calendar_view._event_details, {})
        self.assertFalse(window.winfo_exists())

    def test_unknown_write_remains_visible_and_inspectable(self):
        from assistant_app.domain.errors import AppError

        self.prepare_ui_proposal()
        self.app.engine.integrations.execute = Mock(side_effect=AppError("Inspect the destination", uncertain=True))
        self.button("Approve this change").invoke()
        self.pump()
        self.assertEqual(self.app.approval_view.panel.winfo_manager(), "pack")
        self.assertIn("Outcome unknown", self.app.proposal_list.get(0))
        self.app.preview()
        labels = {w.cget("text") for w in descendants(self.root) if w.winfo_class() == "TButton"}
        self.assertNotIn("Approve this change", labels)

    def test_connection_controls_preserve_unsaved_provider_draft(self):
        self.app.settings()
        combo = next(w for w in descendants(self.root) if w.winfo_class() == "TCombobox")
        combo.set("ollama")
        with (
            patch("assistant_app.frontend.settings.messagebox.showinfo") as info,
            patch.object(self.app, "_run") as run,
        ):
            self.button("Choose calendar…").invoke()
            self.button("Connect Google…").invoke()
            self.assertEqual(info.call_count, 2)
            run.assert_not_called()
        self.assertEqual(combo.get(), "ollama")
        self.assertEqual(self.app.store.load_settings().provider, "anthropic")

    def test_local_endpoint_trailing_slash_is_normalized(self):
        self.app.settings()
        combo = next(w for w in descendants(self.root) if w.winfo_class() == "TCombobox")
        combo.set("ollama")
        entries = [w for w in descendants(self.root) if w.winfo_class() == "TEntry"]
        # Model, Anthropic key, local URL, Notion token, page ID.
        entries[0].delete(0, "end")
        entries[0].insert(0, "test-local-model")
        entries[2].delete(0, "end")
        entries[2].insert(0, "http://127.0.0.1:11434/")
        self.button("Save settings").invoke()
        self.assertEqual(self.app.store.load_settings().local_url, "http://127.0.0.1:11434")

    def prepare_ui_proposal(self):
        writes = []
        self.app.engine.integrations = SimpleNamespace(
            prepare=lambda action, args: {
                "action": action,
                "arguments": args,
                "destination": {"page_id": "selected-page"},
                "after": {"append_text": args["text"]},
                "credential_fingerprint": "internal-only",
            },
            execute=lambda action, prepared: writes.append(prepared) or {"completed": True},
        )
        self.app.engine._dispatch("propose_notion_append", {"text": "Exact draft text"})
        self.app._refresh_proposals()
        self.app.proposal_list.selection_set(0)
        self.app.preview()
        return writes

    def test_preview_and_ui_approval_execute_exactly_once(self):
        writes = self.prepare_ui_proposal()
        previews = [
            w
            for w in descendants(self.root)
            if isinstance(w, tk.Text) and w not in (self.app.transcript, self.app.input)
        ]
        content = previews[0].get("1.0", "end")
        self.assertIn("Exact draft text", content)
        self.assertIn("selected-page", content)
        self.assertNotIn("internal-only", content)
        self.assertEqual(writes, [])
        self.button("Approve this change").invoke()
        self.pump()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["arguments"]["text"], "Exact draft text")

    def test_request_changes_rejects_without_writing(self):
        writes = self.prepare_ui_proposal()
        self.button("Request changes").invoke()
        self.assertEqual(writes, [])
        self.assertEqual(self.app.engine.pending()[0].state, "rejected")
        self.assertIn("Please revise", self.app.input.get("1.0", "end"))

    def test_google_callback_resets_only_after_controller_persists(self):
        from dataclasses import replace

        self.app.store.save_settings(replace(self.app.controller.load_settings(), calendar_id="old-calendar"))
        self.app.controller._connect_google.return_value = "synthetic@example.test"
        self.app.last_user = "old conversation"
        generation = self.app.engine.session
        self.app.settings()
        with (
            patch("assistant_app.frontend.settings.filedialog.askopenfilename", return_value="synthetic.json"),
            patch.object(self.app.store, "save_settings", wraps=self.app.store.save_settings) as save,
        ):
            self.button("Connect Google\u2026").invoke()
            self.app._worker_thread.join(timeout=3)
            self.assertEqual(self.app.engine.session, generation)
            self.pump()
            save.assert_called_once()
        self.assertNotEqual(self.app.engine.session, generation)
        self.assertEqual(self.app.last_user, "")
        self.assertEqual(self.app.controller.load_settings().calendar_id, "")
        self.assertEqual(self.app.controller.load_settings().gmail_account, "synthetic@example.test")
        self.assertIn("Google account connected", self.app.transcript.get("1.0", "end"))
        self.assertIsNone(self.app._worker_thread)

    def test_stale_worker_result_is_discarded_and_reaped(self):
        callback = Mock()
        self.app._run(lambda: "old result", callback)
        self.app._worker_thread.join(timeout=3)
        self.app.engine.reset()
        self.pump()
        callback.assert_not_called()
        self.assertIsNone(self.app._worker_thread)
        self.assertEqual(str(self.app.send_button.cget("state")), "normal")

    def test_worker_errors_redact_private_diagnostics(self):
        def fail():
            raise RuntimeError("private-token-sentinel")

        self.app._run(fail)
        self.pump()
        content = self.app.transcript.get("1.0", "end")
        self.assertNotIn("private-token-sentinel", content)
        self.assertIn("No private diagnostic data was logged", content)
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_busy_worker_blocks_configuration_reset_and_quit(self):
        import threading

        release = threading.Event()
        self.app._run(lambda: release.wait(3))
        generation = self.app.engine.session
        try:
            with patch("assistant_app.frontend.app.messagebox.showinfo") as info:
                self.app.settings()
                self.app.new_session()
                self.app.quit()
                self.assertEqual(info.call_count, 3)
            self.assertEqual(self.app.engine.session, generation)
            self.assertTrue(self.root.winfo_exists())
        finally:
            release.set()
            self.pump()

    def test_calendar_and_disconnect_use_controller(self):
        self.app._calendar_picker([{"id": "synthetic-calendar", "summary": "Test"}])
        choices = next(w for w in descendants(self.root) if isinstance(w, tk.Listbox) and w != self.app.proposal_list)
        choices.selection_set(0)
        with patch.object(self.app.controller, "select_calendar", wraps=self.app.controller.select_calendar) as select:
            self.button("Use selected calendar").invoke()
            select.assert_called_once_with("synthetic-calendar")
        self.app.settings()
        with (
            patch("assistant_app.frontend.settings.messagebox.askyesno", return_value=True),
            patch.object(self.app.controller, "disconnect", wraps=self.app.controller.disconnect) as disconnect,
        ):
            self.button("Disconnect all").invoke()
            disconnect.assert_called_once_with()
        self.assertEqual(self.app.controller.load_settings().calendar_id, "")


class FrontendBoundaryTests(unittest.TestCase):
    def test_frontend_has_no_infrastructure_or_compatibility_imports(self):
        import ast
        from pathlib import Path

        import assistant_app.frontend as frontend

        forbidden = {"infrastructure", "storage", "oauth", "net", "providers", "integrations"}
        for path in Path(frontend.__file__).parent.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(set((node.module or "").split(".")) & forbidden, path.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(set(alias.name.split(".")) & forbidden, path.name)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    owner = node.func.value
                    self.assertFalse(isinstance(owner, ast.Attribute) and owner.attr == "store", path.name)


if __name__ == "__main__":
    unittest.main()
