"""Mocked tests for instagram_live (no network). Run: python tests/test_instagram.py  (or pytest)."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
import instagram_live as ig
from instagram_live import poll

SID = "123456789%3Aabcdef%3A27"
PK, BID = "555", "999"
P_PROFILE, P_STORY, P_INFO = "/api/v1/users/web_profile_info/", f"/api/v1/feed/user/{PK}/story/", f"/api/v1/live/{BID}/info/"
PROFILE = (200, {"data": {"user": {"id": PK}}})
NOT_LIVE = (200, {"broadcast": None, "status": "ok"})
LIVE = (200, {"broadcast": {"id": BID, "broadcast_status": "active"}, "status": "ok"})
def info(v=..., st="active"):
    d = {"broadcast_status": st, "status": "ok"}
    if v is not ...: d["viewer_count"] = v
    return (200, d)

class Fake:
    """routes: path -> list of responses (tuple, or Exception). Last one repeats."""
    def __init__(self, routes): self.routes, self.calls, self.seen_headers = routes, [], []
    def handler(self, req):
        self.calls.append(req.url.path); self.seen_headers.append(dict(req.headers))
        seq = self.routes.get(req.url.path)
        if not seq: return httpx.Response(404, json={"message": "nope"})
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception): raise item
        code, body, *h = item
        return httpx.Response(code, json=body, headers=h[0] if h else None)

def run(fake, steps=1, ctx=None, cfg=None, clock=None):
    """Poll `steps` times; return list of results or exceptions."""
    async def go():
        out, c = [], ctx if ctx is not None else {}
        if clock: c["clock"] = clock
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            for _ in range(steps):
                try: out.append(await poll(cfg or {"session_id": SID, "username": "realmalikgpt", "interval": 30}, http, c))
                except Exception as e: out.append(e)
        return out, c
    return asyncio.run(go())

class T:  # controllable clock
    def __init__(s): s.t = 1000.0
    def __call__(s): return s.t

def test_1_offline():
    r, _ = run(Fake({P_PROFILE: [PROFILE], P_STORY: [NOT_LIVE]}))
    assert r == [{"live": False, "viewers": None}]

def test_2_3_live_detected_with_count():
    r, c = run(Fake({P_PROFILE: [PROFILE], P_STORY: [LIVE], P_INFO: [info(42.0)]}))
    assert r == [{"live": True, "viewers": 42}] and c["bid"] == BID and isinstance(r[0]["viewers"], int)

def test_4_count_changes():
    f = Fake({P_PROFILE: [PROFILE], P_STORY: [LIVE], P_INFO: [info(10), info(25), info(18)]})
    r, _ = run(f, 3)
    assert [x["viewers"] for x in r] == [10, 25, 18]
    assert f.calls.count(P_STORY) == 1 and f.calls.count(P_PROFILE) == 1   # discovery/user id cached

def test_5_missing_count_is_none_never_zero():
    for body in (info(), info(None), info("abc"), info(-3), info(True)):
        r, _ = run(Fake({P_PROFILE: [PROFILE], P_STORY: [LIVE], P_INFO: [body]}))
        assert r == [{"live": True, "viewers": None}], body

def test_6_expired_auth_and_cooldown():
    f = Fake({P_PROFILE: [(400, {"message": "login_required", "status": "fail"})]})
    t = T(); r, c = run(f, 3, clock=t)
    assert isinstance(r[0], ig.InstagramAuthError) and "fresh session_id" in str(r[0])
    assert "backing off" in str(r[1]) and len(f.calls) == 1            # no hammering after auth failure
    assert SID not in str(r[0]) and "abcdef" not in str(r[0])
    t.t += ig.AUTH_COOLDOWN + 1
    run(f, 1, ctx=c, clock=t); assert len(f.calls) == 2                 # retries after cooldown

def test_6b_new_session_resets_cooldown():
    f = Fake({P_PROFILE: [(401, {})]}); r, c = run(f)
    f.routes = {P_PROFILE: [PROFILE], P_STORY: [NOT_LIVE]}
    r2, _ = run(f, 1, ctx=c, cfg={"session_id": "987%3Anew%3A1", "username": "realmalikgpt"})
    assert r2 == [{"live": False, "viewers": None}]

def test_7_http_429_backoff_exponential():
    f = Fake({P_PROFILE: [PROFILE], P_STORY: [(429, {}, {"retry-after": "120"})]})
    t = T(); r, c = run(f, 2, clock=t)
    assert isinstance(r[0], ig.InstagramRateLimited) and "backing off" in str(r[1]) and len(f.calls) == 2
    assert c["until"] - 1000 == 120                                     # honours Retry-After (>30s base)
    t.t += 121; f.routes[P_STORY] = [(429, {})]; run(f, 1, ctx=c, clock=t)
    assert c["fails"] == 2 and c["until"] - t.t == 60                    # 30 * 2**1

def test_8_timeout():
    f = Fake({P_PROFILE: [httpx.ReadTimeout("slow")]})
    r, c = run(f, 2, clock=T())
    assert isinstance(r[0], ig.InstagramError) and "network error" in str(r[0]) and "backing off" in str(r[1])

def test_9_broadcast_ends():
    f = Fake({P_PROFILE: [PROFILE], P_STORY: [LIVE, NOT_LIVE], P_INFO: [info(7), info(7, "stopped")]})
    r, c = run(f, 2)
    assert r[0] == {"live": True, "viewers": 7} and r[1] == {"live": False, "viewers": None} and "bid" not in c

def test_9b_info_404_means_ended_then_rediscover():
    f = Fake({P_PROFILE: [PROFILE], P_STORY: [LIVE, NOT_LIVE], P_INFO: [info(7), (404, {})]})
    r, _ = run(f, 2)
    assert r[1] == {"live": False, "viewers": None}

def test_10_11_service_failure_then_reconnect():
    f = Fake({P_PROFILE: [PROFILE], P_STORY: [LIVE], P_INFO: [info(30), (503, {}), info(33)]})
    t = T(); r, c = run(f, 1, clock=t)
    r2, _ = run(f, 2, ctx=c, clock=t)
    assert isinstance(r2[0], ig.InstagramServiceError) and "backing off" in str(r2[1])
    t.t += 31; r3, _ = run(f, 1, ctx=c, clock=t)
    assert r3 == [{"live": True, "viewers": 33}] and c["fails"] == 0

def test_security_no_secrets_in_errors_and_cookie_only_sent_in_header():
    f = Fake({P_PROFILE: [(500, {})]}); r, _ = run(f)
    assert SID not in str(r[0]) and "sessionid" not in str(r[0])
    assert f.seen_headers[0]["cookie"].startswith("sessionid=") and "ds_user_id=123456789" in f.seen_headers[0]["cookie"]
    try: ig.normalize_session("abc; evil=1"); assert False
    except ig.InstagramAuthError: pass

def test_adapter_fallback_graph_mode_unchanged():
    import adapters
    async def go():
        h = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": "1"}]}))
        async with httpx.AsyncClient(transport=h) as http:
            return await adapters.instagram({"ig_user_id": "1", "access_token": "IGabc"}, http, {})
    assert asyncio.run(go()) == {"live": True, "viewers": None}

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)
