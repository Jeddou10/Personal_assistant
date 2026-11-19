"""Desktop use cases with injected storage, conversation and account services."""

from collections.abc import Callable
from dataclasses import replace
from functools import wraps
from urllib.parse import urlsplit
from uuid import UUID

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import Settings
from assistant_app.domain.ports import StorePort

from .calendar import calendar_snapshot, day_window


def serialized_configuration(method):
    """Use the same operation gate as chat/approval, including OAuth workers."""

    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.engine.configuration_change():
            return method(self, *args, **kwargs)

    return guarded


class ApplicationController:
    def __init__(self, store: StorePort, engine, connect_google: Callable, list_calendars: Callable):
        self.store = store
        self.engine = engine
        self._connect_google = connect_google
        self._list_calendars = list_calendars

    def load_settings(self) -> Settings:
        return self.store.load_settings()

    def calendar_today_tomorrow(self, today=None):
        start, end = day_window(today)
        result = self.engine.read_tool("list_events", {"time_min": start.isoformat(), "time_max": end.isoformat()})
        return calendar_snapshot(result, start.date())

    @serialized_configuration
    def save_settings(self, settings: Settings, secrets: dict[str, str] | None = None) -> None:
        if settings.provider not in ("anthropic", "ollama") or not settings.model.strip():
            raise AppError("Choose a provider and enter its model ID.")
        settings = replace(settings, model=settings.model.strip(), local_url=settings.local_url.strip().rstrip("/"))
        if settings.provider == "ollama":
            try:
                url = urlsplit(settings.local_url)
                if (
                    url.scheme != "http"
                    or url.hostname not in ("127.0.0.1", "::1")
                    or url.username
                    or url.password
                    or url.path
                    or url.query
                    or url.fragment
                    or (url.port is not None and not 1 <= url.port <= 65535)
                ):
                    raise ValueError()
            except ValueError:
                raise AppError("Local Ollama must use an HTTP literal loopback address and a valid port.") from None
        if settings.notion_page_id:
            try:
                settings = replace(settings, notion_page_id=str(UUID(settings.notion_page_id.strip())))
            except ValueError:
                raise AppError("Enter the Notion page UUID, not the complete page URL.") from None
        values = secrets or {}
        if set(values) - {"anthropic_key", "notion_token"} or any(not isinstance(v, str) for v in values.values()):
            raise AppError("Unsupported credential fields.")
        for name, value in values.items():
            if value.strip():
                self.store.set_secret(name, value.strip())
        self.store.save_settings(settings)

    @serialized_configuration
    def connect_google(self, client_file: str) -> str:
        account = self._connect_google(self.store, client_file)
        self.store.save_settings(replace(self.load_settings(), gmail_account=account, calendar_id=""))
        return account

    @serialized_configuration
    def list_calendars(self):
        return self._list_calendars(self.store)

    @serialized_configuration
    def select_calendar(self, calendar_id: str) -> None:
        if not isinstance(calendar_id, str) or not calendar_id or calendar_id == "primary":
            raise AppError("Choose a specific calendar from the list.")
        self.store.save_settings(replace(self.load_settings(), calendar_id=calendar_id))

    @serialized_configuration
    def disconnect(self) -> None:
        for name in ("anthropic_key", "notion_token", "google_tokens", "google_client"):
            self.store.set_secret(name, "")
        self.store.save_settings(replace(self.load_settings(), gmail_account="", calendar_id="", notion_page_id=""))

    def load_preferences(self) -> list[str]:
        return self.store.load_preferences()

    @serialized_configuration
    def save_preferences(self, preferences: list[str]) -> None:
        if (
            not isinstance(preferences, list)
            or len(preferences) > 30
            or any(not isinstance(p, str) or not p.strip() or len(p) > 500 for p in preferences)
        ):
            raise AppError("Keep memory small: at most 30 nonempty preferences, 500 characters each.")
        self.store.save_preferences(preferences)
