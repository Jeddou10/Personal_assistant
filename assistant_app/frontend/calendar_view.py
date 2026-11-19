"""Read-only, nonmodal two-day calendar; consumes application snapshots only."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistant_app.application.calendar import CalendarSnapshot


MAX_EVENTS_PER_DAY = 50
PIXELS_PER_MINUTE = 1.5
MIN_BLOCK_MINUTES = 16


def _minutes(value, day):
    """Use local wall time, including midnight at the following day boundary."""
    if value.date() > day:
        return 1440
    if value.date() < day:
        return 0
    return value.hour * 60 + value.minute + value.second / 60


def _interval(entry, day):
    if entry.start is None or entry.end is None:
        return None
    start = min(1440, max(0, _minutes(entry.start, day)))
    end = min(1440, max(start, _minutes(entry.end, day)))
    # Reserve the actual painted footprint so tiny adjacent events cannot overwrite.
    start = min(start, 1440 - MIN_BLOCK_MINUTES)
    return start, min(1440, max(end, start + MIN_BLOCK_MINUTES))


def _lanes(entries, day):
    """Return (entry, start, end, lane, group_width) for overlap components."""
    intervals = []
    for index, entry in enumerate(entries):
        span = _interval(entry, day)
        if span is not None:
            intervals.append((*span, index, entry))
    intervals.sort(key=lambda item: (item[0], item[1], item[2]))
    result, group, ends = [], [], []
    group_end = -1

    def flush():
        result.extend((*item, len(ends)) for item in group)

    for start, end, _, entry in intervals:
        if group and start >= group_end:
            flush()
            group, ends = [], []
        lane = next((i for i, finish in enumerate(ends) if finish <= start), len(ends))
        if lane == len(ends):
            ends.append(end)
        else:
            ends[lane] = end
        group.append((entry, start, end, lane))
        group_end = max(group_end, end)
    flush()
    return result


def _plain(value):
    return "".join(c for c in str(value) if c in "\n\t" or c.isprintable())


def _fit_text(value, font, width, height):
    """Pixel-bounded wrapping, including long words and arbitrarily long titles."""
    count = int(height // font.metrics("linespace"))
    if count < 1 or width < font.measure("…"):
        return ""
    source = " ".join(_plain(value).split())[:1000]
    lines = []
    while source and len(lines) < count:
        low, high = 0, len(source)
        while low < high:
            mid = (low + high + 1) // 2
            if font.measure(source[:mid]) <= width:
                low = mid
            else:
                high = mid - 1
        if not low:
            break
        cut = low
        if low < len(source):
            space = source.rfind(" ", 0, low + 1)
            if space > 0:
                cut = space
        lines.append(source[:cut])
        source = source[cut:].lstrip()
    if source and lines:
        last = lines[-1]
        while last and font.measure(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return "\n".join(lines)


class CalendarView:
    def __init__(self, app):
        self.app = app
        self.window = None
        self._detail = None
        self._snapshot = None
        self._event_details = {}

    def close(self):
        """Destroy this view and its detail popup; no timers or global bindings exist."""
        window, self.window = self.window, None
        self._detail = None
        self._snapshot = None
        self._event_details.clear()
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    @staticmethod
    def _size(window, width, height):
        sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
        width, height = min(width, max(1, sw - 48)), min(height, max(1, sh - 96))
        window.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(0, (sh - height) // 2)}")
        window.minsize(min(420, width), min(320, height))

    def show(self, snapshot: CalendarSnapshot) -> tk.Toplevel:
        self.close()
        self._snapshot = snapshot
        window = self.window = tk.Toplevel(self.app.chat_window)
        window.title("Your calendar")
        window.transient(self.app.chat_window)
        self._size(window, 1000, 780)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())
        window.bind("<Destroy>", self._destroyed, add="+")
        header = ttk.Frame(window, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="Your calendar", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(header, text=f"{_plain(snapshot.timezone_label)} · Selected calendar · Today and tomorrow").pack(
            anchor="w"
        )
        dates = ttk.Frame(window, padding=(58, 4, 16, 4))
        dates.pack(fill="x")
        for index, day in enumerate(snapshot.days[:2]):
            dates.columnconfigure(index, weight=1, uniform="day")
            ttk.Label(
                dates, text=f"{('Today', 'Tomorrow')[index]} · {day.date:%d %b}", font=("Segoe UI", 10, "bold")
            ).grid(row=0, column=index, sticky="w")
        notices = []
        hidden = sum(max(0, len(day.entries) - MAX_EVENTS_PER_DAY) for day in snapshot.days[:2])
        if snapshot.truncated:
            notices.append("Calendar results are truncated; some events are not included.")
        if hidden:
            notices.append(f"Display limit: {hidden} additional entries not shown (50 per day).")
        if snapshot.skipped:
            notices.append(f"Skipped entries: {snapshot.skipped}.")
        footer = ttk.Frame(window, padding=8)
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="Close", command=self.close).pack(side="right")
        notice = ttk.Label(footer, text="\n".join(notices) or "Read-only · Click an event for details.", wraplength=700)
        notice.pack(side="left", fill="x", expand=True)
        footer.bind("<Configure>", lambda event: notice.configure(wraplength=max(100, event.width - 110)))
        body = ttk.Frame(window)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, background="white", highlightthickness=0, takefocus=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind("<Button-1>", self._open_current)
        self._font = tkfont.Font(window, family="Segoe UI", size=9)
        self.canvas.bind("<Configure>", self._draw)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda event: self._scroll(-3))
        self.canvas.bind("<Button-5>", lambda event: self._scroll(3))
        for key, amount in (("<Up>", -1), ("<Down>", 1), ("<Prior>", -12), ("<Next>", 12)):
            self.canvas.bind(key, lambda event, amount=amount: self._scroll(amount))
        self._draw()
        window.update_idletasks()
        self._draw()
        entries = [entry for day in snapshot.days[:2] for entry in day.entries[:MAX_EVENTS_PER_DAY]]
        starts = [entry.start.hour * 60 + entry.start.minute for entry in entries if entry.start is not None]
        if starts and not any(entry.all_day for entry in entries):
            first = min(starts)
            # Begin near the first appointment; the entire day remains scrollable.
            self.canvas.yview_moveto(max(0, first - 60) * PIXELS_PER_MINUTE / (1440 * PIXELS_PER_MINUTE + 140))
        return window

    def _destroyed(self, event):
        if event.widget is self.window:
            self.window = None
            self._snapshot = None
            self._detail = None
            self._event_details.clear()

    def _scroll(self, amount):
        self.canvas.yview_scroll(amount, "units")
        return "break"

    def _wheel(self, event):
        amount = max(1, abs(int(event.delta)) // 120)
        return self._scroll(-amount if event.delta > 0 else amount)

    def _open_current(self, event):
        for item in self.canvas.find_withtag("current"):
            for tag in self.canvas.gettags(item):
                if tag in self._event_details:
                    entry, day = self._event_details[tag]
                    self._show_detail(entry, day)
                    return

    def _block(self, entry, x1, y1, x2, y2, label, day=None):
        canvas = self.canvas
        tag = f"event_{self._block_id}"
        self._block_id += 1
        self._event_details[tag] = (entry, day)
        canvas.create_rectangle(x1, y1, x2, y2, fill="#e5efff", outline="#7393bd", tags=tag)
        text = _fit_text(label, self._font, x2 - x1 - 8, y2 - y1 - 4)
        if text:
            canvas.create_text(x1 + 4, y1 + 2, anchor="nw", text=text, font=self._font, tags=tag)

    def _draw(self, event=None):
        if self._snapshot is None:
            return
        canvas = self.canvas
        canvas.delete("all")
        self._block_id = 0
        self._event_details = {}
        width = max(1, canvas.winfo_width())
        gutter = min(58, width / 5)
        col = max(1, (width - gutter - 8) / 2)
        days = self._snapshot.days[:2]
        entries = [day.entries[:MAX_EVENTS_PER_DAY] for day in days]
        strips = [[e for e in group if e.all_day or e.start is None or e.end is None] for group in entries]
        grid_top = 82 + max(1, max((len(group) for group in strips), default=0)) * 30
        bottom = grid_top + 1440 * PIXELS_PER_MINUTE
        canvas.configure(scrollregion=(0, 0, width, bottom + 28))
        for hour in range(25):
            y = grid_top + hour * 60 * PIXELS_PER_MINUTE
            canvas.create_line(gutter, y, width - 4, y, fill="#dce1e8")
            canvas.create_text(gutter - 5, y, anchor="e", text=f"{hour:02}:00", font=self._font)
        for index, day in enumerate(days):
            left, right = gutter + index * col, gutter + (index + 1) * col
            heading = f"{('Today', 'Tomorrow')[index]} · {day.date:%a, %d %b %Y}"
            canvas.create_text(
                left + 5, 8, anchor="nw", font=self._font, text=_fit_text(heading, self._font, col - 10, 42)
            )
            canvas.create_text(
                left + 5,
                54,
                anchor="nw",
                font=self._font,
                text=_fit_text("All-day / time unavailable", self._font, col - 10, 22),
            )
            canvas.create_line(left, grid_top, left, bottom, fill="#b8c0cc")
            for row, entry in enumerate(strips[index]):
                label = self._label(entry)
                if not entry.all_day:
                    label = "Time unavailable · " + label
                self._block(entry, left + 3, 78 + row * 30, right - 3, 105 + row * 30, label, day.date)
            timed = [e for e in entries[index] if not e.all_day and e.start is not None and e.end is not None]
            for entry, start, end, lane, lanes in _lanes(timed, day.date):
                lane_width = (col - 6) / lanes
                x = left + 3 + lane * lane_width
                self._block(
                    entry,
                    x,
                    grid_top + start * PIXELS_PER_MINUTE,
                    x + max(0.1, lane_width - 2),
                    grid_top + end * PIXELS_PER_MINUTE - 1,
                    f"{entry.start:%H:%M} · {self._label(entry)}",
                    day.date,
                )
            if not entries[index]:
                canvas.create_text(left + 8, grid_top + 18, anchor="nw", text="No events", font=self._font)

    @staticmethod
    def _label(entry):
        return (
            ("← " if entry.continues_before else "")
            + (entry.title or "Untitled event")
            + (" →" if entry.continues_after else "")
        )

    def _show_detail(self, entry, day=None):
        if self.window is None:
            return
        if self._detail is not None:
            try:
                self._detail.destroy()
            except tk.TclError:
                pass
        popup = self._detail = tk.Toplevel(self.window)
        popup.title("Event details")
        popup.transient(self.window)
        self._size(popup, 560, 420)
        ttk.Button(popup, text="Close", command=popup.destroy).pack(side="bottom", pady=8)
        popup.bind("<Escape>", lambda event: popup.destroy())
        body = ttk.Frame(popup, padding=10)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", width=1, height=1, padx=8, pady=8)
        scrollbar = ttk.Scrollbar(body, command=text.yview)
        scrollbar.pack(side="right", fill="y")
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(fill="both", expand=True)
        lines = [entry.title or "Untitled event", "", "All-day event" if entry.all_day else "Timed event"]
        if day is not None:
            lines.append(f"Displayed date: {day:%A, %d %B %Y}")
        if not entry.all_day:
            for label, value in (("Start", entry.start), ("End", entry.end)):
                lines.append(f"{label}: {value.isoformat(sep=' ') if value else 'Time unavailable'}")
        lines.append(f"Location: {entry.location or 'Not specified'}")
        if entry.continues_before:
            lines.append("Continues from the previous day.")
        if entry.continues_after:
            lines.append("Continues into the next day.")
        if not entry.all_day and (entry.continues_before or entry.continues_after):
            lines.append("Times shown are clipped to this displayed day.")
        text.insert("1.0", _plain("\n".join(lines)))
        text.configure(state="disabled")
