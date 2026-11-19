"""Transport-fake boundary tests: never contact private accounts."""

import copy
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from assistant_app.domain.errors import AppError
from assistant_app.infrastructure.integrations import Integrations

PAGE = "12345678-1234-1234-1234-123456789abc"
START = "2000-01-15T10:00:00+01:00"
END = "2000-01-15T11:00:00+01:00"


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            gmail_account="me@example.com", calendar_id="selected@example.com", notion_page_id=PAGE.replace("-", "")
        )
        self.token = "notion-secret"
        self.store = SimpleNamespace(get_secret=lambda name: self.token if name == "notion_token" else None)
        self.api = Integrations(
            self.store, self.settings, request=self.request, token_provider=lambda store: "google-secret"
        )
        self.calls = []
        self.event = {
            "id": "event123",
            "etag": '"version1"',
            "summary": "Original",
            "start": {"dateTime": START},
            "end": {"dateTime": END},
            "organizer": {"self": True},
            "reminders": {"useDefault": False, "overrides": []},
        }
        self.profile = "me@example.com"
        self.write_error = None
        self.extra = {}

    def test_composed_adapters_share_injected_session_and_transport(self):
        from assistant_app.infrastructure.integrations.calendar import CalendarAdapter
        from assistant_app.infrastructure.integrations.gmail import GmailAdapter
        from assistant_app.infrastructure.integrations.notion import NotionAdapter

        self.assertIsInstance(self.api.gmail, GmailAdapter)
        self.assertIsInstance(self.api.calendar, CalendarAdapter)
        self.assertIsInstance(self.api.notion, NotionAdapter)
        self.assertIs(self.api.gmail.session, self.api.calendar.session)
        self.api.search_mail("test")
        self.assertEqual(self.calls[0][2], {"Authorization": "Bearer google-secret"})
        self.assertEqual(len(self.calls), 2)

    def request(self, method, url, headers=None, data=None, **kwargs):
        self.calls.append((method, url, copy.deepcopy(headers), copy.deepcopy(data)))
        if method != "GET":
            if self.write_error:
                raise self.write_error
            return {"id": "created"}
        if url.endswith("/profile"):
            return {"emailAddress": self.profile}
        if "/pages/" in url:
            return {"object": "page", "id": PAGE}
        if "/events/event123" in url:
            return copy.deepcopy(self.event)
        return copy.deepcopy(self.extra)

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def test_notion_execution_cannot_switch_credential_after_record_validation(self):
        prepared = self.api.prepare("notion_append", {"text": "Approved content"})
        original = self.api.notion.record

        def changed_after_validation(*args):
            expected = original(*args)
            self.token = "different-synthetic-credential"
            return expected

        self.api.notion.record = changed_after_validation
        with self.assertRaisesRegex(AppError, "credentials changed"):
            self.api.execute("notion_append", prepared)
        self.assertEqual(self.writes(), [])

    def create(self):
        return self.api.prepare("calendar_create", {"summary": "Appointment", "start": START, "end": END})

    def test_invalid_arguments_have_no_requests(self):
        cases = [
            ("calendar_create", {"summary": "x", "start": START, "end": END, "calendar_id": "evil"}),
            ("calendar_create", {"summary": "x", "start": "2000-01-15T10:00:00", "end": END}),
            ("calendar_create", {"summary": "x", "start": "2000-01-15T10:00:00+01:99", "end": END}),
            ("calendar_create", {"summary": "x", "start": "2000-01-15T10:00:00-00:00", "end": END}),
            ("calendar_create", {"summary": "x", "start": END, "end": START}),
            ("calendar_update", {"event_id": "event123"}),
            ("calendar_delete", {"event_id": "../evil"}),
            ("notion_append", {"text": "x", "page_id": PAGE}),
            ("notion_append", {"text": "x" * 10001}),
            ("notion_append", {"text": ""}),
            ("send_mail", {}),
        ]
        for action, args in cases:
            with self.subTest(action=action, args=str(args)[:90]):
                with self.assertRaises(AppError):
                    self.api.prepare(action, args)
        self.assertEqual(self.calls, [])

    def test_create_exact_preview_popup_and_notification_suppression(self):
        prepared = self.create()
        self.assertEqual(json.loads(json.dumps(prepared)), prepared)
        self.assertIsNone(prepared["before"])
        self.assertEqual(prepared["payload"], prepared["after"])
        self.assertEqual(
            prepared["payload"]["reminders"], {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}]}
        )
        self.assertFalse(self.writes())
        self.api.execute("calendar_create", prepared)
        method, url, _, data = self.writes()[0]
        self.assertEqual(method, "POST")
        self.assertIn("/selected%40example.com/events?sendUpdates=none", url)
        self.assertEqual(data, prepared["payload"])
        self.assertNotIn("attendees", data)

    def test_update_delete_conditional_and_readable(self):
        for action in ("calendar_update", "calendar_delete"):
            with self.subTest(action=action):
                args = {"event_id": "event123"}
                if action.endswith("update"):
                    args["summary"] = "Changed"
                prepared = self.api.prepare(action, args)
                self.assertEqual(prepared["before"], self.event)
                if action.endswith("update"):
                    self.assertEqual(prepared["after"]["summary"], "Changed")
                    self.assertEqual(prepared["after"]["start"]["dateTime"], START)
                else:
                    self.assertIsNone(prepared["after"])
                    self.assertIn("Delete", prepared["disclosure"])
                self.api.execute(action, prepared)
                method, url, headers, _ = self.writes()[-1]
                self.assertEqual(method, "PATCH" if action.endswith("update") else "DELETE")
                self.assertEqual(headers["If-Match"], '"version1"')
                self.assertTrue(url.endswith("?sendUpdates=none"))

    def test_unsafe_events_rejected_at_prepare_and_execute(self):
        changes = [
            {"attendees": [{"email": "guest@example.com"}]},
            {"attendeesOmitted": True},
            {"recurrence": ["RRULE:FREQ=DAILY"]},
            {"recurringEventId": "parent"},
            {"conferenceData": {"conferenceId": "abc"}},
            {"hangoutLink": "https://meet.google.com/x"},
            {"eventType": "outOfOffice"},
            {"organizer": {"self": False}},
            {"start": {"date": "2000-01-15"}},
            {"reminders": {"useDefault": True}},
            {"reminders": {"useDefault": False, "overrides": [{"method": "email"}]}},
            {"etag": ""},
        ]
        original = copy.deepcopy(self.event)
        for change in changes:
            with self.subTest(change=change):
                self.event = copy.deepcopy(original)
                prepared = self.api.prepare("calendar_delete", {"event_id": "event123"})
                self.event.update(change)
                with self.assertRaises(AppError):
                    self.api.prepare("calendar_delete", {"event_id": "event123"})
                with self.assertRaises(AppError):
                    self.api.execute("calendar_delete", prepared)
        self.assertFalse(self.writes())

    def test_etag_conflict_and_partial_time_update(self):
        with self.assertRaises(AppError):
            self.api.prepare("calendar_update", {"event_id": "event123", "start": END})
        prepared = self.api.prepare("calendar_delete", {"event_id": "event123"})
        self.event["etag"] = '"version2"'
        with self.assertRaises(AppError):
            self.api.execute("calendar_delete", prepared)
        self.assertFalse(self.writes())

    def test_prepared_tampering_and_config_change(self):
        prepared = self.create()
        for key, value in (
            ("payload", {"attendees": []}),
            ("destination", {"calendar_id": "evil"}),
            ("after", {}),
            ("sendUpdates", "all"),
            ("unexpected", True),
        ):
            altered = copy.deepcopy(prepared)
            altered[key] = value
            with self.assertRaises(AppError):
                self.api.execute("calendar_create", altered)
        self.settings.calendar_id = "another@example.com"
        with self.assertRaises(AppError):
            self.api.execute("calendar_create", prepared)
        self.assertFalse(self.writes())

    def test_google_profile_scope_on_reads_and_writes(self):
        prepared = self.create()
        self.profile = "other@example.com"
        for invoke in (
            lambda: self.api.search_mail("hello"),
            lambda: self.api.read_mail("abc123"),
            lambda: self.api.list_events(START, END),
            lambda: self.api.execute("calendar_create", prepared),
        ):
            with self.assertRaises(AppError):
                invoke()
        self.assertFalse(self.writes())
        self.assertTrue(all(c[1].endswith("/profile") for c in self.calls))

    def test_read_caps_and_readonly(self):
        self.extra = {"messages": [{"id": str(i)} for i in range(11)], "nextPageToken": "next"}
        result = self.api.search_mail("subject:test")
        self.assertEqual(len(result["messages"]), 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(parse_qs(urlsplit(self.calls[-1][1]).query)["maxResults"], ["10"])
        self.extra = {"items": [{}] * 51}
        result = self.api.list_events(START, END)
        self.assertEqual(len(result["events"]), 50)
        self.assertTrue(result["truncated"])
        self.api.read_mail("abc123")
        self.assertFalse(self.writes())

    def test_notion_single_bounded_append_and_binding(self):
        text = "a" * 10000
        prepared = self.api.prepare("notion_append", {"text": text})
        self.assertEqual(prepared["destination"]["page_id"], PAGE)
        self.assertEqual(len(prepared["payload"]["children"]), 5)
        self.assertEqual("".join(prepared["after"]["paragraphs"]), text)
        self.api.execute("notion_append", prepared)
        method, url, h, data = self.writes()[0]
        self.assertEqual(method, "PATCH")
        self.assertEqual(url, "https://api.notion.com/v1/blocks/" + PAGE + "/children")
        self.assertEqual(h["Notion-Version"], "2026-03-11")
        self.assertEqual(data, prepared["payload"])
        self.token = "replacement"
        with self.assertRaises(AppError):
            self.api.execute("notion_append", prepared)
        self.assertEqual(len(self.writes()), 1)

    def test_notion_read_never_follows_links_or_nested_blocks(self):
        self.extra = {
            "results": [
                {
                    "id": "nested",
                    "type": "paragraph",
                    "has_children": True,
                    "paragraph": {"rich_text": [{"plain_text": "Visible"}]},
                },
                {"type": "child_page", "id": "other"},
                {"type": "link_to_page", "id": "linked"},
            ],
            "has_more": True,
        }
        result = self.api.read_notion()
        self.assertEqual(result["blocks"], [{"type": "paragraph", "text": "Visible"}])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(PAGE in c[1] for c in self.calls))

    def test_notion_changed_page_and_invalid_page_block_execution(self):
        prepared = self.api.prepare("notion_append", {"text": "hello"})
        for page in ("22345678-1234-1234-1234-123456789abc", "https://notion.so/evil"):
            self.settings.notion_page_id = page
            with self.assertRaises(AppError):
                self.api.execute("notion_append", prepared)
        self.assertFalse(self.writes())

    def test_mail_body_bound_and_attachment_omission(self):
        import base64

        encoded = base64.urlsafe_b64encode(b"x" * 21000).decode()
        self.extra = {
            "id": "abc123",
            "payload": {
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": encoded}},
                    {"mimeType": "text/plain", "filename": "secret.txt", "body": {"data": "c2VjcmV0"}},
                ]
            },
        }
        result = self.api.read_mail("abc123")
        self.assertEqual(len(result["body"]), 20000)
        self.assertTrue(result["truncated"])
        self.assertNotIn("secret", result["body"])
        self.assertFalse(self.writes())

    def test_ambiguous_write_not_retried(self):
        for action, args in (
            ("calendar_create", {"summary": "x", "start": START, "end": END}),
            ("notion_append", {"text": "hello"}),
        ):
            prepared = self.api.prepare(action, args)
            self.write_error = AppError("Uncertain outcome", uncertain=True)
            previous = len(self.writes())
            with self.assertRaises(AppError) as caught:
                self.api.execute(action, prepared)
            self.assertTrue(caught.exception.uncertain)
            self.assertEqual(len(self.writes()), previous + 1)


if __name__ == "__main__":
    unittest.main()
