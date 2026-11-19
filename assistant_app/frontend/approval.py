"""ApprovalView composition component."""

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from assistant_app.domain.errors import AppError

from .theme import ACCENT, BG, MUTED, PANEL, SOFT, TEXT


def preview_text(proposal):
    """Show consequences and exact user content, not internal hashes/API fields."""
    data = proposal.prepared
    destination = data.get("destination", {})
    lines = ["DESTINATION"]
    if isinstance(destination, dict):
        labels = {"account": "Google account", "calendar_id": "Calendar", "page_id": "Notion page"}
        lines.extend(f"{labels.get(key, key)}: {value}" for key, value in destination.items())
    else:
        lines.append(str(destination))
    if proposal.action == "notion_append":
        lines.extend(["", "APPEND THIS TEXT", data.get("after", {}).get("append_text", "")])
    else:
        if data.get("event_id"):
            lines.append("Event ID: " + data["event_id"])
        for heading, event in (("BEFORE", data.get("before")), ("AFTER", data.get("after"))):
            lines.extend(["", heading])
            if event is None:
                lines.append(
                    "Event will be deleted." if heading == "AFTER" else "New appointment; no existing event is changed."
                )
                continue
            for label, key in (
                ("Title", "summary"),
                ("Start", "start"),
                ("End", "end"),
                ("Description", "description"),
                ("Location", "location"),
            ):
                value = event.get(key, "")
                if isinstance(value, dict):
                    value = value.get("dateTime", value.get("date", "")) + (
                        " (" + value["timeZone"] + ")" if value.get("timeZone") else ""
                    )
                lines.append(label + ": " + str(value))
            reminders = event.get("reminders", {}).get("overrides", [])
            lines.append(
                "Reminders: "
                + (", ".join(f"{r.get('method')} {r.get('minutes')} minutes before" for r in reminders) or "None")
            )
    lines.extend(["", "EFFECT", data.get("disclosure", "This exact change runs once only after approval.")])
    return "\n".join(lines)


