# x-dm — OpenCLI plugin for X direct messages

Local OpenCLI plugin that lists, reads and sends **one** X (Twitter) DM at a time
from the owner's already-logged-in Chrome. It works through the OpenCLI Browser
Bridge. It does not log in, does not read cookie files and does not touch account
settings.

Status (2026-10-05, OpenCLI 1.8.6): `dm-threads`, `dm-read` and
`dm-send --dry-run` were checked live with redacted output. **A real send
(`dm-send --yes`) has not been tested.** The owner must run that first test by hand.

## How it works

`x.com/messages` now redirects to the XChat UI at `/i/chat`. DMs are
end-to-end encrypted there, so the GraphQL responses (`GetInitialXChatPageQuery`,
`GetConversationPageQuery`) carry opaque encrypted message blobs. The plugin
therefore reads the DOM that the page has already decrypted. That DOM sits inside
the shadow root of `[data-testid="xchatEmbedRoute"]`.

- Strategy `UI_SELECTOR`, contract `visible-ui`. Anchors: `dm-conversation-item-*`,
  `[data-dm-message-row]`, the hidden `*-sender`, `*-time` and `*-kind` labels,
  `message-text-*` and `dm-composer-textarea`.
- Message time comes from the epoch-seconds value inside the row's React props.
  It is only accepted when it matches the visible `H:MM` label. If no value
  matches, `time` is `null`.
- Inbox `last_time` is approximate. The inbox only shows relative labels such as
  `3 h` or `6 s` (s = semanas, weeks), so the precision is that unit.
- Each command runs in a fresh **background** tab (`siteSession: ephemeral`). The
  tab closes when the command ends. `--keep-tab false` is the default.

## Install / register (no changes to the global npm package)

```bash
opencli plugin install "file:///C:/Users/David/projects/market-opportunity-radar/workers/opencli/x-dm"
opencli plugin list            # x-dm @0.1.0 ← local:...\workers\opencli\x-dm
```

This creates a junction `~/.opencli/plugins/x-dm` that points to this folder, so
edits take effect immediately. Install also creates the gitignored `node_modules/`
link to the host opencli and an empty `package-lock.json`. To remove the plugin:
`opencli plugin uninstall x-dm`.

## Usage

```bash
opencli x-dm dm-threads --limit 20 -f json
opencli x-dm dm-read <thread_id> --limit 30 -f json
opencli x-dm dm-send <thread_id> "texto exacto" --dry-run -f json   # checks only, types nothing
opencli x-dm dm-send <thread_id> "texto exacto" --yes -f json       # sends exactly one message
```

Recommended flags for bots: `--window background --keep-tab false` (both are the defaults).

| command | rows |
|---|---|
| `dm-threads [--limit 1-50, def 20]` | `{thread_id, name, last_text (≤200), last_time (ISO, approx) \| null, unread}` |
| `dm-read <thread_id> [--limit 1-100, def 30]` | oldest→newest `{thread_id, message_id, author, is_me, text, time (ISO) \| null, media \| null}` |
| `dm-send <thread_id> <text> --yes` | `{sent: true, thread_id, verified}` |
| `dm-send <thread_id> <text> --dry-run` | `{would_send: true, thread_id, name}` |

`dm-send` safety rules:
- Without `--yes` (and without `--dry-run`) it refuses with exit 2 (`ARGUMENT`).
- It handles one thread and one click. It never loops over threads and never retries.
- It refuses if the composer already holds a draft.
- After typing, it checks that the composer holds exactly the given text. If not,
  it clears the composer and sends nothing.
- If the send button cannot be found or is disabled, it clears the composer and
  sends nothing.
- `verified` is true only when a new outgoing row with the same text (whitespace
  normalised) appears within about 10 s.

Errors are written to stderr as YAML `{ok: false, error: {code, message, help, exitCode}}`:

| code | meaning |
|---|---|
| `dm_locked` | XChat is asking for the encrypted-DM passcode/PIN. Message: *"Desbloquea los mensajes de X en Chrome"*. The owner must unlock it in Chrome. **This plugin never stores, reads or types any passcode.** |
| `AUTH_REQUIRED` | Chrome is not logged in to x.com. |
| `ARGUMENT` | Bad `thread_id`, `--limit` out of range, empty text, or missing `--yes`. |
| `EMPTY_RESULT` | No conversations or messages rendered, or the thread was not found. |
| `COMMAND_EXEC` | The UI did not load (layout change or rate limit), or a composer/send problem. |

## Offline check

```bash
node workers/opencli/x-dm/selftest.js   # fake fixtures only, no browser
```

## Known limitations

- **XChat only.** Only conversations that the XChat UI renders are visible. Older
  pre-encryption history may not appear, and message requests are not listed.
- **Pagination.** Older messages are loaded by scrolling up, at most 8 times. The
  list is virtualised, so rows are merged by id. Inbox scrolling is limited to 8
  scrolls.
- **`unread` is a heuristic.** It uses bold preview text or an "unread"/"no leído"
  label, and it has not been checked against a real unread thread.
- **`is_me`.** It uses the row alignment class (`justify-end`) or `data-side`.
  Only incoming rows were seen live.
- **Untested send path.** `page.insertText` goes into the shadow-DOM textarea, with
  a React value-setter fallback. The send button is found by `testid*=send`, then
  `type=submit`, then `aria-label` Enviar/Send.
- **Fragile selectors.** X changes testids and minified prop names. Re-check with
  `opencli browser <s> open https://x.com/i/chat --window background` and inspect
  `document.querySelector('[data-testid=xchatEmbedRoute]').shadowRoot`.
- **Rate limits.** Every command loads the full XChat app. Keep calls sparse.
