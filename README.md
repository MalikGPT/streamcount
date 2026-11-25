# MalikGPT Multi-Platform Live Viewer Counter

Cloud service (FastAPI, Render) that polls 7 platforms, auto-detects live/offline, and serves one transparent
overlay: `[live platform logos] [combined concurrent viewers]`. Overlay URL: `https://<service>.onrender.com/overlay`.

## Honest status (read first)
Only `core.py` (live/offline confirmation, stale handling, totals) is unit-tested: `python tests/test_core.py` (5 pass); Instagram adapter is mock-tested only: `python tests/test_instagram.py`.
**No platform adapter, the server, the overlay, Render deploy, OBS or Meld has been run.** Everything below is UNTESTED until you confirm it.
Research is from the author's prior knowledge, not freshly verified: check each doc link before relying on it.

| Platform | Integration | Real concurrent viewers | Auth | Status |
|---|---|---|---|---|
| YouTube | `/live` page discovery + Data API `videos.list` `liveStreamingDetails.concurrentViewers` (1 quota unit/poll) | Yes (official) | API key | untested |
| Twitch | Helix `GET /streams` `viewer_count` | Yes (official) | Client ID+secret (app token) | untested |
| Rumble | Live Stream API URL key, `watching_now` | Yes (per Rumble docs; verify JSON shape) | API key from creator dashboard | untested |
| TikTok | `TikTokLive` lib, `RoomUserSeqEvent` | Yes (unofficial, WebSocket) | username only; cloud IPs may be blocked | untested, disabled by default |
| Kick | Official public API `GET /public/v1/channels?slug=` stream object | Expected (verify field) | OAuth client credentials | untested |
| Nimo | Unofficial: parses public room page | Unverified guesses; errors out if fields absent | none | experimental |
| Instagram | Optional UNOFFICIAL mode (set `session_id`): private-API `live/{id}/info/` `viewer_count`, see `docs/INSTAGRAM.md`. Default: Graph API `/{ig-user-id}/live_media`, live detection only | Unofficial mode: intended yes, **unverified live** (mock-tested). Graph mode: no | Own `sessionid` cookie (unofficial) or Meta access token | untested live; `heartbeat_and_get_viewer_count` (private API) deliberately not used (ToS/ban risk) |

Docs: developers.google.com/youtube/v3/docs/videos, dev.twitch.tv/docs/api/reference/#get-streams, docs.kick.com,
github.com/isaackogan/TikTokLive, developers.facebook.com/docs/instagram-platform (live_media), rumble.com creator dashboard (Live Stream API).

## Logic
Live needs 2 consecutive "live" polls; offline needs 3 consecutive "offline" polls. API errors never change state and never count as 0.
Viewer data older than 3x interval is excluded from the total; a platform with no successful poll for max(10 min, 10x interval) is dropped.
Live with unknown count (Instagram) shows a logo but adds nothing. Logos sorted in fixed order. No live platforms: overlay shows OFFLINE.

## Deploy
1. Push to GitHub. Render > New > Blueprint > pick repo (`render.yaml`). Keep a paid plan: free web services sleep when idle.
2. Read `ADMIN_PASSWORD` from the service env. Open `/admin` (user `admin`), fill credentials, enable platforms.
3. Dashboard-saved settings live in `data/config.json` and are lost on redeploy unless you attach a Render Disk and set `CONFIG_PATH`; or set secrets as env vars (see `.env.example`).
4. OBS/Meld: Browser Source, URL `/overlay` (optional `?scale=1.5&align=left`), e.g. 1200x120. Transparent by default.

## Logos
`static/logos/{twitch,kick,tiktok}.svg` use official-style Simple Icons paths from memory; youtube, instagram, rumble, nimo are **my own approximations**.
Replace all seven with official brand-kit SVGs (same filenames) for exact branding.

## Troubleshooting
- Platform shows error in `/admin`: message is the last exception. 401: bad key. Nimo "field not found": page format changed.
- Overlay stuck OFFLINE: needs 2 live polls (2x interval). Check `/api/state` and `/health`.
- TikTok never connects: Render IPs are often blocked; try later or use a proxy.
