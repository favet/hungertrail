"use strict";

const CFG = JSON.parse(document.getElementById("config").textContent);
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const store = {
  get: (k) => { try { return JSON.parse(localStorage.getItem("ht." + k)); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem("ht." + k, JSON.stringify(v)); } catch {} },
  del: (k) => { try { localStorage.removeItem("ht." + k); } catch {} },
};

let view = null;        // last server view of the current run
let shown = 0;          // log entries already rendered
let busy = false;
let mode = "daily";
let daily = CFG.daily;
let dailyStarted = Date.now();

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) e.append(kid);
  return e;
}

function show(id) {
  $$(".screen").forEach((s) => (s.hidden = s.id !== id));
  window.scrollTo(0, 0);
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 2600);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong. Try again.");
  return data;
}

// ------------------------------------------------------------------ sound
const Sound = (() => {
  let ctx = null;
  const on = () => store.get("sound") !== false;
  function boom() {
    if (!on()) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      const len = ctx.sampleRate * 1.2;
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = buf.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass"; lp.frequency.value = 180;
      const g = ctx.createGain(); g.gain.value = 1.6;
      src.connect(lp).connect(g).connect(ctx.destination);
      src.start();
    } catch {}
  }
  return { boom, on, toggle: () => store.set("sound", !on()) };
})();

// ------------------------------------------------------------------ title
function dailyKey() { return "daily." + daily.date; }

function refreshTitle() {
  const d = new Date(daily.date + "T12:00:00");
  $("#title-date").textContent = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const saved = store.get(dailyKey());
  const btn = $("#btn-daily");
  if (saved && saved.over) btn.textContent = "See today's result";
  else if (saved) btn.textContent = "Continue today's run";
  else btn.textContent = "Enter today's arena";
}

// ------------------------------------------------------------------ reaping
let pick = { district: null, trait: null };

function buildReaping() {
  const last = store.get("setup") || {};
  const dg = $("#districts");
  dg.replaceChildren();
  for (const d of CFG.districts) {
    const b = el("button", { class: "district", "data-id": d.id, onclick: () => choosePick("district", d.id) },
      el("span", { class: "num", text: d.id }),
      el("span", { class: "name", text: d.name }),
      el("span", { class: "mult", text: "×" + d.mult }));
    dg.append(b);
  }
  const tl = $("#traits");
  tl.replaceChildren();
  for (const t of CFG.traits) {
    tl.append(el("button", { class: "trait", "data-id": t.id, onclick: () => choosePick("trait", t.id) },
      el("b", { text: t.name }), el("span", { class: "dim", text: " " + t.desc })));
  }
  $("#name-partner").value = last.partner || "";
  $("#name-partner").placeholder = CFG.names[Math.floor(Math.random() * CFG.names.length)];
  $("#name-ally1").value = (last.allies || [])[0] || "";
  $("#name-ally2").value = (last.allies || [])[1] || "";
  pick = { district: last.district || null, trait: last.trait || null };
  choosePick("district", pick.district);
  choosePick("trait", pick.trait);
}

function choosePick(kind, id) {
  pick[kind] = id;
  $$(kind === "district" ? ".district" : ".trait").forEach((b) => b.classList.toggle("on", String(b.dataset.id) === String(id)));
  $("#btn-start").disabled = !(pick.district && pick.trait);
}

function openReaping(m) {
  mode = m;
  $("#reaping-title").textContent = m === "daily" ? `The Reaping · #${daily.number}` : "The Reaping · Practice";
  buildReaping();
  show("reaping");
}

async function startRun() {
  if (busy) return;
  busy = true;
  const setup = {
    district: pick.district, trait: pick.trait,
    partner: $("#name-partner").value.trim() || $("#name-partner").placeholder,
    allies: [$("#name-ally1").value.trim(), $("#name-ally2").value.trim()].filter(Boolean),
  };
  store.set("setup", setup);
  try {
    const v = await api("/api/run", { mode, ...setup });
    save(v);
    enterGame(v);
  } catch (e) { toast(e.message); }
  busy = false;
}

