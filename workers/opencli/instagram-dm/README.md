# instagram-dm — OpenCLI plugin for Instagram Direct (read-only)

Local OpenCLI plugin that **lists and reads** the owner's own Instagram Direct
conversations from his already-logged-in Chrome, through the OpenCLI Browser
Bridge. **Read-only by design.** There is no send command. It never types,
clicks, POSTs, logs in, reads cookie files or touches settings.

Status (2026-10-05, OpenCLI 1.8.6): `dm-threads` and `dm-read` were checked live
with redacted output only (counts, types, lengths). The owner asked for read-only
first. Sending is out of scope.

## How it works

Strategy `PAGE_FETCH` (opencli `Strategy.COOKIE`), contract `internal-unstable`.
Each command opens a fresh **background** tab on `https://www.instagram.com/robots.txt`,
a tiny same-origin page. From that tab it makes same-session `GET` calls to the web
app's own endpoints, using the same `X-IG-App-ID` header as the built-in
`opencli instagram *` adapters:

- `GET /api/v1/direct_v2/inbox/?folder=&limit=N&thread_message_limit=1[&cursor=…]`
- `GET /api/v1/direct_v2/threads/<thread_id>/?limit=N[&cursor=…]`

Why this path and not the Direct UI or the DOM:
- Opening a conversation in `instagram.com/direct/…` sends a **"seen" receipt** to
  the other person. These GETs do not, because "seen" is a separate POST that this
  plugin never calls.
- It does not load the full Instagram app, so there is less traffic and no
  presence ping from the Direct UI.

Pagination follows `oldest_cursor` for at most 5 pages of 20, with a 1.5 s pause
between pages. Timestamps are microseconds and are converted to ISO UTC. The tab
closes when the command ends (`siteSession: ephemeral`, `--keep-tab false` default).

## Install / register (no changes to the global npm package)

```bash
opencli plugin install "file:///C:/Users/David/projects/market-opportunity-radar/workers/opencli/instagram-dm"
opencli plugin list            # instagram-dm @0.1.0 ← local:...\workers\opencli\instagram-dm
```

This creates a junction `~/.opencli/plugins/instagram-dm` that points to this
folder, so edits take effect immediately. It also creates the gitignored
`node_modules/` link and `package-lock.json`. To remove the plugin:
`opencli plugin uninstall instagram-dm`.

## Usage

```bash
opencli instagram-dm dm-threads --limit 20 -f json
opencli instagram-dm dm-read <thread_id> --limit 30 -f json
```

| command | rows |
|---|---|
| `dm-threads [--limit 1-50, def 20]` | newest first: `{thread_id, name, last_text (≤200), last_time (ISO) \| null, unread}` |
| `dm-read <thread_id> [--limit 1-100, def 30]` | oldest→newest: `{thread_id, message_id, author, is_me, text, time (ISO) \| null, media \| null}` |

Field notes:
- `name` is `thread_title`, falling back to the participants' usernames.
- `last_text` is the newest item's text, or `[<item_type>]` for non-text items such as `[clip]`.
- `unread` is `read_state === 1 || marked_as_unread`.
- `author` is the sender's username, or `me` for the owner's own messages.
- `media` is the Instagram `item_type` for non-text items: `media`, `clip`, `media_share`,
  `story_share`, `voice_media`, `placeholder`, `action_log`, `profile`, `link` and so on.
  It is `null` for plain text. No media URLs are returned.
- `text` is the item text. For links, story replies, action logs and placeholders it
  is taken from the item's own sub-field. Otherwise it is `''`.

Errors are written to stderr as YAML `{ok: false, error: {code, message, help, exitCode}}`:

| code | meaning |
|---|---|
| `dm_locked` | Instagram answered with a checkpoint, challenge, 2FA or PIN/verification requirement. Message: *"Desbloquea los mensajes de Instagram en Chrome"*. The owner must resolve it in Chrome. **This plugin never stores, reads or types any code.** |
| `AUTH_REQUIRED` | Not logged in: 401/403, `login_required`, or a redirect to `/accounts/login`. |
| `rate_limited` | HTTP 429 or "wait a few minutes". Wait; do not retry in a loop (exit 75). |
| `ARGUMENT` | `thread_id` is not numeric, or `--limit` is out of range. |
| `EMPTY_RESULT` | Empty inbox, thread not found (404), or no messages returned. |
| `COMMAND_EXEC` | Any other HTTP failure. The endpoint may have changed. |

## Offline check

```bash
node workers/opencli/instagram-dm/selftest.js   # fake fixtures only, no browser
```

The self-test also asserts that `dm.js` has no write command, no POST and no
typing or clicking.

## Known limitations

- **Primary inbox only.** `folder=` is the default inbox. Message requests
  (pending) and the general/business folders are not listed.
- **End-to-end encrypted chats.** Threads that Meta has moved to E2EE are not
  served by these endpoints. They may come back empty or as `placeholder` items.
  Not observed live (all sampled threads had `e2ee_cutover_status = 0`).
- **`unread` unverified for `true`.** No unread thread existed during the test
  (`unseen_count = 0`).
- **`placeholder` / `story_share` text** fallbacks are covered offline only.
- **Internal endpoint.** `direct_v2` is undocumented and may change or be
  retired without notice. There is no DOM fallback.
- **Rate limits.** Earlier on 2026-10-05, `opencli instagram whoami` got HTTP 429.
  Keep calls sparse; each command makes 1–5 GETs.
