"""Calendar shortcut date, scope and presentation policy without live accounts."""

import unittest
from datetime import date, timedelta, timezone
from unittest.mock import Mock

from assistant_app.application.calendar import calendar_snapshot, day_window, important_mail_prompt
from assistant_app.application.controller import ApplicationController
from assistant_app.application.engine import Engine
from assistant_app.domain.errors import AppError
from assistant_app.domain.models import Settings


class CalendarTests(unittest.TestCase):
    today = date(2000, 1, 15)

    def test_window_and_mail_query_use_explicit_day_boundaries(self):
        start, end = day_window(self.today, zone=timezone.utc)
        self.assertEqual(start.isoformat(), "2000-01-15T00:00:00+00:00")
        self.assertEqual(end.isoformat(), "2000-01-17T00:00:00+00:00")
        prompt = important_mail_prompt(self.today)
        self.assertIn("is:important after:", prompt)
        self.assertIn("2000-01-15", prompt)
        self.assertIn("not only unread", prompt)

    def test_all_day_exclusive_end_and_timed_overnight_clipping(self):
        events = [
            {"summary": "All-day", "start": {"date": "2000-01-15"}, "end": {"date": "2000-01-16"}},
            {
                "summary": "Overnight",
                "start": {"dateTime": "2000-01-15T23:00:00Z"},
                "end": {"dateTime": "2000-01-16T02:00:00Z"},
            },
            {
                "summary": "Midnight end",
                "start": {"dateTime": "2000-01-14T22:00:00Z"},
                "end": {"dateTime": "2000-01-15T00:00:00Z"},
            },
        ]
        snapshot = calendar_snapshot({"events": events}, self.today, zone=timezone.utc)
        self.assertEqual([e.title for e in snapshot.days[0].entries], ["All-day", "Overnight"])
        self.assertEqual([e.title for e in snapshot.days[1].entries], ["Overnight"])
        self.assertTrue(snapshot.days[0].entries[1].continues_after)
        self.assertTrue(snapshot.days[1].entries[0].continues_before)
        self.assertEqual(snapshot.days[1].entries[0].start.hour, 0)

    def test_offsets_cancelled_bad_events_and_result_limit_are_explicit(self):
        event = {
            "summary": "Offset",
            "start": {"dateTime": "2000-01-16T00:30:00+02:00"},
            "end": {"dateTime": "2000-01-16T01:30:00+02:00"},
        }
        snapshot = calendar_snapshot(
            {"events": [event, {}, {"status": "cancelled"}], "truncated": True}, self.today, zone=timezone.utc
        )
        self.assertEqual(snapshot.days[0].entries[0].start.hour, 22)
        self.assertEqual(snapshot.days[1].entries, ())
        self.assertEqual(snapshot.skipped, 1)
        self.assertTrue(snapshot.truncated)
        with self.assertRaises(AppError):
            calendar_snapshot({"events": "invalid"}, self.today)

    def test_direct_calendar_read_never_calls_model_or_mutates_conversation(self):
        store = Mock(revision=0)
        store.load_settings.return_value = Settings()
        integrations = Mock()
        integrations.list_events.return_value = {"events": []}
        provider = Mock()
        engine = Engine(store, integrations=integrations, complete=provider)
        controller = ApplicationController(store, engine, Mock(), Mock())
        snapshot = controller.calendar_today_tomorrow(self.today)
        self.assertEqual(len(snapshot.days), 2)
        integrations.list_events.assert_called_once()
        provider.assert_not_called()
        self.assertEqual(engine.messages, [])
        self.assertEqual(engine.pending(), [])
        with self.assertRaises(AppError):
            engine.read_tool("propose_notion_append", {"text": "Not allowed"})
        integrations.prepare.assert_not_called()

    def test_direct_read_discards_changed_scope(self):
        store = Mock(revision=0)
        store.load_settings.return_value = Settings()
        integrations = Mock()
        engine = Engine(store, integrations=integrations)

        def changed(*_):
            store.revision += 1
            return {"events": []}

        integrations.list_events.side_effect = changed
        with self.assertRaises(AppError):
            engine.read_tool("list_events", {"time_min": "start", "time_max": "end"})
        self.assertEqual(engine.messages, [])

    def test_mail_summary_can_finish_after_ten_sequential_reads(self):
        store = Mock(revision=0)
        store.load_settings.return_value = Settings()
        store.load_preferences.return_value = []
        integrations = Mock()
        integrations.search_mail.return_value = {"messages": [{"id": str(i)} for i in range(10)]}
        integrations.read_mail.return_value = {"subject": "Synthetic", "text": "A short message"}
        replies = [[{"type": "tool_use", "id": "search", "name": "search_mail", "input": {"query": "is:important"}}]]
        replies += [
            [{"type": "tool_use", "id": f"read{i}", "name": "read_mail", "input": {"message_id": str(i)}}]
            for i in range(10)
        ]
        replies.append([{"type": "text", "text": "Finished summary"}])
        engine = Engine(store, integrations=integrations, complete=Mock(side_effect=replies))
        self.assertEqual(engine.chat(important_mail_prompt(self.today)), "Finished summary")
        self.assertEqual(integrations.read_mail.call_count, 10)
        self.assertEqual(engine.limits.max_tool_calls, 12)

    def test_daylight_saving_midnights_are_converted_separately(self):
        from datetime import tzinfo

        class TransitionZone(tzinfo):
            def utcoffset(self, dt):
                return timedelta(hours=2 if dt and dt.date() < date(2026, 10, 26) else 1)

            def dst(self, dt):
                return timedelta(0)

        start, end = day_window(date(2026, 10, 25), zone=TransitionZone())
        self.assertEqual((end.timestamp() - start.timestamp()) / 3600, 49)


if __name__ == "__main__":
    unittest.main()