class ApprovalView:
    def __init__(self, app):
        self.app = app
        self.proposal_ids = []

    def build(self, footer):
        self.panel = proposals = tk.Frame(footer, bg=SOFT, padx=12, pady=8)
        proposal_header = tk.Frame(proposals, bg=SOFT)
        proposal_header.pack(fill="x")
        self.proposal_heading = tk.Label(
            proposal_header, text="You're in control", fg=TEXT, bg=SOFT, font=("Segoe UI", 10, "bold")
        )
        self.proposal_heading.pack(side="left")
        self.review_button = ttk.Button(proposal_header, text="Review", command=self.app.preview)
        self.review_button.pack(side="right")
        self.proposal_hint = tk.Label(
            proposals,
            text="No proposed changes. Every write needs your approval.",
            fg=MUTED,
            bg=SOFT,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.proposal_hint.pack(fill="x", pady=(3, 0))
        self.proposal_list = tk.Listbox(
            proposals,
            height=2,
            bg=SOFT,
            fg=TEXT,
            selectbackground=ACCENT,
            selectforeground="white",
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 10),
            exportselection=False,
        )
        self.proposal_list.bind("<Double-Button-1>", lambda _: self.app.preview())
        self.proposal_list.bind("<Return>", lambda _: self.app.preview())

    def _refresh_proposals(self):
        selection = self.proposal_list.curselection()
        selected = self.proposal_ids[selection[0]] if selection and selection[0] < len(self.proposal_ids) else None
        proposals = self.app.engine.pending()
        if proposals and not self.panel.winfo_manager():
            self.panel.pack(fill="x", pady=(10, 0))
        elif not proposals:
            self.panel.pack_forget()
        self.review_button.configure(state="disabled" if self.app.busy else "normal")
        self.proposal_ids = [p.id for p in proposals]
        pending_count = sum(p.state == "pending" for p in proposals)
        self.proposal_heading.configure(
            text=f"{pending_count} change{'s' if pending_count != 1 else ''} to review"
            if pending_count
            else "Changes and outcomes"
            if proposals
            else "You're in control"
        )
        self.proposal_hint.configure(
            text="Select a change to review, revise or reject it."
            if proposals
            else "No proposed changes. Every write needs your approval."
        )
        if proposals and not self.proposal_list.winfo_manager():
            self.proposal_list.pack(fill="x", pady=(6, 0))
        elif not proposals:
            self.proposal_list.pack_forget()
        names = {
            "notion_append": "Append to Notion",
            "calendar_create": "Create appointment",
            "calendar_update": "Update appointment",
            "calendar_delete": "Delete appointment",
        }
        states = {
            "pending": "Ready for review",
            "unknown": "Outcome unknown — inspect destination",
            "succeeded": "Completed",
            "failed": "Failed",
            "expired": "Expired",
            "rejected": "Rejected",
        }
        rows = tuple(f"{names.get(p.action, p.action)}  ·  {states.get(p.state, p.state)}" for p in proposals)
        if self.proposal_list.get(0, "end") != rows:
            scroll_position = self.proposal_list.yview()[0]
            self.proposal_list.delete(0, "end")
            for row in rows:
                self.proposal_list.insert("end", row)
            self.proposal_list.yview_moveto(scroll_position)
        if selected in self.proposal_ids:
            self.proposal_list.selection_set(self.proposal_ids.index(selected))
        elif proposals:
            self.proposal_list.selection_set(0)

    def preview(self):
        if not self.app._idle():
            return
        selection = self.proposal_list.curselection()
        if not selection:
            return
        proposal = next((p for p in self.app.engine.pending() if p.id == self.proposal_ids[selection[0]]), None)
        if not proposal:
            return
        window = self.app._dialog("Review proposed change")
        tk.Label(
            window, text=proposal.action.replace("_", " ").title(), bg=BG, fg=TEXT, font=("Segoe UI", 17, "bold")
        ).pack(anchor="w", padx=18, pady=(16, 6))
        tk.Label(
            window,
            text="Review the exact destination and content below. Approval applies only to this version.\n"
            "Times include UTC offsets. Calendar-level notifications are controlled by Google.",
            bg=BG,
            fg=MUTED,
            justify="left",
            wraplength=610,
        ).pack(anchor="w", padx=18, pady=4)
        preview = ScrolledText(window, wrap="word", bg=PANEL, fg=TEXT, font=("Consolas", 10), relief="flat")
        preview.insert("1.0", preview_text(proposal))
        preview.configure(state="disabled")
        tk.Label(
            window,
            text=f"Status: {proposal.state}. {proposal.outcome}",
            bg=BG,
            fg=MUTED,
            wraplength=610,
            justify="left",
        ).pack(side="bottom", fill="x", padx=18)
        controls = tk.Frame(window, bg=BG)
        controls.pack(side="bottom", fill="x", padx=18, pady=16)
        preview.pack(fill="both", expand=True, padx=18, pady=12)

        def approve():
            window.destroy()
            self.app._run(
                lambda: self.app.engine.approve(proposal.id),
                lambda result: self.app._append("app", result),
                "Applying approved change…",
            )

        def reject(revise=False):
            if revise and self.app.input.get("1.0", "end-1c").strip():
                if not messagebox.askyesno(
                    "Replace your draft?",
                    "Reject this proposal and replace your unsent message with a revision request?",
                    parent=window,
                ):
                    return
            try:
                self.app.engine.reject(proposal.id)
            except AppError as exc:
                messagebox.showerror("Unavailable", str(exc), parent=window)
                return
            window.destroy()
            self.app._refresh_proposals()
            self.app._append("app", "Proposal rejected. Nothing was written.")
            if revise:
                self.app.replace_draft(
                    "Please revise the rejected " + proposal.action.replace("_", " ") + " proposal: ", confirm=False
                )

        if proposal.state == "pending":
            ttk.Button(controls, text="Approve this change", command=approve).pack(side="left")
            ttk.Button(controls, text="Request changes", command=lambda: reject(True)).pack(side="left", padx=8)
            ttk.Button(controls, text="Reject", command=reject).pack(side="left")
        ttk.Button(controls, text="Close", command=window.destroy).pack(side="right")
