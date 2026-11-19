"""SettingsView composition component."""

import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, messagebox, ttk

from assistant_app.domain.errors import AppError

from .theme import BG, MUTED, PANEL, TEXT


class SettingsView:
    def __init__(self, app):
        self.app = app

    def settings(self):
        if not self.app._idle():
            return
        cfg = self.app.controller.load_settings()
        window = self.app._dialog("Settings · connections and privacy", "740x780")
        viewport = tk.Canvas(window, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=viewport.yview)
        scrollbar.pack(side="right", fill="y")
        viewport.pack(side="left", fill="both", expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        form = tk.Frame(viewport, bg=BG, padx=20, pady=16)
        form_id = viewport.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(form_id, width=event.width))

        def scroll_settings(event):
            if event.delta and event.widget.winfo_class() != "TCombobox":
                steps = max(1, abs(event.delta) // 120)
                viewport.yview_scroll(-steps if event.delta > 0 else steps, "units")
                return "break"

        def reveal_setting(event):
            if event.widget.winfo_class() not in ("TEntry", "TCombobox", "TButton"):
                return
            top = event.widget.winfo_rooty() - form.winfo_rooty()
            bottom = top + event.widget.winfo_height()
            visible_top = viewport.canvasy(0)
            height = viewport.winfo_height()
            total = max(1, form.winfo_height())
            if top < visible_top:
                viewport.yview_moveto(max(0, top - 8) / total)
            elif bottom > visible_top + height:
                viewport.yview_moveto((bottom - height + 8) / total)

        window.bind("<MouseWheel>", scroll_settings)
        window.bind("<FocusIn>", reveal_setting)
        tk.Label(form, text="Your connections", fg=TEXT, bg=BG, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(
            form,
            text="Saving settings starts a fresh session. Keys stay protected by Windows on this PC.\n"
            "Blank secret fields keep existing keys. Disconnect removes saved keys and Google tokens.",
            fg=MUTED,
            bg=BG,
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        fields = {}

        def entry(label, key, value="", secret=False):
            tk.Label(form, text=label, fg=MUTED, bg=BG).pack(anchor="w", pady=(6, 2))
            variable = tk.StringVar(value=value)
            widget = ttk.Entry(form, textvariable=variable, show="•" if secret else "")
            widget.pack(fill="x")
            fields[key] = variable

        tk.Label(form, text="AI provider", fg=MUTED, bg=BG).pack(anchor="w")
        provider = tk.StringVar(value=cfg.provider)
        ttk.Combobox(form, textvariable=provider, values=["anthropic", "ollama"], state="readonly").pack(fill="x")
        entry("Model ID (for the selected provider)", "model", cfg.model)
        entry("Anthropic API key (optional replacement)", "anthropic_key", secret=True)
        entry("Local Ollama address (no model download)", "local_url", cfg.local_url)
        tk.Label(form, text="Google account: " + (cfg.gmail_account or "Not connected"), fg=TEXT, bg=BG).pack(
            anchor="w", pady=(12, 4)
        )
        google_bar = tk.Frame(form, bg=BG)
        google_bar.pack(fill="x")

        def connect():
            if draft_changed():
                messagebox.showinfo(
                    "Save your settings first",
                    "Save the settings you edited before connecting Google. Your draft is still here.",
                    parent=window,
                )
                return
            path = filedialog.askopenfilename(
                parent=window, title="Select Google Desktop OAuth client JSON", filetypes=[("JSON", "*.json")]
            )
            if not path:
                return
            window.destroy()

            def connected(email):
                self.app._configuration_changed()
                self.app._append(
                    "app", "Google account connected. Open Settings → Choose calendar to select one calendar."
                )

            self.app._run(
                lambda: self.app.controller.connect_google(path),
                connected,
                "Waiting for Google sign-in in your browser…",
            )

        def choose_calendar():
            if draft_changed():
                messagebox.showinfo(
                    "Save your settings first",
                    "Save the settings you edited before choosing a calendar. Your draft is still here.",
                    parent=window,
                )
                return
            window.destroy()
            self.app._run(
                lambda: self.app.controller.list_calendars(),
                self.app._calendar_picker,
                "Loading your calendar choices…",
            )

        ttk.Button(google_bar, text="Connect Google…", command=connect).pack(side="left")
        ttk.Button(google_bar, text="Choose calendar…", command=choose_calendar).pack(side="left", padx=8)
        tk.Label(
            form,
            text="Selected calendar: " + (cfg.calendar_id or "None selected"),
            fg=MUTED,
            bg=BG,
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=4)
        entry("Notion integration token (optional replacement)", "notion_token", secret=True)
        entry("Selected Notion page ID (share this page with your integration)", "notion_page_id", cfg.notion_page_id)

        def draft_changed():
            return (
                provider.get() != cfg.provider
                or any(
                    fields[key].get().strip() != getattr(cfg, key) for key in ("model", "local_url", "notion_page_id")
                )
                or any(fields[key].get().strip() for key in ("anthropic_key", "notion_token"))
            )

        tk.Label(
            form,
            text="Cloud mode sends relevant content and saved preferences to Anthropic.\n"
            "Local mode needs an already-installed, tool-capable local Ollama model.\n"
            "Gmail is read-only. Drive/files and separate Notion Calendar support are deferred.",
            fg=MUTED,
            bg=BG,
            justify="left",
            wraplength=680,
        ).pack(anchor="w", pady=12)
        controls = tk.Frame(form, bg=BG)
        controls.pack(fill="x", side="bottom", pady=6)

        def save():
            try:
                updated = replace(
                    cfg,
                    provider=provider.get(),
                    model=fields["model"].get().strip(),
                    local_url=fields["local_url"].get().strip().rstrip("/"),
                    notion_page_id=fields["notion_page_id"].get().strip(),
                )
                self.app.controller.save_settings(
                    updated, {name: fields[name].get().strip() for name in ("anthropic_key", "notion_token")}
                )
                self.app._configuration_changed()
                window.destroy()
            except AppError as exc:
                messagebox.showerror("Settings", str(exc), parent=window)

        def disconnect():
            if not messagebox.askyesno(
                "Disconnect",
                "Remove saved API keys and Google tokens from this app? "
                "This does not revoke permissions at the providers. Preferences remain.",
                parent=window,
            ):
                return
            try:
                self.app.controller.disconnect()
                self.app._configuration_changed()
                window.destroy()
            except AppError as exc:
                messagebox.showerror("Disconnect", str(exc), parent=window)

        ttk.Button(controls, text="Save settings", command=save).pack(side="left")
        ttk.Button(controls, text="Disconnect all", command=disconnect).pack(side="left", padx=8)
        ttk.Button(controls, text="Cancel", command=window.destroy).pack(side="right")

    def _calendar_picker(self, calendars):
        window = self.app._dialog("Choose one calendar", "620x420")
        tk.Label(window, text="Select the only calendar this assistant may access", bg=BG, fg=TEXT).pack(
            padx=16, pady=16
        )
        choices = tk.Listbox(window, bg=PANEL, fg=TEXT, font=("Segoe UI", 11), exportselection=False)
        choices.pack(fill="both", expand=True, padx=16)
        for calendar in calendars:
            choices.insert(
                "end", f"{calendar.get('summary', calendar['id'])} ({calendar.get('accessRole', 'unknown')})"
            )

        def select():
            indices = choices.curselection()
            if not indices:
                return
            try:
                self.app.controller.select_calendar(calendars[indices[0]]["id"])
                self.app._configuration_changed()
                window.destroy()
            except AppError as exc:
                messagebox.showerror("Calendar", str(exc), parent=window)

        ttk.Button(window, text="Use selected calendar", command=select).pack(pady=16)
