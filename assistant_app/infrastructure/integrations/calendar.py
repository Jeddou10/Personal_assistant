"""Calendar previews, event restrictions, and conditional writes."""

from __future__ import annotations

import copy
from urllib.parse import quote, urlencode

from assistant_app.domain.models import JsonObject, Settings
from assistant_app.infrastructure.contracts import Request

from .common import FIELDS, GOOGLE, _fail, _text, _times
from .google import GoogleSession


class CalendarAdapter:
    def __init__(self, settings: Settings, session: GoogleSession, request: Request) -> None:
        self.settings, self.session, self.request = settings, session, request

    def _calendar(self) -> str:
        calendar = _text(self.settings.calendar_id, "selected calendar", 1024)
        # 'primary' is an alias, not a stable configured resource identifier.
        if calendar == "primary" or any(ord(c) < 32 for c in calendar):
            _fail("Select an explicit calendar ID.")
        return GOOGLE + "/calendar/v3/calendars/" + quote(calendar, safe="") + "/events"

    def list_events(self, time_min: str, time_max: str) -> JsonObject:
        """Read at most fifty events from the configured calendar only."""
        _times(time_min, time_max)
        url = self._calendar()
        h = self.session.headers()
        result = self.request(
            "GET",
            url
            + "?"
            + urlencode(
                {
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "maxResults": 50,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                }
            ),
            headers=h,
        )
        return {
            "events": result.get("items", [])[:50],
            "limit": 50,
            "truncated": bool(result.get("nextPageToken")) or len(result.get("items", [])) > 50,
            "disclosure": "At most 50 events; additional events may be omitted.",
        }

    def _safe_event(self, event: JsonObject, event_id: str) -> None:
        if (
            not isinstance(event, dict)
            or event.get("id") != event_id
            or not isinstance(event.get("etag"), str)
            or not event["etag"]
        ):
            _fail("Event identity or ETag missing.")
        if event.get("status") == "cancelled" or event.get("eventType", "default") != "default":
            _fail("Special or cancelled events cannot be changed.")
        if any(
            event.get(k)
            for k in (
                "recurrence",
                "recurringEventId",
                "originalStartTime",
                "attendees",
                "attendeesOmitted",
                "conferenceData",
                "hangoutLink",
            )
        ):
            _fail("Recurring, guest or conference events cannot be changed.")
        if event.get("organizer", {}).get("self") is not True:
            _fail("Externally organized or unverified events cannot be changed.")
        reminders = event.get("reminders", {})
        if reminders.get("useDefault") is not False or any(
            r.get("method") != "popup" for r in reminders.get("overrides", [])
        ):
            _fail("Email or inherited reminders cannot be changed.")
        start, end = event.get("start", {}), event.get("end", {})
        if "date" in start or "date" in end:
            _fail("All-day events cannot be changed.")
        _times(start.get("dateTime"), end.get("dateTime"))

    def record(self, action: str, args: JsonObject, before: JsonObject | None = None) -> JsonObject:
        self._calendar()
        account = _text(self.settings.gmail_account, "connected Gmail account", 320)
        payload = {k: ({"dateTime": v} if k in ("start", "end") else v) for k, v in args.items() if k in FIELDS}
        if action == "calendar_create":
            payload["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}]}
            after = copy.deepcopy(payload)
        else:
            self._safe_event(before, args["event_id"])
            after = copy.deepcopy(before)
            after.update(payload)
            _times(after["start"]["dateTime"], after["end"]["dateTime"])
        return {
            "action": action,
            "arguments": args,
            "destination": {"account": account, "calendar_id": self.settings.calendar_id},
            "event_id": args.get("event_id"),
            "etag": before.get("etag") if before else None,
            "before": copy.deepcopy(before),
            "after": None if action == "calendar_delete" else after,
            "payload": None if action == "calendar_delete" else payload,
            "sendUpdates": "none",
            "disclosure": "Delete this event."
            if action == "calendar_delete"
            else "No invitations or email reminders. Calendar-level notification settings remain outside app control.",
        }

    def prepare(self, action: str, args: JsonObject) -> JsonObject:
        url = self._calendar()
        h = self.session.headers()
        before = None
        if action != "calendar_create":
            before = self.request("GET", url + "/" + quote(args["event_id"], safe=""), headers=h)
        return self.record(action, args, before)

    def execute(self, action: str, args: JsonObject, expected: JsonObject) -> JsonObject:
        url = self._calendar()
        h = self.session.headers()
        method = "POST"
        if action != "calendar_create":
            url += "/" + quote(args["event_id"], safe="")
            current = self.request("GET", url, headers=h)
            self._safe_event(current, args["event_id"])
            if current["etag"] != expected["etag"]:
                _fail("Event changed since preview; prepare a new proposal.")
            h["If-Match"] = expected["etag"]
            method = "DELETE" if action == "calendar_delete" else "PATCH"
        result = self.request(method, url + "?sendUpdates=none", headers=h, data=expected["payload"])

        return result
