"""Route validated operations to composed service adapters.

Prepared records are review data; approval enforcement belongs to the engine.
"""

from __future__ import annotations

from assistant_app.domain.models import JsonObject, Settings
from assistant_app.domain.ports import StorePort
from assistant_app.infrastructure.contracts import Request, TokenProvider

from ..net import request_json
from ..oauth import get_access_token
from .calendar import CalendarAdapter
from .common import _arguments, _fail
from .gmail import GmailAdapter
from .google import GoogleSession
from .notion import NotionAdapter


class Integrations:
    def __init__(
        self,
        store: StorePort,
        settings: Settings,
        request: Request | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self.store, self.settings = store, settings
        request = request if request is not None else request_json
        token_provider = token_provider if token_provider is not None else get_access_token
        session = GoogleSession(store, settings, request, token_provider)
        self.gmail = GmailAdapter(settings, session, request)
        self.calendar = CalendarAdapter(settings, session, request)
        self.notion = NotionAdapter(store, settings, request)

    def search_mail(self, query: str) -> JsonObject:
        return self.gmail.search_mail(query)

    def read_mail(self, message_id: str) -> JsonObject:
        return self.gmail.read_mail(message_id)

    def list_events(self, time_min: str, time_max: str) -> JsonObject:
        return self.calendar.list_events(time_min, time_max)

    def read_notion(self) -> JsonObject:
        return self.notion.read_notion()

    def prepare(self, action: str, arguments: JsonObject) -> JsonObject:
        args = _arguments(action, arguments)
        adapter = self.notion if action == "notion_append" else self.calendar
        return adapter.prepare(action, args)

    def execute(self, action: str, prepared: JsonObject) -> JsonObject:
        if not isinstance(prepared, dict):
            _fail("Invalid prepared proposal.")
        args = _arguments(action, prepared.get("arguments"))
        adapter = self.notion if action == "notion_append" else self.calendar
        expected = adapter.record(action, args, prepared.get("before"))
        if prepared != expected:
            _fail("Proposal content or configuration changed; prepare a new proposal.")
        result = adapter.execute(action, args, expected)
        return {"action": action, "destination": expected["destination"], "result": result}
