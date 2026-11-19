"""Bounded, read-only Gmail adapter."""

from __future__ import annotations

import base64
from urllib.parse import quote, urlencode

from assistant_app.domain.models import JsonObject, Settings
from assistant_app.infrastructure.contracts import Request

from .common import GOOGLE, _fail, _identifier, _text
from .google import GoogleSession


class GmailAdapter:
    def __init__(self, settings: Settings, session: GoogleSession, request: Request) -> None:
        self.settings, self.session, self.request = settings, session, request

    def search_mail(self, query: str) -> JsonObject:
        """Read at most ten matches, with explicit paging disclosure."""
        _text(query, "mail query", 2000)
        h = self.session.headers()
        result = self.request(
            "GET", GOOGLE + "/gmail/v1/users/me/messages?" + urlencode({"q": query, "maxResults": 10}), headers=h
        )
        return {
            "messages": result.get("messages", [])[:10],
            "limit": 10,
            "truncated": bool(result.get("nextPageToken")) or len(result.get("messages", [])) > 10,
            "disclosure": "At most 10 matches; this is not an exhaustive mailbox search.",
        }

    def read_mail(self, message_id: str) -> JsonObject:
        """Read one message; attachments are never fetched, body output is capped."""
        _identifier(message_id)
        h = self.session.headers()
        result = self.request(
            "GET", GOOGLE + "/gmail/v1/users/me/messages/" + quote(message_id, safe="") + "?format=full", headers=h
        )
        payload = result.get("payload", {})
        texts, remaining, truncated = [], 20000, False
        stack = [payload]
        count = 0
        while stack and count < 100:
            part = stack.pop()
            count += 1
            stack.extend(reversed(part.get("parts", [])[:100]))
            body = part.get("body", {})
            if body.get("attachmentId") or part.get("filename"):
                truncated = True
                continue
            if part.get("mimeType") in ("text/plain", "text/html") and body.get("data"):
                try:
                    data = body["data"]
                    text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                except (ValueError, TypeError):
                    _fail("Malformed mail content.")
                texts.append(text[:remaining])
                truncated |= len(text) > remaining
                remaining = max(0, remaining - len(text))
        return {
            "id": result.get("id"),
            "snippet": result.get("snippet", "")[:2000],
            "headers": payload.get("headers", [])[:100],
            "body": "\n".join(texts),
            "truncated": truncated or bool(stack),
            "disclosure": "Attachments omitted; body limited to 20000 characters and 100 parts.",
        }
