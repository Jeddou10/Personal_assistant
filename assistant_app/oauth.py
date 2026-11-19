"""Compatibility exports; use assistant_app.infrastructure.oauth."""

from .infrastructure.oauth import SCOPES, connect_google, get_access_token, list_calendars

__all__ = ["get_access_token", "connect_google", "list_calendars", "SCOPES"]
