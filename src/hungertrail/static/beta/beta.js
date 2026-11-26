"use strict";
// Hunger Trail BETA front end. Same shape as the live app.js, with the beta's screen:
// three stats, a stakes line on every option, the Cornucopia draft, and a log that folds old days.

const CFG = JSON.parse(document.getElementById("config").textContent);
const API = "/api/beta";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const store = {
  get: (k) => { try { return JSON.parse(localStorage.getItem("htb." + k)); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem("htb." + k, JSON.stringify(v)); } catch {} },
  del: (k) => { try { localStorage.removeItem("htb." + k); } catch {} },
};

let view = null;
let shown = 0;
let busy = false;
let mode = "daily";
let daily = CFG.daily;
let dailyStarted = Date.now();
let blocks = {};

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
  const res = await fetch(API + path, body === undefined ? {} : {
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
let pick = { district: null };

function buildReaping() {
  const last = store.get("setup") || {};
  const dg = $("#districts");
  dg.replaceChildren();
  for (const d of CFG.districts) {
    dg.append(el("button", { class: "district", "data-id": d.id, onclick: () => chooseDistrict(d.id) },
      el("span", { class: "num", text: d.id }),
      el("span", { class: "name", text: d.name }),
      el("span", { class: "perk", text: d.perk })));
  }
  $("#name-partner").value = last.partner || "";
  $("#name-partner").placeholder = CFG.names[Math.floor(Math.random() * CFG.names.length)];
  $("#name-ally1").value = (last.allies || [])[0] || "";
  $("#name-ally2").value = (last.allies || [])[1] || "";
  chooseDistrict(last.district || null);
}

function chooseDistrict(id) {
  pick.district = id;
  $$(".district").forEach((b) => b.classList.toggle("on", String(b.dataset.id) === String(id)));
  const d = CFG.districts.find((x) => x.id === id);
  $("#perk-desc").replaceChildren(...(d ? [el("b", { text: `District ${d.id} · ${d.perk}: ` }), d.desc] : ["Tap a district to see its edge."]));
  $("#btn-start").disabled = !id;
}

function openReaping(m) {
  mode = m;
  $("#reaping-title").textContent = m === "daily" ? `The Reaping · β #${daily.number}` : "The Reaping · Practice";
  buildReaping();
  show("reaping");
}

async function startRun() {
  if (busy) return;
  busy = true;
  const setup = {
    district: pick.district,
    partner: $("#name-partner").value.trim() || $("#name-partner").placeholder,
    allies: [$("#name-ally1").value.trim(), $("#name-ally2").value.trim()].filter(Boolean),
  };
  store.set("setup", setup);
  try {
    const v = await api("/run", { mode, ...setup });
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
  blocks = {};
  lastStatus = null;
  $("#slotpop").hidden = true;
  $("#log").replaceChildren();
  show("game");
  render(true);
}

// Tap a kit slot: what it does, and a Use button when it can be used right now.
function slotInfo(k, s) {
  const pop = $("#slotpop");
  const usable = s.can_use === k.id && !busy;
  pop.replaceChildren(
    el("p", {}, el("b", { text: `${k.emoji} ${k.name}` }), " " + k.desc),
    usable ? el("button", { class: "primary", text: "Use it", onclick: () => { pop.hidden = true; send(99, null); } }) : null,
    el("button", { text: "Close", onclick: () => (pop.hidden = true) }));
  pop.hidden = false;
}

let lastStatus = null;

function statusBar(s) {
  const hw = s.health_word.replace(" ", "").toLowerCase();
  const slots = [];
  for (let i = 0; i < s.slots; i++) {
    const k = s.kit[i];
    slots.push(k
      ? el("button", { class: "slot" + (s.can_use === k.id ? " usable" : ""), title: k.name, onclick: () => slotInfo(k, view.status) }, k.emoji)
      : el("span", { class: "slot empty", title: "empty slot" }, "·"));
  }
  const sup = s.supplies === 0 ? "none" : s.supplies === 1 ? "1 day" : `${s.supplies} days`;
  const prev = lastStatus;
  const hpBar = el("span", { class: "hbar" });
  const from = prev ? Math.max(0, prev.health) : Math.max(0, s.health);
  const to = Math.max(0, s.health);
  // A health loss leaves a red ghost of what you lost, which then drains away.
  if (to < from) hpBar.append(el("i", { class: "ghost", style: `width:${from}%` }));
  hpBar.append(el("i", { class: "fill", style: `width:${to}%` }));
  const hp = el("span", { class: "stat health h-" + hw, title: s.health_word }, "❤️ ", el("b", { text: Math.max(0, s.health) }), hpBar);
  const food = el("span", { class: "stat food" + (s.supplies === 0 ? " warn" : s.supplies === 1 ? " low" : "") }, "🎒 ", el("b", { text: sup }));
  if (prev) {
    if (s.health !== prev.health) hp.classList.add("pulse", s.health > prev.health ? "up" : "down");
    if (s.supplies !== prev.supplies) food.classList.add("pulse", s.supplies > prev.supplies ? "up" : "down");
  }
  if (to < from) requestAnimationFrame(() => setTimeout(() => { const g = $(".ghost", hpBar); if (g) g.style.width = to + "%"; }, 450));
  // The first time the medkit can be used, say how.
  if (s.can_use === "medkit" && !(prev && prev.can_use)) {
    const k = "tip.medkit";
    if (!store.get(k)) { store.set(k, true); setTimeout(() => toast("Tip: tap your 🩹 medkit to heal 40."), 900); }
  }
  lastStatus = s;
  const who = [s.kills ? `⚔️ ${s.kills}` : "", s.allies.length ? "🤝 " + s.allies.join(", ") : ""].filter(Boolean).join(" · ");
  let whoEl = el("span", { class: "who", text: who });
  if (s.fight) {
    // The final fight: their health, next to yours.
    const fb = el("span", { class: "hbar foebar" });
    const was = prev && prev.fight ? prev.fight.them : s.fight.them;
    const w = (n) => (n / s.fight.max) * 100 + "%";
    if (s.fight.them < was) fb.append(el("i", { class: "ghost", style: `width:${w(was)}` }));
    fb.append(el("i", { class: "fill", style: `width:${w(s.fight.them)}` }));
    if (s.fight.them < was) requestAnimationFrame(() => setTimeout(() => { const g = fb.querySelector(".ghost"); if (g) g.style.width = w(s.fight.them); }, 450));
    whoEl = el("span", { class: "who foe" + (s.fight.them < was ? " pulse down" : "") },
      `${s.fight.name.split(" (")[0]} ❤️ `, el("b", { text: s.fight.them }), fb);
  }
  return [
    el("div", { class: "row1" },
      el("span", { class: "when", text: s.fight ? `Day ${s.day} · Final fight` : `Day ${s.day} · ${s.remaining} left` }),
      whoEl,
      el("button", { class: "mute", title: "Sound", text: Sound.on() ? "🔊" : "🔇", onclick: (e) => { Sound.toggle(); e.target.textContent = Sound.on() ? "🔊" : "🔇"; } })),
    el("div", { class: "row2" }, hp, food, el("span", { class: "kit" }, ...slots)),
  ];
}

function dayBlock(day) {
  if (!blocks[day]) {
    const d = el("details", { class: "dayblock", open: "" });
    const sum = el("summary", { class: "daysum" });
    const body = el("div", { class: "daybody" });
    d.append(sum, body);
    $("#log").append(d);
    for (const k of Object.keys(blocks)) if (+k < day) blocks[k].d.open = false;
    blocks[day] = { d, sum, body, choices: [], deltas: [] };
    updateSummary(day);
  }
  return blocks[day];
}

function updateSummary(day) {
  const b = blocks[day];
  const head = day === 1 ? "Day 1 · Bloodbath" : `Day ${day}`;
  const bits = [b.choices.join(" · "), b.deltas.join("  ")].filter(Boolean);
  b.sum.replaceChildren(el("b", { text: head }), ...(bits.length ? [el("span", { class: "sumtext", text: " · " + bits.join(" · ") })] : []));
}

function hornScene(layout) {
  let box = $("#horn");
  if (!box) {
    box = el("div", { id: "horn", class: "scene horn" });
    Art.render(box, "title", daily.number);
    dayBlock(1).body.prepend(box);
  }
  $$(".loot", box).forEach((n) => n.remove());
  const spots = { horn: [[62, 70], [70, 78]], mid: [[46, 84], [82, 82]], edge: [[28, 92], [93, 90]] };
  const used = { horn: 0, mid: 0, edge: 0 };
  for (const it of layout) {
    const [x, y] = spots[it.ring][used[it.ring]++];
    box.append(el("span", { class: "loot" + (it.taken ? " taken" : ""), style: `left:${x}%;top:${y}%`, text: it.emoji }));
  }
}

function render(first = false) {
  const v = view;
  $("#status").replaceChildren(...statusBar(v.status));
  const fresh = v.log.slice(shown);
  let delay = 0;
  let tone = null;
  for (const entry of fresh) {
    const b = dayBlock(entry.day);
    let node;
    if (entry.kind === "roll") {
      // The reveal: the roll against the odds, big and coloured, right under your choice.
      tone = entry.ok ? "ok" : entry.near ? "near" : "bad";
      let head = entry.ok ? "✓ MADE IT" : entry.near ? "✗ SO CLOSE" : "✗ MISSED";
      let note = entry.near ? `${entry.chance}% · half damage` : `${entry.chance}%`;
      if (entry.game) {
        head = (entry.ok ? "✓ " : entry.near ? "" : "✗ ") + entry.label;
        note = `${Games.ICON[entry.game] || "⛏️"} ${entry.note}`;
      }
      node = el("p", { class: "entry roll " + tone }, el("b", { text: head }), el("span", { text: note }));
      if (!first && navigator.vibrate) setTimeout(() => navigator.vibrate(entry.ok ? 15 : [30, 40, 30]), delay);
    } else {
      const text = entry.kind === "choice" ? "> " + entry.text : entry.text;
      node = el("p", { class: "entry " + entry.kind, text });
    }
    if (entry.kind === "choice") { tone = null; b.choices.push(entry.text); updateSummary(entry.day); }
    if (entry.kind === "delta") {
      const gain = /[+]/.test(entry.text) && !/−/.test(entry.text);
      const loss = /−/.test(entry.text) && !/[+]/.test(entry.text);
      node.classList.add(gain ? "gain" : loss ? "loss" : "mixed");
      if (tone) node.classList.add("after-" + tone);
      tone = null;
      b.deltas.push(entry.text); updateSummary(entry.day);
    }
    if (!first) {
      node.style.animationDelay = delay + "ms";
      delay += entry.kind === "choice" ? 50 : entry.kind === "roll" ? 140 : entry.kind === "delta" ? 110 : 200;
      if (entry.kind === "cannon" || entry.kind === "death") setTimeout(() => { Sound.boom(); document.body.classList.add("shake"); setTimeout(() => document.body.classList.remove("shake"), 500); }, delay);
    } else node.classList.add("instant");
    b.body.append(node);
  }
  shown = v.log.length;
  if (v.layout) hornScene(v.layout);
  const log = $("#log");
  const scroll = () => log.scrollTo({ top: log.scrollHeight, behavior: first ? "auto" : "smooth" });
  scroll();
  if (!first) setTimeout(scroll, delay + 350);

  const box = $("#options");
  box.replaceChildren();
  box.classList.toggle("draft", v.card === "draft");
  box.classList.toggle("sifting", !!v.sift);
  if (v.over) {
    box.append(el("button", { class: "big primary appear", style: `animation-delay:${delay + 300}ms`, text: v.result.won ? "Claim your crown" : "See how you did", onclick: () => showResults(v) }));
    return;
  }
  if (v.sift) box.append(siftGrid(v.sift, v.options, delay));
  v.options.filter((o) => o.dig == null).forEach((o, i) => box.append(optionButton(o, i, delay, v.card)));
  if (v.card === "draft" && v.status.day === 1 && !v.log.some((e) => e.kind === "roll")) {
    box.append(el("p", { class: "legend appear", style: `animation-delay:${delay + 300}ms` },
      el("span", { class: "lose", text: "✗" }), " what a miss costs  ☠️ could kill you"));
  }
  animating = delay > 0 ? setTimeout(() => (animating = 0), delay + 400) : 0;
}

// A tap on the story while it's still appearing shows it all at once.
let animating = 0;
function skipAnimation() {
  if (!animating) return;
  clearTimeout(animating);
  animating = 0;
  $$("#log .entry, #options .appear").forEach((n) => (n.style.animationDelay = "0ms"));
  const log = $("#log");
  log.scrollTo({ top: log.scrollHeight });
}

// The sift: a 5x5 grid of rubble. Tap a tile to dig it; "Dig wide" turns a cross of five.
let wide = false;
function siftGrid(sf, options, delay) {
  const wrap = el("div", { class: "sift appear", style: `animation-delay:${delay}ms` });
  const digging = sf.how != null;
  const byTile = {};
  for (const o of options) if (o.dig != null) (byTile[o.dig] = byTile[o.dig] || {})[o.big ? "big" : "small"] = o;
  const grid = el("div", { class: "siftgrid" + (digging ? "" : " idle") });
  const cross = (i) => {
    const n = sf.size, x = i % n, y = Math.floor(i / n);
    const near = [[0, -1], [-1, 0], [1, 0], [0, 1]].filter(([dx, dy]) => x + dx >= 0 && x + dx < n && y + dy >= 0 && y + dy < n);
    return [i, ...near.map(([dx, dy]) => (y + dy) * n + x + dx)];
  };
  sf.tiles.forEach((t, i) => {
    const cell = el("button", { class: "tile" + (t.open ? " open" : "") + (t.marked ? " marked" : ""), "data-i": i });
    if (t.open) {
      cell.append(el("span", { class: "what", text: t.emoji }));
      if (t.glint) cell.append(el("span", { class: "tell glint", text: "✨" }));
      if (t.scorch) cell.append(el("span", { class: "tell scorch", text: "🔥" }));
      cell.disabled = true;
    } else {
      if (t.marked) cell.append(el("span", { class: "tell glint", text: "✨" }));
      cell.disabled = !digging;
      cell.onclick = () => {
        const o = byTile[i] && byTile[i][wide ? "big" : "small"];
        if (o) pickOption(o);
      };
      cell.onpointerenter = () => { if (wide && digging) cross(i).forEach((j) => grid.children[j].classList.add("aim")); };
      cell.onpointerleave = () => grid.querySelectorAll(".tile.aim").forEach((c) => c.classList.remove("aim"));
    }
    grid.append(cell);
  });
  wrap.append(grid);
  if (digging) {
    const small = el("button", { class: "mode" + (wide ? "" : " on"), text: "⛏️ Dig one" });
    const big = el("button", { class: "mode" + (wide ? " on" : ""), text: "⛏️ Dig wide: five" });
    small.onclick = () => { wide = false; small.classList.add("on"); big.classList.remove("on"); };
    big.onclick = () => { wide = true; big.classList.add("on"); small.classList.remove("on"); };
    wrap.append(el("div", { class: "siftbar" },
      el("span", { class: "digs", text: `${sf.digs} dig${sf.digs === 1 ? "" : "s"} left` }), small, big));
  } else wide = false;
  return wrap;
}

function optionButton(o, i, delay, card) {
  const stakes = el("span", { class: "stakes" });
  if (o.place) {
    // Places show what they're for and what they risk, not odds: the odds come with the event.
    o.win.forEach((w) => stakes.append(el("span", { class: "win", text: w })));
    o.lose.forEach((w) => stakes.append(el("span", { class: "lose" + (w.includes("Gamemakers") ? " deadly" : ""), text: w })));
  } else if (o.chance != null || (o.minigame && o.lose.length)) {
    stakes.append(el("span", { class: "win", text: "✓ " + (o.win.length ? o.win.join(" ") : "you're fine") }));
    stakes.append(el("span", { class: "lose" + (o.deadly ? " deadly" : ""), text: "✗ " + (o.lose.length ? o.lose.join(" ") : "nothing lost") }));
  } else if (o.win.length) {
    stakes.append(el("span", { class: "win", text: "→ " + o.win.join(" ") }));
  }
  if (card === "draft" && o.chance != null) {
    // Compact two-column grab buttons, so the Cornucopia and the story stay on screen.
    return el("button", { class: "option grab appear", style: `animation-delay:${delay + i * 40}ms`, onclick: () => pickOption(o) },
      el("span", { class: "label" },
        el("span", { class: "ltext", text: o.win[0] }),
        el("small", { class: "hint", text: o.hint.split(". ").slice(1).join(". ") }),
        el("span", { class: "stakes" }, el("span", { class: "lose" + (o.deadly ? " deadly" : ""), text: "✗ " + o.lose.join(" ") }))),
      el("span", { class: "chance " + (o.chance >= 70 ? "good" : o.chance >= 45 ? "even" : "long"), text: o.chance + "%" }));
  }
  const hint = o.place ? null : o.hint;  // places speak through their chips
  return el("button", { class: "option appear" + (o.place ? " where" : ""), style: `animation-delay:${delay + i * 60}ms`, onclick: () => pickOption(o) },
    el("span", { class: "key", text: i + 1 }),
    el("span", { class: "label" },
      el("span", { class: "ltext", text: o.label }),
      hint ? el("small", { class: "hint", text: hint }) : null,
      stakes.childNodes.length ? stakes : null),
    o.chance != null ? el("span", { class: "chance " + (o.chance >= 70 ? "good" : o.chance >= 45 ? "even" : "long"), text: o.chance + "%" })
      : o.minigame ? el("span", { class: "chance game", title: "a minigame", text: Games.ICON[o.minigame.game] }) : null);
}

async function pickOption(o) {
  if (busy || !view || view.over || Games.active()) return;
  if (o.minigame) {
    const f = view.status.fight;
    const ctx = { opponent: f ? f.name : "", nemesis: view.nemesis, foe: f && f.boss ? "🥷" : "🧍" };
    return Games.play(o.minigame, (inputs) => send(o.index, inputs), ctx);
  }
  send(o.index, null);
}

async function send(index, param) {
  busy = true;
  $$("#options button").forEach((b) => (b.disabled = true));
  try {
    const v = await api("/run/choose", { token: view.token, index, param });
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
  if (Games.active() || $("#game").hidden || !view || e.target.tagName === "INPUT") return;
  const opts = view.options.filter((o) => o.dig == null);
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= opts.length) pickOption(opts[n - 1]);
});

// ------------------------------------------------------------------ results
function showResults(v) {
  const r = v.result;
  const art = el("div", { class: "scene" });
  Art.render(art, r.won ? "victory" : "tomb", v.number);
  $("#card").replaceChildren(...[
    art,
    el("p", { class: "kicker", text: v.mode === "daily" ? `Hunger Trail Beta #${v.number} · District ${r.district}` : `Practice · District ${r.district}` }),
    el("h2", { class: "place" + (r.won ? " won" : ""), text: r.won ? "VICTOR" : r.placement_text }),
    el("p", { class: "emoji", text: r.emoji }),
    el("div", { class: "facts" },
      el("span", {}, el("b", { text: r.kills }), r.kills === 1 ? " kill" : " kills"),
      el("span", {}, "Day ", el("b", { text: r.day })),
      el("span", {}, el("b", { text: r.score.toLocaleString() }), " pts")),
    el("p", { class: "cause", text: r.won ? "Won the Hunger Games." : "Killed by " + r.cause + "." }),
    !r.won && r.recap ? el("p", { class: "recap dim", text: r.recap }) : null,
  ].filter(Boolean)
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
    const res = await api("/leaderboard", { token: view.token, nickname });
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
  $("#board-date").textContent = `Hunger Trail β #${daily.number}`;
  const rows = $("#board-rows");
  rows.replaceChildren(el("p", { class: "dim", text: "Reading the names..." }));
  try {
    const data = await api("/leaderboard?day=" + daily.date);
    const me = store.get("submitted." + daily.date);
    if (!data.rows.length) { rows.replaceChildren(el("p", { class: "dim", text: "No one has finished today's beta arena yet. Be the first name on the board." })); return; }
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
    const v = await api("/run/view", { token: rec.token });
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
  if (rec && !rec.over) resume(rec, () => { store.del("practice"); openReaping("practice"); });
  else openReaping("practice");
};
$("#btn-again").onclick = () => { store.del("practice"); openReaping("practice"); };
$("#btn-board").onclick = openBoard;
$("#btn-results-board").onclick = openBoard;
$("#btn-help").onclick = () => show("help");
$("#btn-start").onclick = startRun;
$("#log").addEventListener("pointerdown", skipAnimation);
$("#btn-share").onclick = share;
$("#btn-submit").onclick = submitScore;
$("#nickname").addEventListener("keydown", (e) => { if (e.key === "Enter") submitScore(); });
$$(".back").forEach((b) => (b.onclick = () => { refreshTitle(); show("title"); }));
$$(".home").forEach((b) => (b.onclick = () => { refreshTitle(); show("title"); }));

Art.render($("#title .scene"), "title", daily.number);
refreshTitle();
tickCountdown();
setInterval(tickCountdown, 1000);
