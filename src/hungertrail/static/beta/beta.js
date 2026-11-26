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
  scene = null;
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

// ------------------------------------------------------------------ scenes
// One scene at a time: an event, your choice, the wheel, the result. Then "Continue" folds the scene
// to one line and the next one starts at the top of the screen. Past days fold to one line each.
const BREAKS = new Set(["event", "day", "night"]);
const SOFT = new Set(["round"]);  // a new exchange in the final fight: a fresh scene, no Continue
const HIDDEN = new Set(["day", "system"]);  // the day header and status bar already say these
let scene = null;  // { node, entries }
let renderGen = 0;
let hurry = false;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const visible = (page) => page.some((e) => e.kind !== "choice" && !HIDDEN.has(e.kind));

function paginate(entries) {
  const pages = [[]];
  for (const e of entries) {
    if (BREAKS.has(e.kind) && pages[pages.length - 1].length) pages.push([]);
    pages[pages.length - 1].push(e);
  }
  return pages;
}

function entryNode(entry) {
  let node;
  if (entry.kind === "roll") {
    const tone = entry.ok ? "ok" : entry.near ? "near" : "bad";
    let head = entry.ok ? "MADE IT" : entry.near ? (entry.got ? "GOT IT, BUT CUT" : "CLOSE CALL") : "MISSED";
    let note = entry.chance + "%" + (entry.near ? " · half damage" : "");
    if (entry.game) {
      head = entry.label;
      note = entry.note;
    }
    node = el("p", { class: "entry roll " + tone }, el("b", { text: head }), el("span", { text: note }));
  } else {
    node = el("p", { class: "entry " + entry.kind, text: entry.kind === "choice" ? "> " + entry.text : entry.text });
  }
  if (entry.kind === "delta") {
    const gain = /[+]/.test(entry.text) && !/−/.test(entry.text);
    const loss = /−/.test(entry.text) && !/[+]/.test(entry.text);
    node.classList.add(gain ? "gain" : loss ? "loss" : "mixed");
  }
  return node;
}

// A finished scene folds to one line: what you did and what it cost.
function foldScene(sc) {
  if (!sc || sc.node.classList.contains("past")) return;
  const bits = [];
  for (const e of sc.entries) {
    if (e.kind === "choice") bits.push(e.text);
    else if (e.kind === "roll") bits.push(e.game ? e.label : e.ok ? "made it" : e.near ? "close call" : "missed");
    else if (e.kind === "delta") bits.push(e.text);
  }
  if (!bits.length) {
    const first = sc.entries.find((e) => !HIDDEN.has(e.kind));
    if (!first) { sc.node.remove(); return; }
    bits.push(first.kind === "night" ? "Night" : first.text.length > 70 ? first.text.slice(0, 68) + "…" : first.text);
  }
  const sum = el("p", { class: "scenesum", text: bits.join(" · "), onclick: () => sc.node.classList.toggle("open") });
  sc.node.prepend(sum);
  sc.node.classList.add("past");
}

function newScene(day) {
  foldScene(scene);
  const node = el("div", { class: "sceneblk" });
  dayBlock(day).body.append(node);
  scene = { node, entries: [], day };
  return scene;
}

function scrollToScene() {
  const log = $("#log");
  if (!scene) return;
  const top = scene.node.getBoundingClientRect().top - log.getBoundingClientRect().top + log.scrollTop - 6;
  log.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
}

// A gain or loss lands on the status bar the moment its line appears, not at the end of the turn.
function applyDelta(text) {
  if (!lastStatus) return;
  const num = (re) => { const m = text.match(re); return m ? (m[1] === "+" ? 1 : -1) * +m[2] : 0; };
  const dh = num(/❤️\s*([+−-])(\d+)/), ds = num(/🎒\s*([+−-])(\d+)/);
  if (!dh && !ds) return;
  const s = { ...lastStatus, health: Math.max(0, lastStatus.health + dh), supplies: Math.max(0, lastStatus.supplies + ds) };
  $("#status").replaceChildren(...statusBar(s));
}

function noteEntry(entry, first) {
  const b = dayBlock(entry.day);
  if (entry.kind === "delta" && !first) applyDelta(entry.text);
  if (entry.kind === "choice") { b.choices.push(entry.text); updateSummary(entry.day); }
  if (entry.kind === "delta") { b.deltas.push(entry.text); updateSummary(entry.day); }
  if (entry.kind === "cannon" || entry.kind === "death") {
    Sound.boom();
    document.body.classList.add("shake");
    setTimeout(() => document.body.classList.remove("shake"), 500);
  }
}

// Wait for the player to tap Continue before the next scene.
function continueTap(gen) {
  return new Promise((done) => {
    const box = $("#options");
    box.className = "";
    const go = () => { document.removeEventListener("keydown", key); if (gen === renderGen) done(); };
    const key = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    box.replaceChildren(el("button", { class: "big primary continue appear", text: "Continue", onclick: go }));
    document.addEventListener("keydown", key);
  });
}

