# Personal Assistant

A small Windows desktop companion for your inbox, calendar, and notes. Open the floating soccer ball to chat, choose a cloud or local AI provider, and approve every proposed change before it happens.

**Status: alpha.** Local automated tests cover the core behavior; live Anthropic, Google, Notion, and Ollama connections require your own credentials or local model and have not all been verified end to end. It is not affiliated with those providers.

## What it does

| Feature | Access |
| --- | --- |
| Gmail search and summaries | Read-only, one connected account |
| Google Calendar | One selected calendar; writes require a preview and approval |
| Notion | Read one selected page; append approved text |
| Anthropic Claude | Cloud inference with your API key |
| Ollama | Optional local inference; no automatic downloads or cloud fallback |
| Preferences | Only rules you explicitly save |

The desktop UI includes a draggable soccer ball icon, a cream-and-sage chat view, independent sessions, and approve/revise/reject controls. It has no email-sending, shell-execution, or filesystem-reading tools.

Two developer interfaces are available separately: an explicitly launched [read-only MCP CLI](docs/MCP.md), and an executable [local retrieval foundation](docs/RAG.md). Neither starts with the desktop app. Retrieval is not connected to chat, the UI, or model/MCP tools; no personal files are indexed automatically.

## Quick start

Requires **Windows and Python 3.11+ with Tkinter**. The application itself uses only the Python standard library. No package installation is needed to run from source.

```powershell
git clone https://github.com/Jeddou10/Personal_assistant.git
cd Personal_assistant
python -m assistant_app
```

If you downloaded a ZIP, extract it and run the last command from the extracted project folder. You can also use `./run.ps1`. The app runs only when launched; it installs no startup service.

Click the ball to show/hide chat, drag it to move it, or right-click for the menu. **Ctrl+Enter** sends a message. Closing chat keeps the ball available; **Quit** stops the app.

The chat includes **New chat**, **Menu**, **Send**, and two daily shortcuts: **Important mail today** runs an AI summary of today's Gmail Important messages; **Calendar: today & tomorrow** opens a read-only two-day calendar with timed and all-day events. Calendar reads do not use the AI provider. Both use your computer's local days and preserve an unsent composer draft. Menu holds Settings, explicit preferences, editable quick questions, conversation search, copy-last-answer, and edit-last-request. **Ctrl+F** searches only this session; Enter in the composer inserts a newline. Approval controls appear when there are changes to inspect.

You can draft your next message while a request runs. Failed requests are restored only when the composer is empty; a newer draft is preserved. Nothing is automatically retried or saved as chat history. Copying an answer is an explicit action and places that text on the system clipboard.

Open **Settings** to add your Anthropic key or select an already-installed Ollama model, connect Google, choose a calendar, and configure a Notion page. Keep keys out of chat and source files. See the [setup guide](https://github.com/Jeddou10/Personal_assistant/blob/main/docs/SETUP.md) for consent screens, scopes, page sharing, and troubleshooting.

### Optional installation in a virtual environment

This adds a `personal-assistant` launcher. Installing may download small build tools; it does not download AI models.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\personal-assistant.exe
```

Python/Tkinter must come from your Python installation. No standalone installer or published PyPI package is provided yet.

## You control changes and memory

The AI can **propose** Calendar or Notion writes. It cannot approve them. Review the exact destination and content, then approve, request changes, or reject. Approval is single-use, expires after ten minutes, and is invalidated by settings/session changes. An ambiguous network result is never automatically retried.

New sessions discard earlier conversation context. **Remember last message** opens an editable draft; only **Save preferences** persists it. Ordinary chat does not automatically become memory.

**Cloud mode sends relevant messages, saved preferences, and retrieved content to Anthropic.** Local mode keeps inference on a configured loopback service, but requested Google and Notion operations still use those cloud services. Credentials and explicit preferences are Windows-encrypted under `%LOCALAPPDATA%\PersonalAssistant`. Read the [privacy policy](https://github.com/Jeddou10/Personal_assistant/blob/main/PRIVACY.md) and [security boundaries](https://github.com/Jeddou10/Personal_assistant/blob/main/SECURITY.md).

## Current limits

- Windows only; local validation was performed with Python 3.11. CI is configured to check additional Python versions when run on GitHub.
- Gmail reads omit attachments. Search, event listings, page reads, payload sizes, and tool loops are capped with omission notices.
- Calendar writes support ordinary personal timed events with explicit UTC offsets. Guest, recurring, all-day, conference, externally organized, and inherited/email-reminder events cannot be changed in this version.
- Notion supports top-level text reading and appending paragraphs; no database queries or editing existing blocks.
- Local file retrieval is a Windows-only developer API with explicit root/file selection, text loading, lexical search and in-memory citations. UI/tool integration, embeddings, persistent indexes and Google Drive access remain future work.
- A separate Notion Calendar integration and an OpenAI/Codex adapter are future work.
- Provider behavior, availability, pricing, and account setup are outside this application's control.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/run_tests.py --headless
.\.venv\Scripts\python.exe scripts/check_repository.py
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist/*
.\.venv\Scripts\python.exe scripts/check_dist.py
```

`--headless` keeps native windows withdrawn and skips two UI tests that require visible windows (including their other assertions). For the complete native UI suite, run `python scripts/run_tests.py` in an interactive Windows session. Tests use fake service responses and temporary local storage; they do not require API keys or perform live account writes.

```text
assistant_app/domain/          Models, errors and dependency contracts
assistant_app/application/     Use cases, tool registry and approval policy
assistant_app/frontend/        Tk views and worker coordination
assistant_app/infrastructure/  Providers, integrations, transport and storage
assistant_app/mcp/             Opt-in read-only stdio interface
assistant_app/retrieval/       Explicit local lexical retrieval API
assistant_app/bootstrap.py     Concrete adapter wiring
tests/               Policy, integration, network, storage and UI tests
docs/                Setup, architecture and release guidance
scripts/             Local testing and release checks
.github/             CI, dependency updates and contribution templates
```

Read [CONTRIBUTING.md](https://github.com/Jeddou10/Personal_assistant/blob/main/CONTRIBUTING.md) before proposing a change. See the [architecture](https://github.com/Jeddou10/Personal_assistant/blob/main/docs/ARCHITECTURE.md), [changelog](https://github.com/Jeddou10/Personal_assistant/blob/main/CHANGELOG.md), and [release checklist](https://github.com/Jeddou10/Personal_assistant/blob/main/docs/RELEASING.md). CI validates code and packages only; it does not publish anything.

## License

[MIT](https://github.com/Jeddou10/Personal_assistant/blob/main/LICENSE). Third-party services and any separately installed model weights have their own terms and licenses.
