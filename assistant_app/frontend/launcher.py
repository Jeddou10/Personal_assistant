"""LauncherView composition component."""

import tkinter as tk

from .theme import BG, draw_soccer_ball


class LauncherView:
    def __init__(self, app):
        self.app = app
        self.root = root = app.root
        self._drag = None
        self._moved = False
        root.title("Personal Assistant")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        root.geometry(f"60x60+{root.winfo_screenwidth() - 100}+{root.winfo_screenheight() - 170}")
        # The transparent corners leave a round soccer ball rather than a square tile.
        try:
            root.attributes("-transparentcolor", "#ff00ff")
            launcher_background = "#ff00ff"
        except tk.TclError:
            launcher_background = BG
        badge = tk.Canvas(root, width=60, height=60, bg=launcher_background, highlightthickness=0, cursor="hand2")
        draw_soccer_ball(badge)
        badge.pack(fill="both", expand=True)
        badge.bind("<ButtonPress-1>", self._start_drag)
        badge.bind("<B1-Motion>", self._drag_to)
        badge.bind("<ButtonRelease-1>", self._release_drag)
        badge.bind("<Button-3>", self._launcher_menu)
        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="Open chat", command=self.app.show)
        self.menu.add_command(label="Settings", command=self.app.settings)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.app.quit)

    def _start_drag(self, event):
        self._drag = (
            event.x_root - self.app.root.winfo_x(),
            event.y_root - self.app.root.winfo_y(),
            event.x_root,
            event.y_root,
        )
        self._moved = False

    def _drag_to(self, event):
        if self._drag:
            dx, dy, sx, sy = self._drag
            self._moved |= abs(event.x_root - sx) + abs(event.y_root - sy) > 5
            x = max(0, min(self.app.root.winfo_screenwidth() - 60, event.x_root - dx))
            y = max(0, min(self.app.root.winfo_screenheight() - 60, event.y_root - dy))
            self.app.root.geometry(f"+{x}+{y}")

    def _release_drag(self, _):
        if not self._moved:
            if self.app.chat_window.state() == "withdrawn":
                self.app.show()
            else:
                self.app.chat_window.withdraw()
        self._drag = None

    def _launcher_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
