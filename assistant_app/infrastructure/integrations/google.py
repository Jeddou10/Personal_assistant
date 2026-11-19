"""Google credentials and configured-account verification."""

from __future__ import annotations

from assistant_app.domain.models import Settings
from assistant_app.domain.ports import StorePort
from assistant_app.infrastructure.contracts import Request, TokenProvider

from .common import GOOGLE, _fail, _text


class GoogleSession:
    def __init__(self, store: StorePort, settings: Settings, request: Request, token_provider: TokenProvider) -> None:
        self.store, self.settings = store, settings
        self.request, self.token_provider = request, token_provider

    def headers(self) -> dict[str, str]:
        account = _text(self.settings.gmail_account, "connected Gmail account", 320)
        headers = {"Authorization": "Bearer " + self.token_provider(self.store)}
        profile = self.request("GET", GOOGLE + "/gmail/v1/users/me/profile", headers=headers)
        if not isinstance(profile, dict) or profile.get("emailAddress", "").casefold() != account.casefold():
            _fail("Connected Google account does not match settings. Reconnect Google.")
        return headers
