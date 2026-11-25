"""One async poll function per platform. Each returns {'live': bool, 'viewers': int|None}
or RAISES on any error (so errors are never mistaken for offline/zero).
STATUS: none of these have been run against live services by the author."""
import re, time, json

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def need(cfg, *keys):
    miss = [k for k in keys if not cfg.get(k)]
    if miss:
        raise RuntimeError("missing config: " + ", ".join(miss))


async def _yt_discover(cfg, http, ctx):
    ch = cfg["channel"].strip()
    path = f"channel/{ch}" if ch.startswith("UC") else ch if ch.startswith("@") else f"@{ch}"
    r = await http.get(f"https://www.youtube.com/{path}/live", headers=UA,
                       cookies={"CONSENT": "YES+1"}, follow_redirects=True)
    r.raise_for_status()
    m = re.search(r'rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', r.text)
    if m and '"isLive":true' in r.text:
        ctx["vid"] = m.group(1)
        return r.text if "watching" in r.text else None
    return False


async def youtube(cfg, http, ctx):
    """No API key => UNOFFICIAL: scrapes the public watch page ('N watching now').
    With api_key set => official Data API concurrentViewers. Scraping can break any time;
    if the count text isn't found it returns viewers=None (not 0)."""
    need(cfg, "channel")
    vid = ctx.get("vid")
    page = None
    if not vid:
        page = await _yt_discover(cfg, http, ctx)
        if page is False:
            return {"live": False, "viewers": None}
        vid = ctx["vid"]
    if cfg.get("api_key"):
        r = await http.get("https://www.googleapis.com/youtube/v3/videos",
                           params={"part": "liveStreamingDetails,snippet", "id": vid, "key": cfg["api_key"]})
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items or items[0]["snippet"].get("liveBroadcastContent") != "live":
            ctx.pop("vid", None)
            return {"live": False, "viewers": None}
        cv = items[0].get("liveStreamingDetails", {}).get("concurrentViewers")
        return {"live": True, "viewers": int(cv) if cv is not None else None}
    if page is None:
        r = await http.get(f"https://www.youtube.com/watch?v={vid}", headers=UA,
                           cookies={"CONSENT": "YES+1"}, follow_redirects=True)
        r.raise_for_status()
        page = r.text
    if '"isLive":true' not in page:
        ctx.pop("vid", None)
        return {"live": False, "viewers": None}
    m = (re.search(r'"text":"([\d,.]+)"\},\{"text":" watching', page)
         or re.search(r'"simpleText":"([\d,.]+) watching', page))
    return {"live": True, "viewers": int(re.sub(r"\D", "", m.group(1))) if m else None}


async def twitch(cfg, http, ctx):
    need(cfg, "login", "client_id", "client_secret")
    if ctx.get("exp", 0) < time.time() + 60:
        r = await http.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "grant_type": "client_credentials"})
        r.raise_for_status()
        j = r.json(); ctx["tok"], ctx["exp"] = j["access_token"], time.time() + j["expires_in"]
    r = await http.get("https://api.twitch.tv/helix/streams", params={"user_login": cfg["login"]},
                       headers={"Client-Id": cfg["client_id"], "Authorization": f"Bearer {ctx['tok']}"})
    if r.status_code == 401: ctx["exp"] = 0
    r.raise_for_status()
    d = [s for s in r.json().get("data", []) if s.get("type") == "live"]
    return {"live": True, "viewers": int(d[0]["viewer_count"])} if d else {"live": False, "viewers": None}


async def kick(cfg, http, ctx):
    need(cfg, "slug", "client_id", "client_secret")
    if ctx.get("exp", 0) < time.time() + 60:
        r = await http.post("https://id.kick.com/oauth/token", data={
            "grant_type": "client_credentials", "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"]})
        r.raise_for_status()
        j = r.json(); ctx["tok"], ctx["exp"] = j["access_token"], time.time() + j.get("expires_in", 3600)
    r = await http.get("https://api.kick.com/public/v1/channels", params={"slug": cfg["slug"]},
                       headers={"Authorization": f"Bearer {ctx['tok']}"})
    if r.status_code == 401: ctx["exp"] = 0
    r.raise_for_status()
    data = r.json().get("data") or []
    st = (data[0].get("stream") or {}) if data else {}
    if st.get("is_live"):
        v = st.get("viewer_count")
        return {"live": True, "viewers": int(v) if v is not None else None}
    return {"live": False, "viewers": None}


