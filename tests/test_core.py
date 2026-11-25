import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import Monitor

def mk():
    m = Monitor()
    for n in m.p: m.configure(n, True, 30)
    return m

def live(v): return {"live": True, "viewers": v}
OFF = {"live": False, "viewers": None}

def test_confirmation_and_total():
    m = mk(); t = 1000
    m.report("youtube", live(100), t); assert m.public(t)["platforms"] == []
    m.report("youtube", live(120), t + 1)
    assert m.public(t + 1) == {"platforms": ["youtube"], "total": 120, "live": True}

def test_errors_not_offline_not_zero():
    m = mk(); t = 1000
    for i in range(2): m.report("twitch", live(50), t + i)
    for i in range(10): m.report("twitch", None, t + 2 + i, "boom")
    assert m.public(t + 20)["platforms"] == ["twitch"] and m.total(t + 20) == 50

def test_offline_needs_3():
    m = mk(); t = 1000
    for i in range(2): m.report("kick", live(10), t + i)
    m.report("kick", OFF, t + 2); m.report("kick", OFF, t + 3)
    assert "kick" in m.public(t + 3)["platforms"]
    m.report("kick", OFF, t + 4)
    assert m.public(t + 4) == {"platforms": [], "total": 0, "live": False}

def test_stale_excluded_then_expired():
    m = mk(); t = 1000
    for i in range(2): m.report("rumble", live(70), t + i)
    assert m.total(t + 1 + 91) == 0
    assert "rumble" in m.live_platforms(t + 1 + 91)
    assert m.live_platforms(t + 1 + 700) == []

def test_multi_order_and_none_viewers():
    m = mk(); t = 1000
    for n, v in [("kick", 5), ("youtube", 10), ("instagram", None)]:
        for i in range(2): m.report(n, live(v), t + i)
    r = m.public(t + 1)
    assert r["platforms"] == ["youtube", "kick", "instagram"] and r["total"] == 15

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)