async function revealPage(page, gen, first) {
  hurry = false;
  let shownOne = false;
  for (const entry of page) {
    if (gen !== renderGen) return;
    if (!scene || BREAKS.has(entry.kind) || SOFT.has(entry.kind) || scene.day !== entry.day) {
      if (SOFT.has(entry.kind) && !first && !hurry) await sleep(900);
      newScene(entry.day);
      if (!first) scrollToScene();
    }
    if (!first && entry.kind === "roll" && !entry.game) await Wheel.spin(entry, hurry);
    else if (!first && shownOne && !HIDDEN.has(entry.kind) && !hurry) await sleep(entry.kind === "delta" ? 260 : 420);
    const node = entryNode(entry);
    if (first) node.classList.add("instant");
    scene.node.append(node);
    scene.entries.push(entry);
    noteEntry(entry, first);
    if (!HIDDEN.has(entry.kind)) shownOne = true;
    if (!first) node.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

async function render(first = false) {
  const gen = ++renderGen;
  const v = view;
  const fresh = v.log.slice(shown);
  shown = v.log.length;
  const pages = paginate(fresh);
  if (first) {
    $("#status").replaceChildren(...statusBar(v.status));
    for (const page of pages) await revealPage(page, gen, true);
    if (v.layout) hornScene(v.layout);
    $("#log").scrollTo({ top: $("#log").scrollHeight });
    scrollToScene();
    showOptions(v);
    return;
  }
  $("#options").replaceChildren();
  for (let p = 0; p < pages.length; p++) {
    if (p > 0 && visible(pages[p - 1])) await continueTap(gen);
    if (gen !== renderGen) return;
    // The status bar shows where the run ends up, so it changes with the last scene, not before.
    if (p === pages.length - 1 && p > 0) $("#status").replaceChildren(...statusBar(v.status));
    await revealPage(pages[p], gen, false);
  }
  if (gen !== renderGen) return;
  if (pages.length <= 1) $("#status").replaceChildren(...statusBar(v.status));
  if (v.layout) hornScene(v.layout);
  showOptions(v);
}

function showOptions(v) {
  const box = $("#options");
  box.replaceChildren();
  box.className = "";
  box.classList.toggle("draft", v.card === "draft");
  box.classList.toggle("sifting", !!v.sift);
  const delay = 80;
  if (v.over) {
    box.append(el("button", { class: "big primary appear", style: `animation-delay:${delay}ms`, text: v.result.won ? "Claim your crown" : "See how you did", onclick: () => showResults(v) }));
    return;
  }
  if (v.sift) box.append(siftGrid(v.sift, v.options, delay));
  if (v.card === "draft") return box.append(hornDraft(v, delay));
  v.options.filter((o) => o.dig == null).forEach((o, i) => box.append(optionButton(o, i, delay, v.card)));
}

// The Cornucopia: three rings, two things in each. One line of rules, then each ring says its odds
// and what a miss costs once, in words, and the things in it say only what they're for.
function hornDraft(v, delay) {
  const wrap = el("div", { class: "horndraft appear", style: `animation-delay:${delay}ms` });
  const grabs = v.options.filter((o) => o.ring);
  const first = !v.log.some((e) => e.kind === "roll" && e.day === 1);
  wrap.append(el("p", { class: "hornrule", text: first
    ? "Grab up to two things. The closer to the horn, the better the loot and the worse a miss. Miss, and you run with what you have."
    : "One more grab, or run. The Careers are here now: every grab is harder." }));
  for (const ring of ["edge", "mid", "horn"]) {
    const items = grabs.filter((o) => o.ring === ring);
    if (!items.length) continue;
    const o0 = items[0];
    const sec = el("div", { class: "hornring " + ring });
    sec.append(el("div", { class: "ringhead" },
      el("span", { class: "rname", text: { edge: "At the edge", mid: "Halfway in", horn: "At the horn" }[ring] }),
      el("span", { class: "rmiss" + (o0.deadly ? " deadly" : ""), text: o0.deadly ? "a miss could kill you" : `a miss: −${o0.dmg} health` }),
      el("span", { class: "chance " + (o0.chance >= 70 ? "good" : o0.chance >= 45 ? "even" : "long"), text: o0.chance + "%" })));
    const row = el("div", { class: "ringitems" });
    for (const o of items) {
      const [icon, ...name] = o.win[0].split(" ");
      row.append(el("button", { class: "grabitem", onclick: () => pickOption(o) },
        el("span", { class: "gname" }, el("i", { text: icon }), " " + name.join(" ")),
        el("small", { text: o.desc })));
    }
    sec.append(row);
    wrap.append(sec);
  }
  const run = v.options.find((o) => !o.ring);
  if (run) wrap.append(optionButton(run, 0, 0, "run"));
  return wrap;
}

// A tap on the story while it's still appearing shows the rest of the scene at once.
function skipAnimation() { hurry = true; }

// The sift, after Slay the Spire's Crystal Sphere: treasures cover several tiles, and one is yours
// only once every tile of it is open. A small dig opens one tile; a big dig opens a 3x3.
let bigDig = false;
let aim = null;
function siftGrid(sf, options, delay) {
  const wrap = el("div", { class: "sift appear", style: `animation-delay:${delay}ms` });
  const digging = sf.how != null;
  const n = sf.size;
  const byTile = {};
  for (const o of options) if (o.dig != null) (byTile[o.dig] = byTile[o.dig] || {})[o.big ? "big" : "small"] = o;
  const grid = el("div", { class: "siftgrid" + (digging ? "" : " idle"), style: `grid-template-columns:repeat(${n},1fr)` });
  const area = (i) => {
    const x = i % n, y = Math.floor(i / n), out = [];
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++)
      if (x + dx >= 0 && x + dx < n && y + dy >= 0 && y + dy < n) out.push((y + dy) * n + x + dx);
    return out;
  };
  const showAim = (i) => {
    grid.querySelectorAll(".tile.aim").forEach((c) => c.classList.remove("aim"));
    if (i == null) return;
    (bigDig ? area(i) : [i]).forEach((j) => grid.children[j].classList.add("aim"));
  };
  sf.tiles.forEach((t, i) => {
    const cell = el("button", { class: "tile", "data-i": i });
    if (t.open) {
      cell.classList.add("open");
      cell.disabled = true;
      if (t.shape != null) {
        cell.classList.add(t.trap ? "trap" : t.done ? "done" : "piece");
        for (const side of t.edge) cell.classList.add("e" + side);
        cell.append(el("span", { class: "what", text: t.icon }));
      }
    } else {
      cell.disabled = !digging;
      cell.onclick = () => {
        const o = byTile[i] && byTile[i][bigDig ? "big" : "small"];
        if (!o) return;
        // A big dig takes two taps: one to aim, one to dig. On a phone there's no hover to preview it.
        if (bigDig && aim !== i) { aim = i; showAim(i); hint.textContent = "Tap the same tile again to dig."; return; }
        aim = null;
        pickOption(o);
      };
      cell.onpointerenter = (e) => { if (digging && e.pointerType === "mouse") showAim(i); };
      cell.onpointerleave = (e) => { if (e.pointerType === "mouse") showAim(aim); };
    }
    grid.append(cell);
  });
  const hint = el("p", { class: "dim small sifthint", text: digging ? "Tap a tile to dig it." : "" });
  if (digging) wrap.append(grid);  // before you choose how to dig, just what's buried
  if (digging) {
    const small = el("button", { class: "mode" + (bigDig ? "" : " on"), text: "Small dig: 1 tile" });
    const big = el("button", { class: "mode" + (bigDig ? " on" : ""), text: "Big dig: 3×3" });
    const set = (b) => {
      bigDig = b; aim = null; showAim(null);
      small.classList.toggle("on", !b); big.classList.toggle("on", b);
      hint.textContent = b ? "Tap a tile to aim, then tap it again to dig." : "Tap a tile to dig it.";
    };
    small.onclick = () => set(false);
    big.onclick = () => set(true);
    wrap.append(el("div", { class: "siftbar" },
      el("span", { class: "digs", text: `${sf.digs} dig${sf.digs === 1 ? "" : "s"} left` }), small, big), hint);
  } else { bigDig = false; aim = null; }
  // What's buried, and its size. A treasure is yours once every tile of it is open.
  const legend = el("div", { class: "siftlegend" });
  if (!digging) legend.append(el("span", { class: "lghead", text: "Buried here. A find is yours once every tile of it is dug out." }));
  const groups = [];
  for (const t of sf.legend) {
    const name = t.name.replace(/^an? /, "").replace(/: .*/, "").replace(/^crate with a (.*)/, "$1 in a crate");
    const g = groups.find((x) => x.name === name && x.trap === t.trap);
    if (g) { g.n++; g.done += t.done; g.found = g.found || t.found; } else groups.push({ ...t, name, n: 1, done: +t.done });
  }
  for (const t of groups) {
    const state = t.trap ? (t.done ? "went off" : "") : t.done ? (t.n > 1 ? `${t.done} yours` : "yours") : t.found && t.n === 1 ? `${t.found}/${t.w * t.h}` : "";
    legend.append(el("span", { class: "lg" + (t.trap ? " trap" : "") + (t.done ? " done" : "") },
      `${t.icon} ${t.name}${t.n > 1 ? " ×" + t.n : ""} `, el("i", { text: `${t.w}×${t.h}` }), state ? el("b", { text: " " + state }) : null));
  }
  wrap.append(legend);
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
  if (o.wheel && o.chance != null) return Wheel.open(o, () => send(o.index, null));
  send(o.index, null);
}

async function send(index, param) {
  busy = true;
  $$("#options button").forEach((b) => (b.disabled = true));
  try {
    const v = await api("/run/choose", { token: view.token, index, param });
    view = v;
    save(v);
    await render();
  } catch (e) {
    Wheel.close();
    toast(e.message);
    $$("#options button").forEach((b) => (b.disabled = false));
  }
  busy = false;
}

document.addEventListener("keydown", (e) => {
  if (busy || Games.active() || $("#game").hidden || !view || e.target.tagName === "INPUT") return;
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
