// Polls Rumble's Live Stream API from YOUR network (residential IP) and pushes the result
// to the Render server. Node 18+ required, no dependencies.
// Usage: RUMBLE_API_KEY=... SERVER_URL=https://xxx.onrender.com PUSH_TOKEN=... node rumble-agent.js
const KEY = process.env.RUMBLE_API_KEY, SERVER = (process.env.SERVER_URL || "").replace(/\/$/, "");
const TOKEN = process.env.PUSH_TOKEN, EVERY = Math.max(5, +process.env.INTERVAL || 20) * 1000;
const API = process.env.RUMBLE_API_URL || "https://rumble.com/-livestream-api/get-data";
if (!KEY || !SERVER || !TOKEN) { console.error("Set RUMBLE_API_KEY, SERVER_URL, PUSH_TOKEN"); process.exit(1); }

async function check() {
  const r = await fetch(`${API}?key=${encodeURIComponent(KEY)}`, { headers: { Accept: "application/json" } });
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
