"""Run tests without credentials; optionally keep all native windows hidden."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Withdraw native windows; skip visibility-only checks.")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    runner = unittest.TextTestRunner(verbosity=2)
    if not args.headless:
        return 0 if runner.run(suite).wasSuccessful() else 1

    import tkinter as tk

    from assistant_app.ui import AssistantUI

    class HiddenRoot(tk.Tk):
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            self.withdraw()

    class HiddenWindow(tk.Toplevel):
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            self.withdraw()

    visible_checks = {
        "test_ui.UITests.test_launcher_and_chat",
        "test_ui.UITests.test_settings_controls_and_no_accidental_save",
    }
    filtered = unittest.TestSuite(case for case in flatten(suite) if case.id() not in visible_checks)
    print("Hidden-window mode: excluding two UI tests that require visible windows.", flush=True)
    with (
        patch.object(tk, "Tk", HiddenRoot),
        patch.object(tk, "Toplevel", HiddenWindow),
        patch.object(AssistantUI, "show", lambda self: None),
    ):
        return 0 if runner.run(filtered).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
