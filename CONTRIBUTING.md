# Contributing

Thanks for helping improve Personal Assistant. Small, focused changes are easiest to review. For a larger feature, describe the problem and intended behavior in an issue before building it.

## Development environment

Use Windows with Python 3.11+ and Tkinter. Create a virtual environment and install the development extras:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Launch with `python -m assistant_app` from the repository, or use the virtual environment's `personal-assistant.exe` entry point. Never put real keys, OAuth JSON files, mailbox content, calendar exports or Notion page content in the repository.

## Before opening a pull request

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/run_tests.py --headless
.\.venv\Scripts\python.exe scripts/check_repository.py
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist/*
.\.venv\Scripts\python.exe scripts/check_dist.py
```

For UI changes, also run the complete suite without `--headless` and manually inspect the app at its minimum window size. Do not record screenshots containing private account data. The hidden mode deliberately skips two UI tests that require visible windows, including their other assertions; it is not a replacement for visual review.

Explain the problem, resulting behavior, validation, and remaining limits in your PR. Add regression tests when changing permission enforcement, API contracts, state transitions or concurrency. Documentation-only changes generally do not need new tests. Avoid unrelated refactors and generated build output.

## Design boundaries

- Keep the app lightweight; new runtime dependencies need a concrete benefit.
- Never give the model an approval/execution, email-send, shell, or arbitrary filesystem tool.
- Construct API destinations from selected settings, not model-supplied URLs.
- Writes must use immutable previews, explicit UI approval and single-use dispatch.
- Preserve scope checks, Calendar ETags, timeout/uncertain-outcome handling and no automatic write retries.
- Never fall back from local to cloud inference. Do not download models implicitly.
- Keep conversations transient and save preferences only through explicit user action.
- Keep Tk operations on the main thread. Remote work belongs on workers.
- Test with fake data. An integration test using a real account is a separate manual step, never a CI requirement.

## Refactor and extension guidance

Keep models and dependency contracts in `domain/`, use cases and approval policy in `application/`, Tk widgets in `frontend/`, and service/storage implementations in `infrastructure/`. Wire concrete adapters through `bootstrap.py`. Preserve the documented legacy compatibility exports when moving implementations; prefer injected stores, completion callables, integration factories, transports and clocks in new tests.

MCP is explicitly launched, read-only stdio. Preserve its allowlist, initialization rules, bounded framing and lazy credential access; do not expose proposal, approval or write tools. A host receiving private content is a separate trust boundary. See [MCP.md](docs/MCP.md).

Retrieval is an executable foundation, not a shipped chat feature. Use temporary synthetic UTF-8 files and explicitly selected roots in examples/tests. Preserve Windows handle validation, path exclusions, resource budgets, scope invalidation and citation provenance. Do not crawl personal directories, index private fixtures, download embeddings or add automatic UI/tool hooks. New loader/index implementations must honor their contracts; the `Embedder` protocol is future-facing and unused by lexical search. See the [developer example](docs/RAG.md).

Validate documentation examples without credentials, live account writes or private paths. Do not describe fake-service tests as live integration verification.

## Communication and licensing

Follow the [code of conduct](CODE_OF_CONDUCT.md). Report suspected vulnerabilities using [SECURITY.md](SECURITY.md), not a public issue containing exploit details or private data.

By submitting a contribution, you agree that it can be distributed under this project's MIT license. Include the source and license of any third-party material you add; do not submit code or assets you are not entitled to share.
