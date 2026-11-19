"""ChatView composition component."""

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from .theme import ACCENT, BG, BORDER, MUTED, PANEL, SOFT, TEXT, draw_soccer_ball


class ChatView:
    def __init__(self, app):
        self.app = app
        self._busy = False
        self._busy_label = "Working"
        self.last_answer = ""
        self._draft_revision = 0
        self._find_matches = []
        self._find_index = -1
        self._find_visible = False

    def _build_chat(self):
        self.chat_window = tk.Toplevel(self.app.root)
        w = self.chat_window
        w.title("Personal Assistant")
        width = min(800, max(1, w.winfo_screenwidth() - 40))
        height = min(760, max(1, w.winfo_screenheight() - 80))
        w.geometry(f"{width}x{height}")
        w.minsize(min(560, width), min(480, height))
        w.configure(bg=BG)
        w.protocol("WM_DELETE_WINDOW", w.withdraw)
        header = tk.Frame(w, bg=BG, padx=18, pady=12)
        header.pack(fill="x")
        avatar = tk.Canvas(header, width=40, height=40, bg=BG, highlightthickness=0)
        draw_soccer_ball(avatar, 40)
        avatar.pack(side="left", padx=(0, 14))
        title = tk.Frame(header, bg=BG)
        tk.Label(title, text="Personal assistant", bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self.menu_button = ttk.Menubutton(header, text="Menu")
        self.menu_button.pack(side="right")
        self.new_chat_button = ttk.Button(header, text="New chat", command=self.app.new_session)
        self.new_chat_button.pack(side="right", padx=(8, 8))
        # Reserve actions before the expandable title on small/high-DPI screens.
        title.pack(side="left", fill="x", expand=True)
        self.menu = tk.Menu(self.menu_button, tearoff=False, postcommand=self._prepare_menu)
        self.menu_button.configure(menu=self.menu)
        for label, callback in (
            ("Settings", self.app.settings),
            ("Preferences", self.app.preferences),
            ("Remember last message", self.app.remember),
        ):
            self.menu.add_command(label=label, command=callback)
        self.menu.add_separator()
        self.quick_menu = tk.Menu(self.menu, tearoff=False)
        for label, prompt in (
            (
                "Brief my day",
                "Give me a read-only briefing of today's calendar and unread emails. Do not make changes.",
            ),
            ("Summarize my inbox", "Summarize my unread emails from today."),
            ("Check my calendar", "What appointments do I have today?"),
            ("Read my Notion page", "Summarize my selected Notion page."),
        ):
            self.quick_menu.add_command(label=label, command=lambda text=prompt: self._suggest(text))
        self.menu.add_cascade(label="Quick questions", menu=self.quick_menu)
        self.menu.add_command(label="Copy last answer", command=self.copy_last_answer)
        self.menu.add_command(label="Edit last request", command=self.edit_last_request)
        self.menu.add_command(label="Find", accelerator="Ctrl+F", command=self.show_find)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.app.quit)
        w.bind("<Control-f>", lambda event: self.show_find())
        w.bind("<Control-F>", lambda event: self.show_find())
        shortcuts = tk.Frame(w, bg=BG)
        shortcuts.pack(fill="x", padx=24, pady=(10, 0))
        shortcuts.columnconfigure((0, 1), weight=1, uniform="shortcut")
        self.mail_button = ttk.Button(shortcuts, text="Important mail today", command=self.app.summarize_important_mail)
        self.mail_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.calendar_button = ttk.Button(shortcuts, text="Calendar: today & tomorrow", command=self.app.show_calendar)
        self.calendar_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        # Reserve the lower controls before the expanding conversation, so the
        # composer and approval controls remain reachable in a smaller window.
        footer = tk.Frame(w, bg=BG)
        footer.pack(side="bottom", fill="x", padx=24, pady=(0, 16))
        self.status = tk.StringVar(value="Ready · Ctrl+Enter to send · Close chat to tuck it away")
        self.status_label = tk.Label(
            footer,
            textvariable=self.status,
            bg=BG,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=760,
            font=("Segoe UI", 9),
        )
        self.status_label.pack(side="bottom", fill="x", pady=(8, 0))
        self.status_label.bind(
            "<Configure>", lambda event: self.status_label.configure(wraplength=max(200, event.width))
        )
        composer = tk.Frame(footer, bg=PANEL, highlightbackground=BORDER, highlightcolor=ACCENT, highlightthickness=1)
        composer.pack(side="bottom", fill="x", pady=(12, 0))
        tk.Label(composer, text="What can I help you with?", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=14, pady=(10, 0)
        )
        entry_row = tk.Frame(composer, bg=PANEL)
        entry_row.pack(fill="x", padx=6, pady=(0, 6))
        self.input = tk.Text(
            entry_row,
            width=1,
            height=2,
            wrap="word",
            bg=PANEL,
            fg=TEXT,
            insertbackground=ACCENT,
            selectbackground=SOFT,
            font=("Segoe UI", 11),
            relief="flat",
            padx=9,
            pady=8,
        )
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Control-Return>", self._send_shortcut)
        self.input.bind("<<Modified>>", self._input_changed)
        self.input.bind("<Configure>", self._resize_input)
        self.input.bind("<FocusIn>", lambda _: composer.configure(highlightbackground=ACCENT))
        self.input.bind("<FocusOut>", lambda _: composer.configure(highlightbackground=BORDER))
        self.send_button = ttk.Button(entry_row, text="Send", style="Accent.TButton", command=self.app.send)
        self.send_button.pack(side="right", padx=8)
        self.app.approval_view.build(footer)
        self.character_count = tk.StringVar(value="")
        self.character_label = tk.Label(
            composer, textvariable=self.character_count, bg=PANEL, fg=MUTED, anchor="e", font=("Segoe UI", 9)
        )
        self.character_label.pack(fill="x", padx=14, pady=(0, 6))
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.find_bar = tk.Frame(w, bg=BG)
        self.find_query = tk.StringVar(value="")
        self.find_status = tk.StringVar(value="")
        tk.Label(self.find_bar, text="Find", bg=BG, fg=MUTED).pack(side="left", padx=(0, 6))
        self.find_entry = ttk.Entry(
            self.find_bar,
            textvariable=self.find_query,
            width=20,
            validate="key",
            validatecommand=(w.register(lambda value: len(value) <= 256), "%P"),
        )
        self.find_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(self.find_bar, text="Next", command=self.find_next).pack(side="left", padx=6)
        ttk.Button(self.find_bar, text="Close", command=self.hide_find).pack(side="right")
        tk.Label(self.find_bar, textvariable=self.find_status, bg=BG, fg=MUTED).pack(side="left", padx=6)
        self.find_entry.bind("<Return>", lambda event: self.find_next())
        self.find_entry.bind("<Escape>", lambda event: self.hide_find())
        self.find_query.trace_add("write", self._search_changed)
        self.transcript = ScrolledText(
            w,
            wrap="word",
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=SOFT,
            font=("Segoe UI", 11),
            relief="flat",
            borderwidth=0,
            padx=22,
            pady=18,
            spacing1=4,
            spacing3=6,
            state="disabled",
        )
        self.transcript.pack(fill="both", expand=True, padx=24, pady=(14, 10))
        self.transcript.vbar.configure(
            bg=BORDER, troughcolor=PANEL, activebackground=SOFT, relief="flat", borderwidth=0
        )
        for role, color in (("user", ACCENT), ("assistant", TEXT), ("app", MUTED)):
            self.transcript.tag_config(role, foreground=color, font=("Segoe UI", 10, "bold"), spacing1=12)
        self.transcript.tag_config(
            "user_body", background=SOFT, lmargin1=12, lmargin2=12, rmargin=12, spacing1=8, spacing3=10
        )
        self.transcript.tag_config("app_body", foreground=MUTED, font=("Segoe UI", 10))
        self.transcript.tag_config("find_match", background="#fff0a6", foreground="#202020")
        self.transcript.tag_config("find_current", background="#f5c65d", foreground="#202020")
        self._append(
            "assistant",
            "Hello, what would you like to get done?\n\n"
            "I can help you catch up on email, check your day, or find something in Notion. "
            "Connect your accounts in Settings to get started.",
        )

        # The greeting is not a model answer eligible for copying.
        self.last_answer = ""
        self._update_controls()
        self.capture_draft_revision()

    def _suggest(self, text):
        return self.app.replace_draft(text)

    def edit_last_request(self):
        if self.app.last_user:
            return self.app.replace_draft(self.app.last_user)
        return False

    def copy_last_answer(self):
        if self.last_answer:
            try:
                self.chat_window.clipboard_clear()
                self.chat_window.clipboard_append(self.last_answer)
                if not self._busy:
                    self.status.set("Last answer copied to your clipboard.")
            except tk.TclError:
                self.status.set("Clipboard unavailable. Select and copy the answer manually.")

    def _send_shortcut(self, event=None):
        self.app.send()
        return "break"

    def capture_draft_revision(self):
        self._input_changed()
        return self._draft_revision

    def _input_changed(self, event=None):
        if self.input.edit_modified():
            self._draft_revision += 1
            self.input.edit_modified(False)
        self._update_controls()

    def _update_controls(self):
        if hasattr(self, "character_count"):
            count = len(self.input.get("1.0", "end-1c"))
            limit = self.app.engine.limits.max_message_chars
            self.character_count.set(f"{count:,} / {limit:,}" + (" - Message too long" if count > limit else ""))
        self._resize_input()

    def _resize_input(self, event=None):
        # Count wrapped display lines; no timers or idle callbacks are needed.
        lines = self.input.count("1.0", "end-1c", "displaylines")
        height = max(2, min(6, (lines[0] if lines else 0) + 1))
        if int(self.input.cget("height")) != height:
            self.input.configure(height=height)

    def _prepare_menu(self):
        busy = self._busy or self.app.busy
        for label in ("Settings", "Preferences", "Quit", "Quick questions"):
            self.menu.entryconfigure(label, state="disabled" if busy else "normal")
        for label in ("Remember last message", "Edit last request"):
            self.menu.entryconfigure(label, state="normal" if self.app.last_user and not busy else "disabled")
        self.menu.entryconfigure("Copy last answer", state="normal" if self.last_answer else "disabled")
        self.menu.entryconfigure("Find", state="normal" if self.transcript.get("1.0", "end-1c") else "disabled")

    def set_busy(self, busy, label=""):
        was_busy = self._busy
        self._busy = bool(busy)
        self.send_button.configure(state="disabled" if busy else "normal")
        self.new_chat_button.configure(state="disabled" if busy else "normal")
        self.mail_button.configure(state="disabled" if busy else "normal")
        self.calendar_button.configure(state="disabled" if busy else "normal")
        if busy:
            self._busy_label = label or "Working"
            self.progress.pack(fill="x", pady=(6, 0))
            if not was_busy:
                self.progress.start(12)
            self.update_busy(0)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.status.set("Ready - Ctrl+Enter to send")
        self._prepare_menu()

    def update_busy(self, elapsed):
        if self._busy:
            self.status.set(f"{self._busy_label} - {max(0, int(elapsed))}s - You can keep drafting")

    def close(self):
        self.progress.stop()

    def reset_session(self):
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self.last_answer = ""
        self.hide_find()
        self.find_query.set("")
        self.find_status.set("")
        self._find_matches = []
        self._find_index = -1

    def show_find(self):
        self._find_visible = True
        self.find_bar.pack(fill="x", padx=24, pady=(8, 0), before=self.transcript)
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")
        self._search_changed()
        return "break"

    def hide_find(self):
        self._find_visible = False
        self.find_bar.pack_forget()
        self.transcript.tag_remove("find_match", "1.0", "end")
        self.transcript.tag_remove("find_current", "1.0", "end")
        self.find_status.set("")
        self.input.focus_set()
        return "break"

    def _search_changed(self, *args):
        self.transcript.tag_remove("find_match", "1.0", "end")
        self.transcript.tag_remove("find_current", "1.0", "end")
        self._find_matches = []
        self._find_index = -1
        query = self.find_query.get()
        if not self._find_visible or not query:
            self.find_status.set("")
            return
        if len(query) > 256:
            self.find_status.set("Use up to 256 characters")
            return
        start = "1.0"
        length = tk.IntVar(master=self.chat_window)
        while len(self._find_matches) < 500:
            found = self.transcript.search(query, start, stopindex="end-1c", nocase=True, regexp=False, count=length)
            if not found or length.get() <= 0:
                break
            end = self.transcript.index(f"{found}+{length.get()}c")
            self._find_matches.append((found, end))
            self.transcript.tag_add("find_match", found, end)
            start = end
        self.find_status.set(
            "First 500 matches"
            if len(self._find_matches) == 500
            else f"{len(self._find_matches)} matches"
            if self._find_matches
            else "No matches"
        )

    def find_next(self):
        if self._find_matches:
            self._find_index = (self._find_index + 1) % len(self._find_matches)
            start, end = self._find_matches[self._find_index]
            self.transcript.tag_remove("find_current", "1.0", "end")
            self.transcript.tag_add("find_current", start, end)
            self.transcript.tag_raise("find_current")
            self.transcript.see(start)
            self.find_status.set(f"{self._find_index + 1} of {len(self._find_matches)}")
        return "break"

    def show(self):
        self.chat_window.deiconify()
        self.chat_window.lift()
        self.input.focus_set()

    def _append(self, role, text):
        at_bottom = self.transcript.yview()[1] >= 0.999
        if role == "assistant":
            self.last_answer = text
        self.transcript.configure(state="normal")
        self.transcript.insert(
            "end", {"user": "YOU", "assistant": "YOUR ASSISTANT", "app": "UPDATE"}[role] + "\n", role
        )
        self.transcript.insert("end", text + "\n\n", role + "_body")
        self.transcript.configure(state="disabled")
        if at_bottom or role == "user":
            self.transcript.see("end")
        if self._find_visible:
            self._search_changed()
