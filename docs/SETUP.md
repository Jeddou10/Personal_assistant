# Setup and usage

Install or run the app using the [quick start](../README.md#quick-start), then configure the accounts you want it to access.

## Set up your connections

Open **Menu > Settings**. Keys belong in the masked settings fields, never in chat or repository files. Blank key fields preserve an existing key. Saving changed settings discards the current conversation and all pending approvals so information cannot carry across providers/accounts. Unsent composer drafts remain available for you to review before sending with the new settings.

## Everyday controls

**Important mail today** immediately requests a summary using Gmail's `is:important` label and explicit local-day boundaries, including read and unread messages. Mail summaries use your chosen AI provider and retain the existing ten-match search limit. [Google documents epoch-second query boundaries](https://developers.google.com/workspace/gmail/api/guides/filtering) for timezone-accurate searches.

**Calendar: today & tomorrow** reads your selected calendar and opens a separate two-column calendar, including all-day and overnight events. Click an event for read-only details. It does not call an AI model or add events to the chat. The view shows computer-local time; all-day end dates are exclusive. Up to 50 source events are shown, with a notice when results are incomplete or malformed events were skipped. The window closes when you reset the session or change settings. Date ranges follow [Google's event-list bounds](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).

- **Ctrl+Enter** sends; **Enter** adds a line. The composer grows up to six lines and then scrolls.
- **Menu > Quick questions** prepares an editable request, including a daily briefing. You decide whether to send it.
- **Ctrl+F** opens literal conversation search, showing up to the first 500 matches. Search never calls an AI provider.
- **Menu > Copy last answer** copies only the most recent assistant answer to the system clipboard.
- **Menu > Edit last request** brings the previous request back into the composer for editing. It never submits or retries anything automatically.
- Close the chat window to hide it; the soccer ball stays available. Use **Menu > Quit** to exit.

While a request is running, progress and elapsed time are shown. You may type a new draft, but account/session changes and another send wait for the operation to finish. Proposed changes still require exact preview and approval; an uncertain write outcome must be inspected at its destination before another attempt.

### Anthropic Claude

1. Select `anthropic`.
2. Enter your Anthropic API key and an API model ID available to your account. The default is `claude-sonnet-5-5`; it is editable as models change.
3. Save settings, then send a short message.

Cloud mode sends your messages, explicitly saved preferences, and relevant tool results to Anthropic. This app does not control Anthropic's retention policies. API requests use your account and may incur usage charges. Model and API behavior follow the [Anthropic Messages reference](https://platform.claude.com/docs/en/api/messages) and [model list](https://platform.claude.com/docs/en/models/overview).

### Google: one Gmail account and one calendar

1. In your Google Cloud project, enable **Gmail API** and **Google Calendar API**.
2. Configure the OAuth consent screen for your own account. If the app is in Testing, add yourself as a test user.
3. Create an OAuth client of type **Desktop app**, download its JSON outside this repository, and select it through **Connect Google…**. The app opens Google's sign-in page. Select your intended account and consent to the requested scopes.
4. Return to Settings and use **Choose calendar…** to choose exactly one calendar.

The requested scopes are `gmail.readonly`, `calendar.events`, and `calendar.calendarlist.readonly`. Google grants calendar-event access at account level; **this application's code** restricts event access to your selected calendar. Calendar-list access is used only to populate the picker. Gmail has no send/modify scope and no sending code.

Google Desktop OAuth uses PKCE, a state-checked loopback callback, and refresh tokens. Testing-mode or revoked credentials may require reconnection. Details: [Google desktop OAuth documentation](https://developers.google.com/identity/protocols/oauth2/native-app).

### Notion: one page

1. Create an internal Notion integration with read-content and insert-content capabilities.
2. Share your chosen page with that integration using Notion's page connection controls. Share only the intended page.
3. Enter the integration token and page UUID in Settings. The UUID is the 32 hexadecimal characters at the end of the page URL, with or without dashes; enter only the UUID, not the URL.

This version reads top-level ordinary text blocks and appends approved plain-text paragraphs to the selected page. It does not edit existing blocks, follow child/linked pages, query databases, read files, or change other pages. The API may give an integration access to more resources than the app uses; the app binds requests to the selected page. See [Notion append-block documentation](https://developers.notion.com/reference/patch-block-children).

### Optional local AI later

Nothing is downloaded automatically. When you separately install Ollama and a local model that supports tools:

1. Choose `ollama` in Settings.
2. Enter the installed model name and `http://127.0.0.1:11434` (or an IPv6 loopback address).
3. Save. The app checks model metadata/tool support before chat.

Cloud model aliases and non-loopback inference URLs are rejected. No automatic fallback to Anthropic occurs. The Ollama process itself is trusted software: the app cannot attest what a modified local server does. Google and Notion operations still contact those services. See the [Ollama chat API](https://docs.ollama.com/api/chat).

## Using the assistant

Examples:

- “Summarize my unread emails from today.”
- “What appointments do I have tomorrow?”
- “Read my selected Notion page and summarize the open tasks.”
- “Prepare a 30-minute appointment tomorrow at 14:00, UTC+01:00, called Project review.”
- “Prepare a summary of these emails to append to my Notion page.”

Writes first appear under **Proposed changes**. Select one and click **Review selected**. The preview shows destination, exact content, before/after state and applicable notification behavior. **Approve this change** executes precisely that proposal once. **Request changes** rejects that version and lets you request a new one. **Reject** does nothing remotely. A chat message saying “approve” cannot bypass these controls. Proposals expire after ten minutes.

Calendar writes support ordinary, personal timed events with explicit UTC offsets. Creating an event uses a ten-minute popup reminder. Updates/deletions require a matching ETag, self-organizer, no guests, no recurrence/conference data, and explicit non-email reminder settings. All-day events, guest events and inherited/default-reminder events can be read but cannot be changed in this version. Requests force `sendUpdates=none`; Google calendar-level notification settings remain outside app control. See [Google event updates](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch).

If a write's outcome is **unknown**, the app never retries it. Inspect the destination through read-only chat or the service itself before starting another session and considering a fresh proposal. Starting a new session does not undo a completed write. An already-dispatched request cannot be recalled.


## Troubleshooting

- **No AI response:** verify the selected provider, model ID and API key. An API subscription/key is separate from a consumer chat subscription.
- **Google sign-in cancelled or timed out:** retry Connect Google and finish consent in the browser within three minutes. Confirm both Google APIs are enabled and your account is a test user if the consent app is in Testing.
- **Google access revoked/expired:** reconnect the account and choose the calendar again.
- **Notion page unavailable:** share that exact page with the integration and check its UUID.
- **Calendar change rejected:** this version intentionally restricts event types and reminders; see the write limits above.
- **Local model unavailable:** install/configure Ollama separately and choose an already-installed local GGUF model with tool support. The app never downloads a model.
- **Unknown write outcome:** inspect the destination before making a new proposal; do not blindly repeat the action.

Disconnecting removes stored credentials from this app; revoke provider-side grants separately. See [privacy](../PRIVACY.md).
