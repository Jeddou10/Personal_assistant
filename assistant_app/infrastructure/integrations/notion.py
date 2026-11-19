"""Scoped Notion reads and bounded append operations."""

from __future__ import annotations

import hashlib
import uuid

from assistant_app.domain.models import JsonObject, Settings
from assistant_app.domain.ports import StorePort
from assistant_app.infrastructure.contracts import Request

from .common import NOTION, _fail, _text


class NotionAdapter:
    def __init__(self, store: StorePort, settings: Settings, request: Request) -> None:
        self.store, self.settings, self.request = store, settings, request

    def _notion(self) -> tuple[str, dict[str, str]]:
        try:
            page = str(uuid.UUID(self.settings.notion_page_id))
        except (ValueError, TypeError, AttributeError):
            _fail("Select a valid Notion page UUID.")
        token = self.store.get_secret("notion_token")
        _text(token, "Notion credential", 10000)
        return page, {"Authorization": "Bearer " + token, "Notion-Version": "2026-03-11"}

    def _page(self, page: str, headers: dict[str, str] | None) -> JsonObject:
        result = self.request("GET", NOTION + "/pages/" + page, headers=headers)
        try:
            same = str(uuid.UUID(result.get("id", ""))) == page
        except (ValueError, TypeError, AttributeError):
            same = False
        if not same or result.get("object") != "page" or result.get("archived") or result.get("in_trash"):
            _fail("Selected Notion page is unavailable.")
        return result

    def read_notion(self) -> JsonObject:
        """Read only top-level ordinary blocks; never follow linked/child pages."""
        page, h = self._notion()
        self._page(page, h)
        result = self.request("GET", NOTION + "/blocks/" + page + "/children?page_size=100", headers=h)
        ordinary = {
            "paragraph",
            "heading_1",
            "heading_2",
            "heading_3",
            "bulleted_list_item",
            "numbered_list_item",
            "quote",
            "callout",
            "to_do",
            "toggle",
            "code",
        }
        blocks, remaining, omitted = [], 20000, False
        for block in result.get("results", [])[:100]:
            kind = block.get("type")
            if kind not in ordinary:
                omitted = True
                continue
            text = "".join(
                r.get("plain_text", r.get("text", {}).get("content", ""))
                for r in block.get(kind, {}).get("rich_text", [])
            )
            blocks.append({"type": kind, "text": text[:remaining]})
            omitted |= bool(block.get("has_children")) or len(text) > remaining
            remaining = max(0, remaining - len(text))
        return {
            "page_id": page,
            "blocks": blocks,
            "truncated": omitted or bool(result.get("has_more")) or len(result.get("results", [])) > 100,
            "disclosure": "Only top-level ordinary text, up to 100 blocks and 20000 characters. Nested content and linked/child pages are not followed.",
        }

    def record(self, action: str, args: JsonObject, before: JsonObject | None = None) -> JsonObject:
        page, h = self._notion()
        chunks = [args["text"][i : i + 2000] for i in range(0, len(args["text"]), 2000)]
        payload = {
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
                }
                for chunk in chunks
            ]
        }
        return {
            "action": action,
            "arguments": args,
            "destination": {"page_id": page},
            "before": None,
            "after": {"append_text": args["text"], "paragraphs": chunks},
            "payload": payload,
            "credential_fingerprint": hashlib.sha256(h["Authorization"].encode()).hexdigest(),
            "disclosure": "Append only. Notion cannot condition this write on page version. An ambiguous result requires inspection before a new proposal.",
        }

    def prepare(self, action: str, args: JsonObject) -> JsonObject:
        page, h = self._notion()
        self._page(page, h)
        return self.record(action, args)

    def execute(self, action: str, args: JsonObject, expected: JsonObject) -> JsonObject:
        page, h = self._notion()
        if (
            page != expected["destination"]["page_id"]
            or hashlib.sha256(h["Authorization"].encode()).hexdigest() != expected["credential_fingerprint"]
        ):
            _fail("Notion credentials changed; prepare a new proposal.")
        self._page(page, h)
        return self.request("PATCH", NOTION + "/blocks/" + page + "/children", headers=h, data=expected["payload"])
