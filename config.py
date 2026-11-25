import json, os, threading
from adapters import FIELDS
from core import ORDER

PATH = os.environ.get("CONFIG_PATH", "data/config.json")
_lock = threading.Lock()
DEFAULTS = {n: {"enabled": False, "interval": 30} for n in ORDER}


def _load():
    try:
        with open(PATH) as f: return json.load(f)
    except Exception:
        return {}


def get(name):
    """Saved config, with env vars (e.g. TWITCH_CLIENT_ID, TWITCH_ENABLED) as fallback."""
    saved = _load().get(name, {})
    cfg = dict(DEFAULTS[name]); cfg.update({k: v for k, v in saved.items() if v not in ("", None)})
    for k, _ in FIELDS[name]:
        cfg.setdefault(k, os.environ.get(f"{name}_{k}".upper(), ""))
    if "enabled" not in saved and os.environ.get(f"{name}_ENABLED"):
        cfg["enabled"] = os.environ[f"{name}_ENABLED"].lower() in ("1", "true", "yes")
    if "interval" not in saved and os.environ.get(f"{name}_INTERVAL"):
        cfg["interval"] = int(os.environ[f"{name}_INTERVAL"])
    return cfg


def masked():
    out = {}
    for n in ORDER:
        c, o = get(n), {"enabled": get(n)["enabled"], "interval": get(n)["interval"]}
        for k, secret in FIELDS[n]:
            o[k] = ("set" if c.get(k) else "") if secret else c.get(k, "")
        out[n] = o
    return out


def save(name, data):
    if name not in ORDER: raise KeyError(name)
    with _lock:
        allc = _load(); cur = allc.get(name, {})
        cur["enabled"] = bool(data.get("enabled"))
        cur["interval"] = max(5, int(data.get("interval") or 30))
        for k, secret in FIELDS[name]:
            v = (data.get(k) or "").strip()
            if v and not (secret and v == "set"): cur[k] = v  # blank keeps existing
        allc[name] = cur
        os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
        with open(PATH, "w") as f: json.dump(allc, f)
