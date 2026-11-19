# Privacy and data handling

Personal Assistant is a desktop application, not a hosted service. It has no project-operated telemetry, analytics endpoint, or chat backend.

## Data flows

| Mode or operation | What leaves the computer |
| --- | --- |
| Anthropic chat | Your requests, explicit saved preferences, relevant conversation context and retrieved tool results go to Anthropic |
| Local Ollama chat | The same inference context goes to the configured loopback service; the app never switches to Anthropic automatically |
| Gmail/Calendar requests | OAuth credentials and requested API operations go to Google |
| Notion requests | Integration credentials and requested read/approved append operations go to Notion |
| Explicit MCP CLI | Selected Gmail, Calendar and Notion read results go to the local MCP host over stdio; the host may log or forward them to its own services/models |
| Retrieval developer API | The built-in reader and lexical index send nothing over the network; explicitly selected text and citations stay in process memory |

Provider privacy policies, retention rules, pricing and account settings apply independently. No claim of zero cloud retention is made. Local inference still requires internet when you request Google or Notion data.

## What is stored locally

The app stores data under `%LOCALAPPDATA%\PersonalAssistant`, outside its source directory:

- `state.json`: provider/model/resource settings and Windows-DPAPI-encrypted credentials. Resource identifiers such as the selected account/page/calendar are configuration and are not encrypted.
- `preferences.bin`: Windows-DPAPI-encrypted preferences that you explicitly save.

Conversations and retrieved content are held in memory for the current session and are not intentionally written to disk. Starting a new session or changing settings clears that context and pending approvals. The operating system may use swap, crash dumps or backups outside the application's control.

Unsent drafts remain in memory; changing settings preserves the unsent composer draft. Conversation search is local to the current session. **Copy last answer** explicitly transfers that answer to the system clipboard, where other applications or enabled clipboard-history/sync features may retain it. The app never copies answers automatically or clears the system clipboard on your behalf.

Preferences are included in the selected AI's context. Only save information you want reused. Text such as "remember this" does not persist automatically; use the explicit memory controls.

## Optional MCP and retrieval

Launching `python -m assistant_app.mcp` opts into trusting the local host with private integration content. There is no per-read desktop approval, no inference inside this MCP server and no network listener. Read-only describes the exposed operations, not a promise that returned content stays local: the host controls its retention and onward transmission. Credential refresh may update local token storage during reads.

Retrieval does not run at desktop startup and is not connected to chat or MCP tools. It only ingests individually selected files beneath explicitly approved roots through its developer API. Roots, document text, lexical index entries and citations are not persisted by the built-in implementation; citations contain relative filenames, root IDs, line ranges and content hashes. Selection is not automatic indexing, and no personal files are bundled or indexed by installation. No embeddings are downloaded and no Google Drive connection exists.

`clear()` discards indexed documents but retains approved roots; `set_roots([])` revokes all roots and discards the index. Returned text held by a caller remains that caller's responsibility. Future custom loaders, indexes or model integrations can change these data flows and require separate review. Source snippets and filenames are untrusted data, never instructions or permission to act.

## Removal and revocation

Use **Preferences** to remove saved rules. **Disconnect all** removes saved API keys, Google tokens/client credentials, and selected account resources from the app. It leaves preferences intact and does not revoke provider-side grants.

To remove all local app data, quit every app instance and delete the `%LOCALAPPDATA%\PersonalAssistant` directory. Provider-side data and authorizations must be managed through the respective Google, Notion, Anthropic or local-server controls. Uninstalling the Python package does not automatically remove local app data.

Never attach these local data files to an issue or support request, even though some fields are encrypted.
