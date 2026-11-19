# Architecture

Personal Assistant is a single-user Windows application built with Python and Tkinter. It has no persistent chat service and no runtime dependency outside the standard library.

## Layers and composition

```text
Tk frontend -> ApplicationController -> Engine / ApprovalService / ToolRegistry
MCP stdio   -> ReadService            -> validated read handlers
                          |
                   domain ports/models
                          |
bootstrap wires infrastructure: providers, integrations, OAuth, transport, storage

RetrievalService -> DocumentLoader + Index (separate developer API)
```

| Layer | Responsibility |
| --- | --- |
| `domain/` | Immutable settings/proposal records, limits, errors and storage/provider/integration contracts; no Tk or concrete service wiring |
| `application/` | Conversation orchestration, controller use cases, approval policy, prompts, tool definitions/validation and read-only service |
| `frontend/` | Launcher, chat, settings, preferences and approval views, theme and task coordination; widget updates stay on the Tk thread |
| `infrastructure/` | DPAPI storage, OAuth, bounded HTTP transport, Anthropic/Ollama providers and Gmail/Calendar/Notion adapters |
| `bootstrap.py` | Composition root selecting concrete adapters for the desktop and lazy MCP read service |
| `mcp/` | Bounded read-only stdio protocol adapter; no Tk, model inference or write dispatch |
| `retrieval/` | Independent explicit local text ingestion, safe Windows reader, chunking, lexical index and citations |

The application engine accepts a store, completion callable, integration instance or factory, clock and runtime limits. The controller receives its engine and account-service callables. Integration adapters accept request and token-provider dependencies. Tests can inject fakes without live credentials or patching compatibility-module globals. These are trusted Python dependencies, not a sandbox for third-party code.

## Stable compatibility exports

Existing imports remain available while implementations move into layers:

| Legacy module | Compatibility surface |
| --- | --- |
| `assistant_app.engine` | `Engine` constructor wiring default adapters; `Proposal`, `TOOLS`, `SYSTEM` |
| `assistant_app.ui` | `AssistantUI`, `main`, `draw_cat`, `preview_text` |
| `assistant_app.storage` | `Store`, `Settings` |
| `assistant_app.providers` | `complete` |
| `assistant_app.integrations` | `Integrations` |
| `assistant_app.oauth` | `get_access_token`, `connect_google`, `list_calendars`, `SCOPES` |
| `assistant_app.net` | `request_json`, `MAX_BYTES`, `AppError` |

The desktop launch command remains `python -m assistant_app`. Prefer layer imports and explicit injection for new code; compatibility exports preserve existing callers, not every private implementation detail or monkeypatch target. The project remains alpha.

## Optional interfaces

`python -m assistant_app.mcp` explicitly starts a read-only stdio session advertising MCP `2025-11-25`. Discovery does not load credentials; actual calls load current selections and reject stale results if settings change. It offers four integration reads, no write/proposal/approval tools, and no network listener. Provider API reads still use the network. See [MCP.md](MCP.md) for host trust and protocol limits.

The [retrieval foundation](RAG.md) is executable independently but is not connected to the UI, engine tools or MCP. A caller must select Windows roots and individual text files. The default index is lexical and in memory, with source revalidation and line/hash citations. There is no crawler, embedding download, persistent index or Drive adapter. `DocumentLoader`, `Index` and future `Embedder` contracts leave room for later implementations without enabling them today.

## State and approval

One disposable conversation is active at a time. Model calls and remote operations run on workers; a queue carries results back to the Tk thread. Configuration/session controls are blocked while work is in progress. The UI reaps the worker before permitting close/reset.

The application controller also shares the engine's operation gate, so configuration changes cannot race with approved writes even outside the UI. Reset rebuilds scoped adapters through their factory before replacing session state. Directly injected adapters cannot reset into changed configuration without a factory; construct a new engine instead.

Tools expose reads and proposal creation. There is no model-visible approval, direct execution, persistent-memory write, email-send or arbitrary file/shell capability. Proposals bind canonical content to the session and configuration and expire after ten minutes. An approval changes state before dispatch, so repeated clicks cannot reuse it.

Calendar writes revalidate restrictions and use conditional updates/deletes. Notion appends are bounded to one request, but the API lacks an atomic page-version condition. Ambiguous write failures remain unknown, are not replayed, and block fresh proposals in that session until the user inspects the destination and starts another session.

## Persistence and packaging

The package contains only application code and license metadata. Source distributions also include public documentation, tests and development scripts. Runtime data belongs under Local AppData, not the repository or installed package. Internal local notes, credentials, caches, build output and virtual environments are excluded from version control and distribution.

Tests use fake network responses and temporary stores. Packaging checks inspect archive contents, and CI installs the built wheel for an import/entry-point smoke test. Public-service or private-account connectivity is not implied by unit-test success.

See [SECURITY.md](../SECURITY.md) for trust boundaries and [PRIVACY.md](../PRIVACY.md) for data flows.
