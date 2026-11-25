import asyncio, json, logging, os, re, secrets, time
from contextlib import asynccontextmanager
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import config
from adapters import ADAPTERS
from core import ORDER, Monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mvc")
mon = Monitor()
subs: set = set()
security = HTTPBasic()


def admin(c: HTTPBasicCredentials = Depends(security)):
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if not pw or not (secrets.compare_digest(c.password.encode(), pw.encode())
                      and secrets.compare_digest(c.username.encode(), b"admin")):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


def clean(e):
    msg = str(e)
    if isinstance(e, httpx.HTTPStatusError):
        msg += " | " + e.response.text[:200]
    msg = re.sub(r"//[^/@\s]+@", "//***@", msg)
    msg = re.sub(r"(?i)(sessionid|ds_user_id)[=:]\s*[^;\s'\"]+", r"\1=***", msg)
    return re.sub(r"(access_token|api_key|session_id|sessionid|key|client_secret|token)=[^&\s'\"]+", r"\1=***", msg)


async def worker(name, http):
    ctx = {}
    while True:
        cfg = config.get(name)
        mon.configure(name, cfg["enabled"], cfg["interval"])
        pushed_recently = time.time() - mon.p[name].get("pushed_at", 0) < 120
        if cfg["enabled"] and os.environ.get(f"{name.upper()}_MODE") != "push" and not pushed_recently:
            try:
                mon.report(name, await asyncio.wait_for(ADAPTERS[name](cfg, http, ctx), 25))
            except Exception as e:
                log.warning("%s poll error: %s", name, clean(e))
                mon.report(name, None, error=clean(e))
        await asyncio.sleep(cfg["interval"] if cfg["enabled"] else 5)


async def broadcaster():
    last = None
    while True:
        snap = json.dumps(mon.public())
        if snap != last:
            last = snap
            for q in list(subs): q.put_nowait(snap)
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    http = httpx.AsyncClient(timeout=15)
    tasks = [asyncio.create_task(worker(n, http)) for n in ORDER] + [asyncio.create_task(broadcaster())]
    yield
    for t in tasks: t.cancel()
    await http.aclose()


app = FastAPI(lifespan=lifespan)
S = os.path.join(os.path.dirname(__file__), "static")
app.mount("/logos", StaticFiles(directory=os.path.join(S, "logos")), name="logos")


@app.post("/api/push/{name}")
async def push(name: str, request: Request):
    tok = os.environ.get("PUSH_TOKEN", "")
    got = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not tok or not secrets.compare_digest(got.encode(), tok.encode()):
        raise HTTPException(401)
    if name not in mon.p: raise HTTPException(404)
    b = await request.json()
    mon.p[name]["pushed_at"] = time.time()  # server stops its own polling while pushes arrive
    if b.get("error"):
        mon.report(name, None, error=str(b["error"])[:200])
    else:
        v = b.get("viewers")
        mon.report(name, {"live": bool(b.get("live")), "viewers": int(v) if v is not None else None})
    return {"ok": True}


@app.get("/health")
def health(): return {"ok": True}


@app.get("/overlay")
def overlay(): return FileResponse(os.path.join(S, "overlay.html"))


@app.get("/api/state")
def state(): return mon.public()


@app.get("/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue(); subs.add(q)

    async def gen():
        try:
            yield "retry: 3000\n\n" f"data: {json.dumps(mon.public())}\n\n"
            while not await request.is_disconnected():
                try: yield f"data: {await asyncio.wait_for(q.get(), 15)}\n\n"
                except asyncio.TimeoutError: yield ": ping\n\n"
        finally: subs.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/admin", dependencies=[Depends(admin)])
def admin_page(): return FileResponse(os.path.join(S, "admin.html"))


@app.get("/api/admin/config", dependencies=[Depends(admin)])
def get_cfg(): return {"config": config.masked(), "status": mon.admin()}


@app.post("/api/admin/config/{name}", dependencies=[Depends(admin)])
async def set_cfg(name: str, request: Request):
    try: config.save(name, await request.json())
    except KeyError: raise HTTPException(404)
    return {"ok": True}
