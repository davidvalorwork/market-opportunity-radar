# messenger — READ-ONLY OpenCLI plugin for Facebook Messenger

Local OpenCLI plugin that **lists and reads** the owner's own Facebook Messenger
conversations from his already-logged-in Chrome (`facebook.com/messages`). It works
through the OpenCLI Browser Bridge.

**Read-only by design (owner's decision, 2026-10-05).** There is no send command. The
plugin never focuses, types into or clicks the message composer. It does not log in,
does not read cookie files and does not change settings.

Status (2026-10-05, OpenCLI 1.8.6): `dm-threads` and `dm-read` were checked live on
the owner's account. The check printed only counts and types, never content. Both
plain (`/messages/t/…`) and end-to-end encrypted (`/messages/e2ee/t/…`) chats were
read. No PIN prompt came up during that check, so the `dm_locked` path is untested live.

## How it works

- Strategy `UI_SELECTOR`, contract `visible-ui`. Encrypted chats exist in clear only
  in the DOM that the page has already decrypted, so the plugin reads the DOM.
- Inbox: rows of the single `[role=grid]` and their `a[href*="/messages/"]` links. Each
  row's `aria-label` ("hace 3 días") becomes an approximate ISO `last_time`. The list
  is scrolled (up to 8 times) until `--limit` rows are collected.
- Conversation: `[role=log] [aria-roledescription][data-message-id]`. The element's
  `aria-label` has the form `A las <hora>, <autor>: <texto>`. Author, text and time come
  from there. When the label has no text (seen on some Marketplace rows), the visible
  DOM text is used. `is_me` = author `Tú`/`You`. Older messages load by scrolling the
  log up (up to 8 times). The list is virtualised, so rows are merged by
  `data-message-id`.
- Each command runs in a fresh **background** tab (`siteSession: ephemeral`). The tab
  closes when the command ends. `--keep-tab false` is the default.

## Install / register (no changes to the global npm package)

```bash
opencli plugin install "file:///C:/Users/David/projects/market-opportunity-radar/workers/opencli/messenger"
opencli plugin list            # messenger @0.1.0 ← local:...\workers\opencli\messenger
```

This creates a junction `~/.opencli/plugins/messenger` that points to this folder,
plus the gitignored `node_modules/` link and `package-lock.json`. To remove the plugin:
`opencli plugin uninstall messenger`.

## Usage

```bash
opencli messenger dm-threads --limit 20 -f json
opencli messenger dm-read <thread_id> --limit 30 -f json      # e.g. 1234567890 or e2ee:1234567890
```

| command | rows |
|---|---|
| `dm-threads [--limit 1-50, def 20]` | `{thread_id, name, last_text (≤200), last_time, unread}` |
| `dm-read <thread_id> [--limit 1-100, def 30]` | oldest→newest `{thread_id, message_id, author, is_me, text, time (ISO) \| null, media \| null}` |

- `thread_id` is `"<n>"` for `/messages/t/<n>/` and `"e2ee:<n>"` for `/messages/e2ee/t/<n>/`.
- `last_time` is an approximate ISO date (the inbox shows relative times only, so the
  precision is that unit). If the label cannot be parsed, the raw visible label is kept.
- `time` is the label time converted to ISO in the machine's local time zone, or `null`.
- `media` is `image`, `video`, `audio` or `null`. Attachments that carry no text have
  `text: ""`.

Errors are written to stderr as YAML `{ok: false, error: {code, message, help, exitCode}}`:

| code | meaning |
|---|---|
| `dm_locked` | Messenger asks for the PIN or recovery code that restores encrypted chats. Message: *"Desbloquea los mensajes de Messenger en Chrome"*. The owner must unlock it in Chrome. **The plugin never types or stores any PIN.** |
| `AUTH_REQUIRED` | Chrome is not logged in to facebook.com, or it is stuck on a checkpoint. |
| `ARGUMENT` | Bad `thread_id` or `--limit` out of range. |
| `EMPTY_RESULT` | No conversations or messages rendered, or the thread was not found. |
| `COMMAND_EXEC` | The UI did not load within 25 s (layout change or rate limit). |

## Offline check

```bash
node workers/opencli/messenger/selftest.js   # fake fixtures only, no browser
```

## Known limitations / side effects

- **`/messages/` auto-opens the newest conversation** next to the inbox. No URL shows
  the inbox alone. `dm-read` necessarily opens the conversation it reads. Both run in a
  background window (`document.visibilityState = hidden`, no focus). Messenger
  normally sends "seen" receipts only for a visible, focused window, but **this was not
  verified**. Receipts travel over Messenger's own websocket/worker sync, which the
  network log does not show. Assume a read may mark the chat as seen.
- The page itself sends its normal telemetry while loaded (`/ajax/bnzai`, `/ajax/qm`,
  `/ajax/webstorage`, `bootloader-endpoint`). The plugin sends no requests of its own.
- **Locale.** Label parsing targets Spanish (`A las …`, `Tú`, `hace …`, Spanish month
  and weekday names), with basic English. In another UI language, `time` may be `null`
  and `is_me` may fall back to bubble alignment, which is only used when there is no
  label.
- **`unread` is a heuristic.** It uses bold row text or an unread/"no leído" label. It
  has not been checked against a real unread thread.
- **`dm_locked` detection** uses a PIN/password/numeric input in a dialog or in the main
  area, or PIN/restore wording in a dialog. It has not been tested live.
- **Fragile selectors.** Facebook changes markup often. To re-check, run
  `opencli browser <s> open https://www.facebook.com/messages/ --window background`
  and inspect `[role=grid]` and `[role=log]`. Print only counts or redacted shapes.
- **Rate limits.** Every command loads the full Facebook app. Keep calls sparse.
