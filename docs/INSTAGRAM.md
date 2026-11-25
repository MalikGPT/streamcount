# Instagram Live viewer count (UNOFFICIAL)

**Status: implemented and unit-tested with mocked responses only. NOT verified against the live Instagram service.**
Instagram has no official concurrent-viewer field (the Graph API `live_media` edge only says *whether* you are live).
This adapter (`instagram_live.py`) uses private-API endpoints documented by community libraries. Their docs are years
old; Instagram can change or remove them at any time, and using them may violate Instagram's Terms. Use your own account only.

## Research summary
| Item | Source | Documented by Meta? | Tested here? | Real concurrent viewers? |
|---|---|---|---|---|
| `GET feed/user/{id}/story/` -> top-level `broadcast` (live discovery) | ping/instagram_private_api `user_broadcast()` | No | No (mocks only) | n/a (detection) |
| `GET live/{broadcast_id}/info/` -> `viewer_count`, `broadcast_status` (`active/interrupted/stopped/hard_stop`) | ping/instagram_private_api docs, dilame/instagram-private-api | No | No (mocks only) | Believed yes; field present in the library's sample response; unconfirmed on current Instagram |
| `GET live/{id}/heartbeat_and_get_viewer_count/` | same libraries | No | not used | Broadcaster-side call; deliberately avoided |
| `users/web_profile_info/?username=` (username -> id) | widely used web endpoint | No | No | n/a |
| Graph API `/{ig-user-id}/live_media` | Meta docs | Yes | No | **No** (live detection only) |

Failure conditions: endpoint removed/renamed, `viewer_count` absent (-> `viewers: None`), session expired/checkpoint,
HTTP 429, account flagged. Nothing is ever estimated; `total_unique_viewer_count`, likes, followers etc. are ignored.

## Setup
1. Log in to instagram.com **in your own browser** as @realmalikgpt.
2. DevTools -> Application/Storage -> Cookies -> `https://www.instagram.com` -> copy the **value** of `sessionid`.
3. Provide it as a secret: env var `INSTAGRAM_SESSION_ID` (Render: dashboard env var, `sync: false`), or the `/admin` page
   (field `session_id`, stored in `data/config.json`, which is git-ignored). Also set `username` (default `realmalikgpt`).
4. Enable Instagram in `/admin`, interval **>= 30 s** (the adapter never backs off below 30 s base).
5. Treat the value like a password. If it leaks, log out of all sessions in Instagram to revoke it.

No passwords are used, and there is no CAPTCHA/challenge handling: on `login_required` / `checkpoint_required`
the adapter stops calling for 30 minutes (or until you paste a new session), and `/admin` shows the error.

## Behaviour
- Offline: 1-2 requests/poll (user id cached after first success). Live: 1 request/poll (`live/{id}/info/`).
- Errors raise (never treated as offline or 0 viewers); `core.Monitor` keeps state through errors.
- 429/5xx/timeouts: exponential backoff (30 s x 2^n, max 15 min; honours `Retry-After`). Overlapping polls are skipped.
- Missing/invalid `viewer_count` -> `{"live": true, "viewers": null}`: logo shows, nothing added to the total.
- `interrupted` -> live with unknown count; `stopped`/`hard_stop`/404 on info -> offline (cached id dropped).
- Without `session_id` the old Graph API mode (live detection only) is used unchanged.
- Optional env overrides: `INSTAGRAM_API_BASE`, `INSTAGRAM_USER_AGENT` (config keys `api_base`, `user_agent`).

## Tests
`pip install -r requirements.txt && python tests/test_instagram.py && python tests/test_core.py` (also works with `pytest`).
Mocks cover: offline, live detected, count retrieved, count changing, missing count, expired auth, 429, timeout,
broadcast end, 503, reconnect, no secret leakage.

## How to verify for real (required before trusting it)
While actually live: set the session, enable Instagram, open `/admin` and `/api/state`; compare the number with the one in
the Instagram Live app. If `/admin` shows an HTTP error or `viewers` stays null, the endpoint changed: do not rely on it.