async def rumble(cfg, http, ctx):
    need(cfg, "api_key")  # Rumble 'Live Stream API' URL key from your creator dashboard
    r = await http.get(cfg.get("api_url") or "https://rumble.com/-livestream-api/get-data",
                       params={"key": cfg["api_key"]},
                       headers={**UA, "Accept": "application/json,*/*", "Accept-Language": "en-US,en;q=0.9"})
    r.raise_for_status()
    j = r.json()
    streams = j.get("livestreams") or ([j] if "is_live" in j else [])
    for s in streams:
        if s.get("is_live"):
            v = s.get("watching_now")
            return {"live": True, "viewers": int(v) if v is not None else None}
    return {"live": False, "viewers": None}


async def nimo(cfg, http, ctx):
    """EXPERIMENTAL: no official viewer API. Parses the public room page; field names are
    unverified guesses. Raises (=> 'error', never offline/zero) if it cannot find them."""
    need(cfg, "room_id")
    r = await http.get(f"https://www.nimo.tv/live/{cfg['room_id']}", headers=UA, follow_redirects=True)
    r.raise_for_status()
    t = r.text
    st = re.search(r'"(?:liveStreamStatus|isLive|liveStatus)"\s*:\s*"?(\w+)"?', t)
    if not st:
        raise RuntimeError("nimo: live-status field not found (page format changed?)")
    is_live = st.group(1).lower() in ("1", "true")
    if not is_live:
        return {"live": False, "viewers": None}
    v = re.search(r'"(?:totalViewerCount|viewerCount|liveViewerCount|onlineCount)"\s*:\s*(\d+)', t)
    return {"live": True, "viewers": int(v.group(1)) if v else None}


async def instagram(cfg, http, ctx):
    """Detects a live broadcast only; the API has no concurrent-viewer field, so viewers=None.
    Tokens starting 'IG' (Instagram Login) use graph.instagram.com; others use graph.facebook.com."""
    need(cfg, "ig_user_id", "access_token")
    host = "graph.instagram.com" if cfg["access_token"].startswith("IG") else "graph.facebook.com"
    r = await http.get(f"https://{host}/v21.0/{cfg['ig_user_id']}/live_media",
                       params={"access_token": cfg["access_token"]})
    r.raise_for_status()
    return {"live": bool(r.json().get("data")), "viewers": None}


async def tiktok(cfg, http, ctx):
    need(cfg, "username")
    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import ConnectEvent, DisconnectEvent, RoomUserSeqEvent
    c = ctx.get("client")
    if c is None or ctx.get("user") != cfg["username"]:
        c = TikTokLiveClient(unique_id="@" + cfg["username"].lstrip("@"))
        ctx.update(client=c, user=cfg["username"], viewers=None, up=False)

        @c.on(ConnectEvent)
        async def _c(e): ctx["up"] = True

        @c.on(DisconnectEvent)
        async def _d(e): ctx.update(up=False, viewers=None)

        @c.on(RoomUserSeqEvent)
        async def _v(e):
            v = getattr(e, "m_total", None) or getattr(e, "total", None)
            if v is not None: ctx["viewers"] = int(v)
    if not ctx["up"]:
        if await c.is_live():
            ctx["task"] = await c.start()  # background task; reconnect happens on next poll
            return {"live": True, "viewers": ctx.get("viewers")}
        return {"live": False, "viewers": None}
    return {"live": True, "viewers": ctx.get("viewers")}


ADAPTERS = dict(youtube=youtube, twitch=twitch, rumble=rumble, tiktok=tiktok,
                kick=kick, nimo=nimo, instagram=instagram)
FIELDS = dict(
    youtube=[("channel", 0), ("api_key", 1)],  # api_key optional
    twitch=[("login", 0), ("client_id", 0), ("client_secret", 1)],
    rumble=[("api_key", 1)],
    tiktok=[("username", 0)],
    kick=[("slug", 0), ("client_id", 0), ("client_secret", 1)],
    nimo=[("room_id", 0)],
    instagram=[("ig_user_id", 0), ("access_token", 1)],
)  # (name, is_secret)