// ------------------------------------------------------------------ game
function save(v) {
  const rec = { token: v.token, over: v.over };
  if (v.mode === "daily") store.set("daily." + v.date, rec);
  else store.set("practice", rec);
}

function enterGame(v) {
  view = v;
  shown = 0;
  $("#log").replaceChildren();
  show("game");
  render(true);
}

function statusBar(s) {
  const bar = (n, cls) => el("span", { class: "meter " + cls }, el("i", { style: `width:${Math.max(0, Math.min(100, n))}%` }));
  const items = s.items.length ? s.items.join(", ") : "nothing";
  const allies = s.allies.length ? " · with " + s.allies.join(", ") : "";
  return [
    el("div", { class: "row1" },
      el("span", { text: `Day ${s.day} · ${s.weather}` }),
      el("span", { class: "remaining", text: `${s.remaining} left · ${s.kills} ${s.kills === 1 ? "kill" : "kills"}` }),
      el("button", { class: "mute", title: "Sound", text: Sound.on() ? "🔊" : "🔇", onclick: (e) => { Sound.toggle(); e.target.textContent = Sound.on() ? "🔊" : "🔇"; } })),
    el("div", { class: "row2" },
      el("span", { class: "stat health h-" + s.health_word.replace(" ", "").toLowerCase() }, "Health ", el("b", { text: s.health_word })),
      el("span", { class: "stat" + (s.food === 0 ? " warn" : "") }, "Food ", el("b", { text: s.food })),
      el("span", { class: "stat" + (s.water === 0 ? " warn" : "") }, "Water ", el("b", { text: s.water }))),
    el("div", { class: "row3" },
      el("span", { class: "gauge" }, "Favor", bar(s.favor, "favor")),
      el("span", { class: "gauge" }, "Heat", bar(s.heat, "heat"))),
    el("div", { class: "row4 dim", text: `Carrying ${items}${allies}` }),
  ];
}

function render(first = false) {
  const v = view;
  $("#status").replaceChildren(...statusBar(v.status));
  const log = $("#log");
  const fresh = v.log.slice(shown);
  let delay = 0;
  let lastDay = shown ? v.log[shown - 1].day : 0;
  for (const entry of fresh) {
    if (entry.day !== lastDay && entry.kind !== "day" && shown + fresh.indexOf(entry) > 0) lastDay = entry.day;
    const node = el("p", { class: "entry " + entry.kind, text: entry.kind === "choice" ? "> " + entry.text : entry.text });
    if (!first) {
      node.style.animationDelay = delay + "ms";
      delay += entry.kind === "choice" ? 60 : 260;
      if (entry.kind === "cannon" || entry.kind === "death") setTimeout(() => { Sound.boom(); document.body.classList.add("shake"); setTimeout(() => document.body.classList.remove("shake"), 500); }, delay);
    } else node.classList.add("instant");
    log.append(node);
  }
  shown = v.log.length;
  const scroll = () => log.scrollTo({ top: log.scrollHeight, behavior: first ? "auto" : "smooth" });
  if (first) scroll(); else { scroll(); setTimeout(scroll, delay + 350); }

  const box = $("#options");
  box.replaceChildren();
  if (v.over) {
    box.append(el("button", { class: "big primary appear", style: `animation-delay:${delay + 300}ms`, text: v.result.won ? "Claim your crown" : "See how you did", onclick: () => showResults(v) }));
    return;
  }
  v.options.forEach((o, i) => {
    const b = el("button", { class: "option appear", style: `animation-delay:${delay + i * 70}ms`, onclick: () => pickOption(o) },
      el("span", { class: "key", text: i + 1 }),
      el("span", { class: "label" }, el("span", { text: o.label }), o.hint ? el("small", { text: o.hint }) : null),
      o.chance != null ? el("span", { class: "chance " + (o.chance >= 70 ? "good" : o.chance >= 45 ? "even" : "long"), text: o.chance + "%" }) : null);
    box.append(b);
  });
}

async function pickOption(o) {
  if (busy || !view || view.over) return;
  if (o.minigame === "hunt") return Hunt.play((hits) => send(o.index, hits));
  send(o.index, null);
}

