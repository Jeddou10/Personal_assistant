"""Built-in tool schemas. No approval or arbitrary file/command capability."""


def _tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {k: {"type": "string", "description": v} for k, v in properties.items()},
            "required": list(required),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool(
        "search_mail",
        "Read-only Gmail search. Returns up to ten messages; narrow the query as needed.",
        {"query": "Gmail search expression"},
        ["query"],
    ),
    _tool(
        "read_mail",
        "Read a message from the connected Gmail account, no attachments.",
        {"message_id": "ID returned by search_mail"},
        ["message_id"],
    ),
    _tool(
        "list_events",
        "Read selected calendar appointments within a time interval.",
        {"time_min": "Inclusive ISO datetime with UTC offset", "time_max": "Exclusive ISO datetime with UTC offset"},
        ["time_min", "time_max"],
    ),
    _tool("read_notion", "Read the selected Notion page only. Results may be capped.", {}),
    _tool(
        "propose_calendar_create",
        "Prepare an appointment for UI approval. Does NOT create it. No guests, recurrence or all-day events.",
        {
            "summary": "Title",
            "start": "ISO datetime with explicit offset",
            "end": "ISO datetime with explicit offset",
            "description": "Plain text description",
            "location": "Location",
        },
        ["summary", "start", "end"],
    ),
    _tool(
        "propose_calendar_update",
        "Prepare changes to an ordinary personal timed event for UI approval. Does NOT update it.",
        {
            "event_id": "ID returned by list_events",
            "summary": "New title",
            "start": "New ISO datetime with explicit offset",
            "end": "New ISO datetime with explicit offset",
            "description": "New description",
            "location": "New location",
        },
        ["event_id"],
    ),
    _tool(
        "propose_calendar_delete",
        "Prepare deletion of one ordinary personal timed event for UI approval. Does NOT delete it.",
        {"event_id": "ID returned by list_events"},
        ["event_id"],
    ),
    _tool(
        "propose_notion_append",
        "Prepare plain text to append to the selected Notion page for UI approval. Does NOT write it.",
        {"text": "Exact text to append, at most 10000 characters"},
        ["text"],
    ),
]

READ_TOOL_NAMES = frozenset({"search_mail", "read_mail", "list_events", "read_notion"})
