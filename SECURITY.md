# Security

## Reporting a vulnerability

Do not put API keys, tokens, private content, or exploit details in a public issue.

If GitHub's **Security → Report a vulnerability** control is available, use it for a private report. Private vulnerability reporting must be enabled by the repository owner before this route exists. If it is unavailable, open a minimal issue asking for a private security contact, without vulnerability details. This repository does not currently publish a dedicated security email address or promise a response time.

Include the affected version, environment, impact, and a minimal reproduction using fabricated data once a private channel is available. If you exposed a real credential, revoke it at the provider; deleting a file alone does not revoke access.

## Support status

This is an unreleased alpha. Security fixes target the current development branch; no released-version support window exists yet. Automated tests and reviews are not a security audit or a guarantee of safety.

## Boundaries enforced by the app

- Gmail uses a read-only OAuth scope and has no sending implementation.
- The model can read configured resources and propose writes. Only the UI can approve an immutable, session-bound proposal for one dispatch.
- Calendar update/delete operations use ETags and restricted event types. Notion appends lack a transactional page-version precondition; uncertain outcomes require inspection.
- Remote data is treated as untrusted content. Prompt instructions alone are not the permission boundary; code validates destinations and operations.
- Redirects and HTTP proxies are disabled in the app's transport. Cloud requests use HTTPS. Local inference accepts literal loopback addresses only and rejects known cloud-model aliases.
- Transport deadlines cover socket operations. OS-level DNS resolution is synchronous and can outlast the budget; expired requests are rejected before later dispatch.
- Secrets and saved preferences use Windows DPAPI. The app does not intentionally persist transcripts, raw retrieved content, or request/response logs.

## Optional MCP boundary

The CLI is an explicit opt-in for one trusted local stdio host and advertises protocol `2025-11-25`. It exposes only `search_mail`, `read_mail`, `list_events` and `read_notion`, with validated arguments and bounded frames, sessions and call rates. It has no network listener, external MCP client, write/proposal/approval tools, arbitrary file access or shell execution. Actual integration reads can contact Google/Notion and refresh credentials; discovery does not access stored credentials.

The host can read private content without a desktop approval prompt and can retain or forward it. Stdio is not an authentication boundary against other programs running as your user. Trust the host and its configured services before enabling it. Tool output remains untrusted source material; host/model instructions must not promote returned text into authority. See [MCP details](docs/MCP.md).

## Local retrieval boundary

The separate [retrieval API](docs/RAG.md) is not a model tool or UI feature. Its default reader fails closed outside Windows and requires explicit roots and files. It uses held handles to check local paths, root identity, containment and file metadata before and after bounded reads. It rejects reparse points (including symlinks, junctions and cloud placeholders), UNC/device paths, remote drives, alternate streams, unsupported text formats and selected secret/generated path patterns.

Filename filters cannot prove a document contains no secrets. Only select intended material. Query-time revalidation removes changed or inaccessible sources; it is not continuous monitoring or an atomic multi-file snapshot. Citations identify source bytes and lines, not truth or safety. Snippets and filenames remain untrusted even when citation headers are escaped. Injected loaders/indexes are trusted code and must preserve the security contract; a future model hookup must maintain permission enforcement independently of prompts.

## Limits of the threat model

The app trusts the operating system, your Windows user session, its Python installation, and the configured local model server. DPAPI does not protect against software already running as the same user. A modified local inference server can misrepresent what it does. Cloud providers control their own data handling.

Google Calendar permissions are granted at account level; selection of one calendar is an application-enforced restriction. A Notion integration may have broader provider-side access than this app uses. Share only intended pages and review your provider grants.

Repository checks catch common credential patterns and private filenames, but are not a comprehensive secret scanner. Review files manually before publishing and enable platform secret scanning/private reporting when available. Never store credentials in source, `.env` files intended for publication, screenshots, test fixtures or build artifacts.