async function send(index, param) {
  busy = true;
  $$("#options button").forEach((b) => (b.disabled = true));
  try {
    const v = await api("/api/run/choose", { token: view.token, index, param });
    view = v;
    save(v);
    render();
  } catch (e) {
    toast(e.message);
    $$("#options button").forEach((b) => (b.disabled = false));
  }
  busy = false;
}

document.addEventListener("keydown", (e) => {
  if (!$("#hunt").hidden) {
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); Hunt.shoot(); }
    return;
  }
  if ($("#game").hidden || !view || e.target.tagName === "INPUT") return;
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= view.options.length) pickOption(view.options[n - 1]);
});

// ------------------------------------------------------------------ hunting minigame
const Hunt = (() => {
  let shots = 0, hits = 0, pos = 0, dir = 1, speed = 0.9, zone = [40, 56], raf = 0, last = 0, done = null, locked = false;
  const track = () => $("#hunt .track");
  function frame(t) {
    const dt = last ? Math.min(50, t - last) : 16;
    last = t;
    pos += dir * speed * dt / 10;
    if (pos >= 100) { pos = 100; dir = -1; }
    if (pos <= 0) { pos = 0; dir = 1; }
    $("#hunt .marker").style.left = pos + "%";
    raf = requestAnimationFrame(frame);
  }
  function newZone() {
    const w = 18 - shots * 3;
    const start = 8 + Math.random() * (84 - w);
    zone = [start, start + w];
    const z = $("#hunt .zone");
    z.style.left = start + "%"; z.style.width = w + "%";
    z.textContent = ["🐇", "🦃", "🦌"][shots] || "🐇";
  }
  function label() {
    $("#hunt .hunt-shots").textContent = "Shots: " + "➶".repeat(3 - shots).padEnd(3, "·") + "   Hits: " + hits;
  }
  function play(cb) {
    done = cb; shots = 0; hits = 0; pos = 0; dir = 1; speed = 0.9; last = 0; locked = false;
    $("#hunt").hidden = false;
    newZone(); label();
    raf = requestAnimationFrame(frame);
  }
  function shoot() {
    if (locked || shots >= 3) return;
    const hit = pos >= zone[0] - 1 && pos <= zone[1] + 1;
    if (hit) hits++;
    shots++;
    track().classList.add(hit ? "hit" : "miss");
    locked = true;
    label();
    setTimeout(() => {
      track().classList.remove("hit", "miss");
      locked = false;
      if (shots >= 3) {
        cancelAnimationFrame(raf);
        $("#hunt").hidden = true;
        done(hits);
      } else { speed += 0.35; newZone(); }
    }, 450);
  }
  $("#btn-shoot").addEventListener("pointerdown", (e) => { e.preventDefault(); shoot(); });
  return { play, shoot };
})();

// ------------------------------------------------------------------ results
function showResults(v) {
  const r = v.result;
  const card = $("#card");
  const art = el("div", { class: "scene" });
  Art.render(art, r.won ? "victory" : "tomb", v.number);
  card.replaceChildren(
    art,
    el("p", { class: "kicker", text: v.mode === "daily" ? `Hunger Trail #${v.number} · District ${r.district}` : `Practice · District ${r.district}` }),
    el("h2", { class: "place" + (r.won ? " won" : ""), text: r.won ? "VICTOR" : r.placement_text }),
    el("p", { class: "emoji", text: r.emoji }),
    el("div", { class: "facts" },
      el("span", {}, el("b", { text: r.kills }), r.kills === 1 ? " kill" : " kills"),
      el("span", {}, "Day ", el("b", { text: r.day })),
      el("span", {}, el("b", { text: r.score.toLocaleString() }), " pts"),
      el("span", { text: "×" + r.multiplier })),
    el("p", { class: "cause", text: r.won ? "Won the Hunger Games." : "Killed by " + r.cause + "." }),
  );
  const canSubmit = v.mode === "daily" && !store.get("submitted." + v.date);
  $("#submit-box").hidden = !canSubmit;
  $("#submit-msg").textContent = "";
  $("#nickname").value = store.get("nick") || "";
  $("#btn-results-board").hidden = v.mode !== "daily";
  show("results");
}

