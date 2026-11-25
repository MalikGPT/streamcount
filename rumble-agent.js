// Polls Rumble's Live Stream API from YOUR network (residential IP) and pushes the result
// to the Render server. Node 18+ required, no dependencies.
// Usage: RUMBLE_API_KEY=... SERVER_URL=https://xxx.onrender.com PUSH_TOKEN=... node rumble-agent.js
const KEY = process.env.RUMBLE_API_KEY, SERVER = (process.env.SERVER_URL || "").replace(/\/$/, "");
const TOKEN = process.env.PUSH_TOKEN, EVERY = Math.max(5, +process.env.INTERVAL || 20) * 1000;
const API = process.env.RUMBLE_API_URL || "https://rumble.com/-livestream-api/get-data";
if (!KEY || !SERVER || !TOKEN) { console.error("Set RUMBLE_API_KEY, SERVER_URL, PUSH_TOKEN"); process.exit(1); }

const AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
  "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
];
const ua = () => process.env.USER_AGENT || AGENTS[Math.floor(Math.random() * AGENTS.length)];

async function check() {
  const r = await fetch(`${API}?key=${encodeURIComponent(KEY)}`, {
    headers: { "User-Agent": ua(), Accept: "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9" },
  });
  if (!r.ok) throw new Error(`Rumble HTTP ${r.status}`);
  const j = await r.json();
  const list = j.livestreams || ("is_live" in j ? [j] : []);
  const s = list.find((x) => x.is_live);
  return s ? { live: true, viewers: s.watching_now == null ? null : Number(s.watching_now) }
           : { live: false, viewers: null };
}

async function push(body) {
  const r = await fetch(`${SERVER}/api/push/rumble`, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Server HTTP ${r.status}`);
}

async function loop() {
  let body;
  try { body = await check(); console.log(new Date().toISOString(), JSON.stringify(body)); }
  catch (e) { body = { error: String(e.message).slice(0, 150) }; console.warn("check failed:", e.message); }
  try { await push(body); } catch (e) { console.warn("push failed:", e.message); }
  setTimeout(loop, EVERY);
}
loop();
