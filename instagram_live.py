"""UNOFFICIAL Instagram Live concurrent-viewer adapter (private/mobile-style API, own account only).

STATUS: NOT VERIFIED AGAINST THE LIVE SERVICE. Endpoint paths come from community libraries
(ping/instagram_private_api, dilame/instagram-private-api); their docs are years old and Instagram
may have changed or removed them. Test with your own account while live (see docs/INSTAGRAM.md).

Flow (read-only, 1 request per poll while live, 1-2 while offline):
  1. username -> numeric user id   GET users/web_profile_info/?username=   (cached)
  2. live discovery                GET feed/user/{id}/story/   -> top-level "broadcast" or null
  3. count                         GET live/{broadcast_id}/info/ -> "viewer_count", "broadcast_status"
Deliberately NOT used: live/{id}/heartbeat_and_get_viewer_count/ (a broadcaster-side call that can
register activity), logins with passwords, or anything that handles challenges/CAPTCHAs.

Contract (same as other adapters): returns {'live': bool, 'viewers': int|None} or RAISES on error,
so errors are never mistaken for offline/zero. viewers is None unless Instagram returned a valid
non-negative `viewer_count`; nothing is ever estimated.
"""
import asyncio, math, re, time
from urllib.parse import unquote

import httpx

DEFAULT_BASE = "https://i.instagram.com/api/v1"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
IG_APP_ID = "936619743392459"          # public web app id used by instagram.com
REQ_TIMEOUT = 10
MAX_BACKOFF = 900                      # seconds
AUTH_COOLDOWN = 1800                   # stop calling after a rejected session (no hammering/lockout)
ENDED = {"stopped", "hard_stop", "ended"}
AUTH_MESSAGES = {"login_required", "checkpoint_required", "challenge_required", "consent_required"}


class InstagramError(RuntimeError): pass
class InstagramAuthError(InstagramError): pass
class InstagramRateLimited(InstagramError):
    def __init__(self, msg, retry_after=None): super().__init__(msg); self.retry_after = retry_after
class InstagramServiceError(InstagramError): pass
class InstagramNotFound(InstagramError): pass


def normalize_session(raw):
    """Accept 'sessionid=XXX;' or the bare value. Rejects anything that could inject headers."""
    s = (raw or "").strip().strip(";")
    if s.lower().startswith("sessionid="): s = s[len("sessionid="):]
    if not s or re.search(r"[\s;,\x00-\x1f]", s):
        raise InstagramAuthError("instagram: session_id is empty or malformed (paste only the sessionid value)")
    return s


def viewers_or_none(v):
    """Strict: a real, finite, non-negative number only. Anything else -> None (never a guess)."""
    if v is None or isinstance(v, bool): return None
    try: n = float(v)
    except (TypeError, ValueError): return None
    return int(n) if math.isfinite(n) and n >= 0 else None


def _headers(session, cfg):
    sid = normalize_session(session)
    uid = unquote(sid).split(":")[0]
    cookie = f"sessionid={sid}" + (f"; ds_user_id={uid}" if uid.isdigit() else "")
    return {"User-Agent": cfg.get("user_agent") or DEFAULT_UA, "X-IG-App-ID": IG_APP_ID,
            "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9", "Cookie": cookie}


async def _get(http, cfg, path, params=None):
    base = (cfg.get("api_base") or DEFAULT_BASE).rstrip("/")
    r = await http.get(f"{base}/{path}", params=params, headers=_headers(cfg["session_id"], cfg),
                       timeout=REQ_TIMEOUT, follow_redirects=False)
    s = r.status_code
    if s == 429:
        ra = r.headers.get("retry-after", "")
        raise InstagramRateLimited("instagram: HTTP 429 rate limited", int(ra) if ra.isdigit() else None)
    if 300 <= s < 400: raise InstagramAuthError(f"instagram: redirect HTTP {s} (session expired?)")
    try: j = r.json()
    except ValueError: j = None
    msg = j.get("message") if isinstance(j, dict) else None
    if s == 401 or (isinstance(msg, str) and msg in AUTH_MESSAGES):
        raise InstagramAuthError(f"instagram: session rejected ({msg or 'HTTP ' + str(s)}); supply a fresh session_id")
    if s == 403: raise InstagramAuthError("instagram: HTTP 403 (session rejected or request blocked)")
    if s == 404: raise InstagramNotFound("instagram: HTTP 404")
    if s >= 500: raise InstagramServiceError(f"instagram: HTTP {s} service error")
    if s >= 400: raise InstagramError(f"instagram: HTTP {s}")
    if not isinstance(j, dict): raise InstagramServiceError("instagram: non-JSON/unexpected response")
    return j


