"""Synthetic calendar checks: no Tk interpreter, accounts, or network."""

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from assistant_app.application.calendar import CalendarDay, CalendarEntry, CalendarSnapshot
from assistant_app.frontend.calendar_view import CalendarView, _fit_text, _interval, _lanes, _minutes

DAY = date(2026, 10, 25)


def entry(start=9, end=10, **kwargs):
    return CalendarEntry(
        "Meeting",
        datetime(2026, 10, 25, start, tzinfo=timezone.utc),
        datetime(2026, 10, 25, end, tzinfo=timezone.utc),
        False,
        "Office",
        **kwargs,
    )


class FixedFont:
    def measure(self, text):
        return len(text) * 7

    def metrics(self, key):
        return 14


class CalendarViewTests(unittest.TestCase):
    def test_constructor_is_lazy_and_close_is_idempotent(self):
        view = CalendarView(object())
        window = Mock()
        view.window = window
        view.close()
        view.close()
        window.destroy.assert_called_once_with()
        self.assertIsNone(view.window)

    def test_midnight_boundary(self):
        midnight = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.assertEqual(_minutes(midnight, DAY), 1440)
        event = CalendarEntry("Late", datetime(2026, 10, 25, 23, tzinfo=timezone.utc), midnight, False, "", False, True)
        self.assertEqual(_interval(event, DAY), (1380, 1440))
        self.assertTrue(CalendarView._label(event).endswith("→"))

    def test_fallback_keeps_local_hour_and_visible_block(self):
        start = datetime(2026, 10, 25, 2, 45, tzinfo=timezone(timedelta(hours=2)))
        end = datetime(2026, 10, 25, 2, 15, tzinfo=timezone(timedelta(hours=1)))
        event = CalendarEntry("Fallback", start, end, False, "")
        self.assertGreater(end.timestamp(), start.timestamp())
        self.assertEqual(_interval(event, DAY), (165, 181))
        self.assertIn("+02:00", start.isoformat())
        self.assertIn("+01:00", end.isoformat())

    def test_lanes_reuse_space_and_reset_between_groups(self):
        result = _lanes([entry(9, 12), entry(10, 11), entry(11, 12), entry(13, 14)], DAY)
        self.assertEqual([(row[3], row[4]) for row in result], [(0, 2), (1, 2), (1, 2), (0, 1)])
        for a in result:
            for b in result:
                if a is not b and a[3] == b[3]:
                    self.assertFalse(a[1] < b[2] and b[1] < a[2])

    def test_text_stays_inside_pixel_bounds(self):
        font = FixedFont()
        for value in ("very long title " * 1000, "x" * 2000, "Title\n\tSecond line"):
            fitted = _fit_text(value, font, 70, 28)
            self.assertLessEqual(len(fitted.splitlines()), 2)
            self.assertTrue(all(font.measure(line) <= 70 for line in fitted.splitlines()))
        self.assertEqual(_fit_text("Title", font, 3, 30), "")
        self.assertEqual(_fit_text("Title", font, 100, 3), "")

    def test_render_caps_events_and_keeps_empty_day(self):
        view = CalendarView(object())
        view._snapshot = CalendarSnapshot(
            (CalendarDay(DAY, (entry(),) * 55), CalendarDay(DAY + timedelta(days=1), ())), "UTC", True, 2
        )
        view.canvas = Mock()
        view.canvas.winfo_width.return_value = 950
        view._font = FixedFont()
        view._block = Mock()
        view._draw()
        self.assertEqual(view._block.call_count, 50)
        labels = [call.kwargs.get("text") for call in view.canvas.create_text.call_args_list]
        self.assertIn("No events", labels)
        self.assertIn("24:00", labels)

    def test_wheel_is_local_and_directional(self):
        view = CalendarView(object())
        view.canvas = Mock()
        self.assertEqual(view._wheel(SimpleNamespace(delta=-120)), "break")
        view.canvas.yview_scroll.assert_called_with(1, "units")
        view._wheel(SimpleNamespace(delta=240))
        view.canvas.yview_scroll.assert_called_with(-2, "units")


if __name__ == "__main__":
    unittest.main()