async function share() {
  const text = view.result.share;
  try {
    if (navigator.share && matchMedia("(pointer: coarse)").matches) { await navigator.share({ text }); return; }
    await navigator.clipboard.writeText(text);
    toast("Copied. Paste it anywhere.");
  } catch (e) {
    if (e && e.name === "AbortError") return;
    const ta = el("textarea", { class: "copybox" });
    ta.value = text;
    $("#card").append(ta);
    ta.select();
    toast("Copy the text below.");
  }
}

async function submitScore() {
  const nickname = $("#nickname").value.trim();
  $("#submit-msg").textContent = "";
  try {
    const res = await api("/api/leaderboard", { token: view.token, nickname });
    store.set("nick", nickname);
    store.set("submitted." + view.date, nickname);
    $("#submit-box").hidden = true;
    toast(res.rank ? `You're #${res.rank} today.` : "Submitted.");
    openBoard();
  } catch (e) { $("#submit-msg").textContent = e.message; }
}

// ------------------------------------------------------------------ leaderboard
async function openBoard() {
  show("board");
  $("#board-date").textContent = `Hunger Trail #${daily.number}`;
  const rows = $("#board-rows");
  rows.replaceChildren(el("p", { class: "dim", text: "Reading the names..." }));
  try {
    const data = await api("/api/leaderboard?day=" + daily.date);
    const me = store.get("submitted." + daily.date);
    if (!data.rows.length) { rows.replaceChildren(el("p", { class: "dim", text: "No one has finished today's arena yet. Be the first name on the board." })); return; }
    rows.replaceChildren(...data.rows.map((r, i) => el("div", { class: "brow" + (r.nickname === me ? " me" : "") },
      el("span", { class: "rank", text: i + 1 }),
      el("span", { class: "who" }, el("b", { text: r.nickname }), el("small", { text: ` D${r.district} · ${r.placement === 1 ? "Victor" : "#" + r.placement} · ${r.kills}k` })),
      el("span", { class: "em", text: r.emoji }),
      el("span", { class: "pts", text: r.score.toLocaleString() }))));
  } catch (e) { rows.replaceChildren(el("p", { class: "dim", text: e.message })); }
}

// ------------------------------------------------------------------ countdown
function tickCountdown() {
  const left = Math.max(0, daily.next_in - Math.floor((Date.now() - dailyStarted) / 1000));
  const h = String(Math.floor(left / 3600)).padStart(2, "0");
  const m = String(Math.floor((left % 3600) / 60)).padStart(2, "0");
  const s = String(left % 60).padStart(2, "0");
  $$(".countdown").forEach((c) => (c.textContent = `${h}:${m}:${s}`));
  if (left === 0) setTimeout(() => location.reload(), 1500);
}

// ------------------------------------------------------------------ wiring
async function resume(rec, fallback) {
  try {
    const v = await api("/api/run/view", { token: rec.token });
    save(v);
    if (v.over) { view = v; showResults(v); } else enterGame(v);
  } catch (e) { toast(e.message); fallback(); }
}

$("#btn-daily").onclick = () => {
  const rec = store.get(dailyKey());
  if (rec) resume(rec, () => { store.del(dailyKey()); openReaping("daily"); });
  else openReaping("daily");
};
$("#btn-practice").onclick = () => {
  const rec = store.get("practice");
  if (rec && !rec.over) resume(rec, () => openReaping("practice"));
  else openReaping("practice");
};
$("#btn-again").onclick = () => { store.del("practice"); openReaping("practice"); };
$("#btn-board").onclick = openBoard;
$("#btn-results-board").onclick = openBoard;
$("#btn-help").onclick = () => show("help");
$("#btn-start").onclick = startRun;
$("#btn-share").onclick = share;
$("#btn-submit").onclick = submitScore;
$("#nickname").addEventListener("keydown", (e) => { if (e.key === "Enter") submitScore(); });
$$(".back").forEach((b) => (b.onclick = () => { refreshTitle(); show("title"); }));
$$(".home").forEach((b) => (b.onclick = () => { refreshTitle(); show("title"); }));

Art.render($("#title .scene"), "title", daily.number);
refreshTitle();
tickCountdown();
setInterval(tickCountdown, 1000);