async def _resolve_user(http, cfg, ctx, username):
    if ctx.get("pk") and ctx.get("user") == username: return ctx["pk"]
    j = await _get(http, cfg, "users/web_profile_info/", {"username": username})
    pk = ((j.get("data") or {}).get("user") or {}).get("id")
    if not pk: raise InstagramError("instagram: could not resolve user id (profile format changed?)")
    ctx.update(pk=str(pk), user=username)
    return ctx["pk"]


async def _info(http, cfg, ctx, bid):
    """-> {'live': bool, 'viewers': int|None}. Ended/unknown broadcast clears the cached id."""
    try:
        j = await _get(http, cfg, f"live/{bid}/info/")
    except InstagramNotFound:
        ctx.pop("bid", None)
        return None
    st = str(j.get("broadcast_status") or "").lower()
    if st in ENDED:
        ctx.pop("bid", None)
        return {"live": False, "viewers": None}
    if st == "interrupted":     # connection hiccup on the broadcaster side: still live, count unreliable
        return {"live": True, "viewers": None}
    return {"live": True, "viewers": viewers_or_none(j.get("viewer_count"))}


async def _poll(http, cfg, ctx):
    username = (cfg.get("username") or "realmalikgpt").lstrip("@").strip()
    if ctx.get("bid"):
        res = await _info(http, cfg, ctx, ctx["bid"])
        if res is not None: return res
    pk = await _resolve_user(http, cfg, ctx, username)
    j = await _get(http, cfg, f"feed/user/{pk}/story/")
    b = j.get("broadcast")
    if not b: return {"live": False, "viewers": None}
    if not isinstance(b, dict): raise InstagramError("instagram: unexpected 'broadcast' field type")
    if str(b.get("broadcast_status") or "").lower() in ENDED: return {"live": False, "viewers": None}
    bid = b.get("id") or b.get("broadcast_id")
    if not bid: raise InstagramError("instagram: live broadcast found but no id field")
    ctx["bid"] = str(bid)
    res = await _info(http, cfg, ctx, ctx["bid"])
    return res if res is not None else {"live": True, "viewers": None}


async def poll(cfg, http, ctx):
    """Adapter entry point. Handles locking, exponential backoff, 429s and expired sessions."""
    if not cfg.get("session_id"): raise RuntimeError("missing config: session_id")
    clock = ctx.get("clock") or time.time
    if ctx.get("sess") != cfg["session_id"]:        # new session supplied -> forget old cooldown/state
        keep = ctx.get("clock")
        ctx.clear(); ctx["sess"] = cfg["session_id"]
        if keep: ctx["clock"] = keep
    lock = ctx.setdefault("lock", asyncio.Lock())
    if lock.locked(): raise InstagramError("instagram: previous poll still running (skipped)")
    async with lock:
        now = clock()
        if ctx.get("until", 0) > now:
            raise InstagramError(f"instagram: backing off {int(ctx['until'] - now)}s ({ctx.get('why', 'error')})")
        base = max(30, int(cfg.get("interval") or 30))
        try:
            res = await asyncio.wait_for(_poll(http, cfg, ctx), 20)
        except InstagramAuthError:
            ctx.update(until=now + AUTH_COOLDOWN, why="auth"); ctx.pop("bid", None); raise
        except (InstagramRateLimited, InstagramServiceError, httpx.TransportError, asyncio.TimeoutError) as e:
            ctx["fails"] = ctx.get("fails", 0) + 1
            wait = min(MAX_BACKOFF, base * 2 ** (ctx["fails"] - 1))
            if isinstance(e, InstagramRateLimited) and e.retry_after: wait = min(MAX_BACKOFF, max(wait, e.retry_after))
            ctx.update(until=now + wait, why=type(e).__name__)
            if isinstance(e, (httpx.TransportError, asyncio.TimeoutError)):
                raise InstagramError(f"instagram: network error ({type(e).__name__})") from None
            raise
        ctx.update(fails=0, until=0)
        return res
