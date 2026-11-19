"""PreferencesView composition component."""

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from assistant_app.domain.errors import AppError

from .theme import BG, MUTED, PANEL, TEXT


class PreferencesView:
    def __init__(self, app):
        self.app = app

    def remember(self):
        if not self.app.last_user:
            messagebox.showinfo(
                "Remember a preference",
                "Send a message first, or add a preference in Preferences.",
                parent=self.app.chat_window,
            )
            return
        self.app.preferences(draft=self.app.last_user)

    def preferences(self, draft=None):
        if not self.app._idle():
            return
        try:
            content = self.app.controller.load_preferences()
        except AppError as exc:
            messagebox.showerror("Preferences unavailable", str(exc), parent=self.app.chat_window)
            return
        window = self.app._dialog("Explicit preferences", "660x520")
        tk.Label(
            window, text="Only save rules you want remembered", bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))
        tk.Label(
            window,
            text="One preference per line. Edit or remove lines to change memory.\n"
            "Saved preferences are included in your chosen AI's context, including Anthropic in cloud mode.\n"
            "Nothing is saved until you click Save.",
            bg=BG,
            fg=MUTED,
            justify="left",
            wraplength=610,
        ).pack(anchor="w", padx=18)
        editor = ScrolledText(window, bg=PANEL, fg=TEXT, insertbackground=TEXT, wrap="word", font=("Segoe UI", 11))
        if draft:
            content.append(draft.replace("\n", " "))
        editor.insert("1.0", "\n".join(content))

        def save():
            try:
                preferences = [line.strip() for line in editor.get("1.0", "end").splitlines() if line.strip()]
                self.app.controller.save_preferences(preferences)
                window.destroy()
                self.app._append("app", "Explicit preferences saved. No conversation history was saved.")
            except AppError as exc:
                messagebox.showerror("Preferences", str(exc), parent=window)

        controls = tk.Frame(window, bg=BG)
        controls.pack(side="bottom", fill="x", padx=18, pady=(0, 16))
        editor.pack(fill="both", expand=True, padx=18, pady=16)
        ttk.Button(controls, text="Save preferences", command=save).pack(side="left")
        ttk.Button(controls, text="Cancel", command=window.destroy).pack(side="right")
