"""Shared input validation for configured service operations."""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any, NoReturn

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject

GOOGLE = "https://www.googleapis.com"
NOTION = "https://api.notion.com/v1"
FIELDS = {"summary", "start", "end", "description", "location"}


def _fail(message: Any) -> NoReturn:
    raise AppError(message)


def _text(value: Any, name: str, limit: int = 10000, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        _fail(f"Invalid {name}.")
    return value


def _timestamp(value: Any) -> datetime:
    _text(value, "timestamp", 100)
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value
    ) or value.endswith("-00:00"):
        _fail("Timestamps require an explicit UTC offset and seconds.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("Invalid timestamp.")


def _times(start: str, end: str) -> None:
    if _timestamp(end) <= _timestamp(start):
        _fail("End must be after start.")


def _identifier(value: Any) -> str:
    _text(value, "resource identifier", 1024)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        _fail("Invalid resource identifier.")
    return value


def _arguments(action: str, arguments: JsonObject) -> JsonObject:
    if not isinstance(arguments, dict):
        _fail("Arguments must be an object.")
    args = copy.deepcopy(arguments)
    required = {
        "calendar_create": {"summary", "start", "end"},
        "calendar_update": {"event_id"},
        "calendar_delete": {"event_id"},
        "notion_append": {"text"},
    }
    allowed = {
        "calendar_create": FIELDS,
        "calendar_update": FIELDS | {"event_id"},
        "calendar_delete": {"event_id"},
        "notion_append": {"text"},
    }
    if action not in allowed or not required[action] <= args.keys() or args.keys() - allowed[action]:
        _fail("Unsupported action or arguments; destinations come only from settings.")
    for key, value in args.items():
        if key == "event_id":
            _identifier(value)
        elif key in ("start", "end"):
            _timestamp(value)
        else:
            _text(value, key, empty=key in ("description", "location"))
    if action == "calendar_update" and not (args.keys() & FIELDS):
        _fail("An update requires at least one changed field.")
    if "start" in args and "end" in args:
        _times(args["start"], args["end"])
    return args
