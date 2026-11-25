"""Live/offline state machine + viewer total. Pure stdlib, unit-tested."""
import time

ORDER = ["youtube", "twitch", "rumble", "tiktok", "kick", "nimo", "instagram"]


class Monitor:
    def __init__(self, live_confirm=2, off_confirm=3, stale_factor=3, hard_stale=600):
        self.live_confirm, self.off_confirm = live_confirm, off_confirm
        self.stale_factor, self.hard_stale = stale_factor, hard_stale
        self.p = {n: dict(enabled=False, live=False, viewers=None, last_ok=0.0,
                          live_streak=0, off_streak=0, errors=0, last_error=None,
                          interval=30) for n in ORDER}

    def configure(self, name, enabled, interval):
        p = self.p[name]
        p["interval"] = max(5, int(interval))
        if p["enabled"] and not enabled:
            p.update(live=False, viewers=None, live_streak=0, off_streak=0)
        p["enabled"] = bool(enabled)

    def report(self, name, result, now=None, error=None):
        """result: {'live': bool, 'viewers': int|None}, or None on API/network error.
        Errors NEVER change live state and are never treated as zero viewers."""
        now = now or time.time()
        p = self.p[name]
        if result is None:
            p["errors"] += 1
            p["last_error"] = str(error)[:200] if error else "error"
            return
        p.update(errors=0, last_error=None, last_ok=now)
        if result["live"]:
            p["live_streak"] += 1
            p["off_streak"] = 0
            p["viewers"] = result.get("viewers")
            if not p["live"] and p["live_streak"] >= self.live_confirm:
                p["live"] = True
        else:
            p["off_streak"] += 1
            p["live_streak"] = 0
            if p["live"] and p["off_streak"] >= self.off_confirm:
                p.update(live=False, viewers=None)
            elif not p["live"]:
                p["viewers"] = None

    def _fresh(self, p, now):
        return now - p["last_ok"] <= self.stale_factor * p["interval"]

    def _expire(self, now):
        for p in self.p.values():
            if p["live"] and now - p["last_ok"] > max(self.hard_stale, 10 * p["interval"]):
                p.update(live=False, viewers=None, live_streak=0)

    def live_platforms(self, now=None):
        now = now or time.time()
        self._expire(now)
        return [n for n in ORDER if self.p[n]["enabled"] and self.p[n]["live"]]

    def total(self, now=None):
        now = now or time.time()
        return sum(self.p[n]["viewers"] for n in self.live_platforms(now)
                   if self.p[n]["viewers"] is not None and self._fresh(self.p[n], now))

    def public(self, now=None):
        now = now or time.time()
        live = self.live_platforms(now)
        return {"platforms": live, "total": self.total(now), "live": bool(live)}

    def admin(self, now=None):
        now = now or time.time()
        self.live_platforms(now)
        return {n: dict(p, fresh=self._fresh(p, now),
                        age=round(now - p["last_ok"]) if p["last_ok"] else None)
                for n, p in self.p.items()}
