# Changelog

Notable user-facing changes are recorded here. Version `0.1.0a1` is an alpha package version; no release has been published yet.

## Unreleased

### Added

- Daily shortcuts for an Important-mail summary and a direct, read-only today/tomorrow calendar view without AI inference.

- Windows floating soccer-ball launcher and text chat with a cream-and-sage interface.
- Simplified chat controls, conditional approval panel, conversation search, explicit answer copying and editable quick-question drafts.
- Bounded auto-growing composer, elapsed progress feedback and failed-request recovery that preserves newer drafts.
- Nonblocking worker cleanup, startup-failure recovery and protection against accidental draft replacement.
- Anthropic chat and optional local Ollama inference with no automatic model downloads or fallback.
- Read-only Gmail, one selected Google Calendar, and one selected Notion page.
- Explicit approve/revise/reject controls for Calendar writes and Notion appends.
- Transient sessions and explicit, Windows-encrypted preference storage.
- Bounded transport, OAuth desktop authorization, scoped integration requests and automated tests.
- Python package metadata, MIT license, contributor/security/privacy guidance, and CI/release checks.

### Refactored

- Split domain contracts, application use cases, Tk frontend and infrastructure adapters; concrete wiring lives in `bootstrap.py` and legacy import paths retain compatibility exports.
- Added injected storage, provider, integration and transport boundaries and shared validated read-tool routing.
- Added an opt-in, read-only MCP stdio CLI advertising protocol `2025-11-25`, with no network listener or write tools.
- Added an executable Windows local retrieval foundation: explicit roots/files, bounded safe text reads, lexical in-memory indexing and line/hash citations. No UI/tool hookup, automatic indexing, embedding downloads or Drive integration.

### Known limitations

- Windows-only private storage; no standalone installer.
- Live provider/account verification still requires user-supplied credentials.
- Restricted Calendar event types and top-level Notion text only.
- File retrieval is developer-only; chat/UI/tool access, semantic search, persistent indexing and Drive access are not implemented. Additional AI/calendar adapters remain future work.
