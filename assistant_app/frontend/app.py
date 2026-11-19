"""Tk application orchestrator; configuration belongs to the application controller."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from assistant_app.application.calendar import important_mail_prompt
from assistant_app.application.controller import ApplicationController
from assistant_app.domain.errors import AppError

from .approval import ApprovalView
from .calendar_view import CalendarView
from .chat import ChatView
from .launcher import LauncherView
from .preferences import PreferencesView
from .settings import SettingsView
from .tasks import TaskRunner
from .theme import BG, configure_theme


class AssistantUI:
    def __init__(self, root, store=None, *, controller: ApplicationController | None = None):
        if controller is None:
            from assistant_app.bootstrap import create_controller

            controller = create_controller(store)
        self.root = root
        self.controller = controller
        self.last_user = ""
        configure_theme(root)
        self.tasks = TaskRunner(self)
        self.approval_view = ApprovalView(self)
        self.settings_view = SettingsView(self)
        self.preferences_view = PreferencesView(self)
        self.launcher_view = LauncherView(self)
        self.chat_view = ChatView(self)
        self.calendar_view = CalendarView(self)
        self.chat_view._build_chat()
        self.tasks.poll_id = root.after(100, self._poll)
        self.refresh_id = root.after(1000, self._refresh_periodically)
        self.show()

    @property
    def engine(self):
        return self.controller.engine

    @property
    def store(self):
        return self.controller.store

    @property
    def chat_window(self):
        return self.chat_view.chat_window

    @property
    def status(self):
        return self.chat_view.status

    @property
    def status_label(self):
        return self.chat_view.status_label

    @property
    def input(self):
        return self.chat_view.input

    @property
    def send_button(self):
        return self.chat_view.send_button

    @property
    def transcript(self):
        return self.chat_view.transcript

    @property
    def proposal_list(self):
        return self.approval_view.proposal_list

    @property
    def proposal_heading(self):
        return self.approval_view.proposal_heading

    @property
    def proposal_hint(self):
        return self.approval_view.proposal_hint

    @property
    def proposal_ids(self):
        return self.approval_view.proposal_ids

    @property
    def busy(self):
        return self.tasks.busy

    @property
    def events(self):
        return self.tasks.events

    @property
    def _worker_thread(self):
        return self.tasks._worker_thread

    @property
    def menu(self):
        return self.launcher_view.menu

    def _idle(self):
        if self.busy:
            messagebox.showinfo(
                "Operation in progress",
                "Please wait for the current operation. "
                "An already-dispatched remote write cannot be recalled. Requests have time limits.",
                parent=self.chat_window,
            )
            return False
        return True

    def _dialog(self, title, size="660x620"):
        window = tk.Toplevel(self.chat_window)
        window.title(title)
        requested_width, requested_height = (int(value) for value in size.split("x"))
        width = min(requested_width, max(320, self.root.winfo_screenwidth() - 80))
        height = min(requested_height, max(300, self.root.winfo_screenheight() - 120))
        window.geometry(f"{width}x{height}")
        window.transient(self.chat_window)
        window.configure(bg=BG)
        window.grab_set()
        return window

    def replace_draft(self, text, *, confirm=True):
        """Replace an unsent draft only after consent; never submit it."""
        if not self._idle():
            return False
        current = self.input.get("1.0", "end-1c")
        if confirm and current.strip() and current != text:
            if not messagebox.askyesno(
                "Replace your draft?",
                "Replace the unsent message in the composer?",
                parent=self.chat_window,
            ):
                return False
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.input.edit_modified(True)
        self.input.focus_set()
        return True

    def summarize_important_mail(self):
        return self.send(text_override=important_mail_prompt())

    def show_calendar(self):
        if not self._idle():
            return
        return self._run(
            self.controller.calendar_today_tomorrow, self.calendar_view.show, "Loading today and tomorrow..."
        )

    def send(self, text_override=None):
        if self.busy:
            return "break"
        draft = self.input.get("1.0", "end-1c") if text_override is None else text_override
        text = draft.strip()
        if not text:
            return "break"
        maximum = self.engine.limits.max_message_chars
        if len(text) > maximum:
            messagebox.showerror(
                "Message too long", f"Please keep messages under {maximum} characters.", parent=self.chat_window
            )
            return "break"
        if text_override is None:
            self.last_user = draft
            self.input.delete("1.0", "end")
        draft_revision = self.chat_view.capture_draft_revision()

        def recover(_error):
            if text_override is not None:
                self.status.set("Summary unavailable. Your draft is unchanged; use Important mail today to try again.")
                return
            # A user can compose the next message while a request runs. Never
            # replace that newer draft, or retry a request/remote write implicitly.
            if self.chat_view.capture_draft_revision() == draft_revision and not self.input.get("1.0", "end-1c"):
                self.input.insert("1.0", draft)
                self.input.edit_modified(True)
                self.status.set("Request restored as a draft. Review any proposed changes before sending again.")
            else:
                self.status.set(
                    "Request failed. Your current draft is safe; Menu > Edit last request recovers the earlier one."
                )

        started = self._run(
            lambda: self.engine.chat(text),
            lambda result: self._append("assistant", result),
            "Summarizing important mail..." if text_override is not None else "Reading and thinking...",
            on_error=recover,
        )
        if started and text_override is None:
            self._append("user", text)
        return "break"

    def new_session(self):
        if not self._idle():
            return
        if self.engine.messages or self.engine.pending() or self.input.get("1.0", "end-1c").strip():
            if not messagebox.askyesno(
                "Start a new session?",
                "Discard this conversation, unsent draft and all proposals? "
                "Saved preferences remain. Inspect any unknown write outcome before retrying it.",
                parent=self.chat_window,
            ):
                return
        try:
            self.engine.reset()
        except AppError as exc:
            self._append("app", str(exc))
            return
        self.last_user = ""
        self.input.delete("1.0", "end")
        self.chat_view.reset_session()
        self.calendar_view.close()
        self._refresh_proposals()
        self._append(
            "app", "New session. Previous conversation and proposals discarded; explicit preferences retained."
        )

    def _configuration_changed(self):
        self.engine.reset()
        self.last_user = ""
        # Provider/account changes clear conversation scope, not unsent work.
        self.chat_view.reset_session()
        self.calendar_view.close()
        self._refresh_proposals()
        self._append("app", "Settings updated. Started a fresh session and cleared prior proposals.")

    def _refresh_periodically(self):
        try:
            if not self.busy:
                self._refresh_proposals()
        except AppError:
            self.status.set("Changes could not be refreshed. Check Settings.")
        finally:
            self.refresh_id = self.root.after(1000, self._refresh_periodically)

    def quit(self):
        if not self._idle():
            return
        if not self.tasks.close():
            return
        self.chat_view.close()
        self.calendar_view.close()
        self.root.after_cancel(self.refresh_id)
        self.root.destroy()

    def _run(self, operation, done=None, label="Working...", on_error=None):
        return self.tasks._run(operation, done, label, on_error=on_error)

    def _poll(self):
        return self.tasks._poll()

    def _append(self, role, text):
        return self.chat_view._append(role, text)

    def _suggest(self, text):
        return self.chat_view._suggest(text)

    def show(self):
        return self.chat_view.show()

    def _refresh_proposals(self):
        return self.approval_view._refresh_proposals()

    def preview(self):
        return self.approval_view.preview()

    def settings(self):
        try:
            return self.settings_view.settings()
        except AppError as exc:
            messagebox.showerror("Settings unavailable", str(exc), parent=self.chat_window)

    def _calendar_picker(self, calendars):
        return self.settings_view._calendar_picker(calendars)

    def preferences(self, draft=None):
        return self.preferences_view.preferences(draft)

    def remember(self):
        return self.preferences_view.remember()


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        ttk.Style(root).theme_use("clam")
        AssistantUI(root)
        root.deiconify()
        root.mainloop()
    except AppError as exc:
        messagebox.showerror("Personal Assistant", str(exc), parent=root)
        root.destroy()
