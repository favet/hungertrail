// Hunger Trail leaderboard API over D1.
//
// The game server (Render) is the only writer. It authenticates with a key derived from its own
// secret. The repo is public, so there's no shared secret here: the first key to POST /register
// is trusted from then on (only its SHA-256 is stored). Reads are public.

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function sha256(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function storedHash(env) {
  const row = await env.DB.prepare("SELECT value FROM config WHERE key = 'app_key_hash'").first();
  return row ? row.value : null;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const NICK = /^[A-Za-z0-9_.\- ]{2,16}$/;
const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    try {
      if (req.method === "POST" && url.pathname === "/register") {
        const { key } = await req.json();
        if (typeof key !== "string" || key.length < 32) return json({ error: "bad key" }, 400);
        const h = await sha256(key);
        const have = await storedHash(env);
        if (have === h) return json({ ok: true });
        if (have) return json({ error: "already registered" }, 409);
        await env.DB.prepare("INSERT INTO config (key, value) VALUES ('app_key_hash', ?)").bind(h).run();
        return json({ ok: true }, 201);
      }

      if (req.method === "POST" && url.pathname === "/submit") {
        const auth = (req.headers.get("authorization") || "").replace(/^Bearer /, "");
        const have = await storedHash(env);
        if (!have || (await sha256(auth)) !== have) return json({ error: "unauthorized" }, 401);
        const e = await req.json();
        const ok = DATE.test(e.date) && NICK.test(e.nickname) && typeof e.run_id === "string" && e.run_id.length <= 40 &&
          int(e.score, 0, 1e7) && int(e.placement, 1, 24) && int(e.kills, 0, 24) && int(e.day, 1, 20) && int(e.district, 1, 12) &&
          typeof e.cause === "string" && e.cause.length <= 120 && typeof e.emoji === "string" && e.emoji.length <= 80;
        if (!ok) return json({ error: "bad entry" }, 400);
        try {
          await env.DB.prepare(
            "INSERT INTO scores (date, nickname, nick_key, run_id, score, placement, kills, day, district, cause, emoji) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
          ).bind(e.date, e.nickname, e.nickname.toLowerCase(), e.run_id, e.score, e.placement, e.kills, e.day, e.district, e.cause, e.emoji).run();
        } catch (err) {
          if (String(err).includes("UNIQUE")) return json({ error: "duplicate" }, 409);
          throw err;
        }
        return json({ ok: true });
      }

      if (req.method === "GET" && url.pathname === "/board") {
        const date = url.searchParams.get("date") || "";
        if (!DATE.test(date)) return json({ error: "bad date" }, 400);
        const limit = Math.max(1, Math.min(200, parseInt(url.searchParams.get("limit") || "50", 10) || 50));
        const { results } = await env.DB.prepare(
          "SELECT nickname, score, placement, kills, day, district, emoji FROM scores WHERE date = ? ORDER BY score DESC, created_at ASC LIMIT ?"
        ).bind(date, limit).all();
        return json({ rows: results });
      }

      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "server error" }, 500);
    }
  },
};
