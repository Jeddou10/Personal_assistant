# Read-only MCP CLI

This alpha interface is opt-in and separate from the desktop launcher. It advertises MCP protocol version `2025-11-25` and implements a bounded stdio subset, not every MCP feature. Live host/account interoperability still needs verification with your own configuration.

## Launch and trust

Configure the desired account, calendar and Notion page in the desktop app first. A trusted MCP host can then launch this command from the source checkout (or an environment where the package is installed):

```powershell
python -m assistant_app.mcp
```

The host connects stdin/stdout; this is not an interactive chat prompt. Frames are newline-delimited UTF-8 JSON-RPC 2.0. Stdout is reserved for protocol responses and diagnostics go to stderr. EOF ends the session. There is no network listener, background startup service or outbound MCP client. The desktop does not automatically start this CLI.

Launching it grants the host access to selected private integration reads without a desktop approval prompt for each read. The host may log results or forward them to cloud models. Read-only does not mean offline or confidential from the host. Actual reads contact Google/Notion and may refresh locally stored OAuth tokens; the server itself does not invoke an AI provider. See [privacy](../PRIVACY.md) and [security](../SECURITY.md).

## Supported operations

Initialize with `initialize` (including `protocolVersion`, `capabilities` and `clientInfo`), inspect the returned version, then send `notifications/initialized` before listing or calling tools. The server returns `2025-11-25`; a host unable to use that version should disconnect. Supported methods are `initialize`, `ping`, `tools/list` and `tools/call`, plus the initialized notification. Unknown notifications are ignored and never dispatch tool calls.

| Tool | Required string arguments | Scope |
| --- | --- | --- |
| `search_mail` | `query` | Connected Gmail account; capped results |
| `read_mail` | `message_id` | Connected Gmail account; no attachments |
| `list_events` | `time_min`, `time_max` | Selected calendar; ISO datetimes with UTC offsets |
| `read_notion` | None | Selected Notion page; bounded top-level text |

Unknown tools, extra arguments and invalid argument types are rejected. Each call uses current stored selections; results are discarded if settings change during the read. Initialization and tool discovery do not create storage or inspect credentials.

There are no write, proposal, approval, preference-editing, filesystem, retrieval or shell tools. There are no resources, roots negotiation, prompts, tasks, pagination, batches or outbound requests. Desktop Calendar/Notion approval controls are a separate application path and are not available through MCP.

## Bounds and failures

The implementation limits each input frame to 64 KiB and each output frame to 256 KiB. A server instance accepts at most 2,000 input frames, 8 MiB total input, 16 MiB total output, 60 tool attempts per rolling minute and 500 per instance. Integration response limits also apply. A host must tolerate terminal EOF when transport budgets are exhausted.

Malformed JSON, duplicate keys, nonfinite numbers, batches and unterminated frames are rejected. Oversized input ends the session. Tool failures return a generic error without exposing exception text or credentials. Source text returned successfully is still untrusted: messages, event descriptions and page text must not become instructions or permission to perform actions.

The protocol adapter receives an injected read dispatcher. `bootstrap.create_read_service()` supplies lazy application wiring; `ReadService` and the shared tool registry enforce selected scope and argument validation. Injected dependencies and the local host are trusted code, not isolated plugins.
