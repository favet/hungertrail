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
// Only the first run of today's arena is ranked: a replay knows the pile, the nemesis and the finale.
function playedToday() { return !!store.get("played." + daily.date); }

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
      el("span", { class: "perk", text: d.perk }),
      el("span", { class: "mult", text: "×" + d.mult })));
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
  $("#perk-desc").replaceChildren(...(d ? [el("b", { text: `District ${d.id} · ${d.perk}: ` }), d.desc,
    el("span", { class: "multnote", text: d.mult === 1 ? " Scores ×1: the Capitol's favorites start strong." : d.mult === 2 ? " Scores ×2." : " Scores ×3: you start with only 1 day of food." })]
    : ["Tap a district to see its edge. Poorer districts score more."]));
  $("#btn-start").disabled = !id;
}

function openReaping(m) {
  mode = m;
  $("#reaping-title").textContent = m === "daily" ? `The Reaping · Arena #${daily.number}` + (playedToday() ? " · replay" : "") : "The Reaping · Practice";
  $("#reaping-note").hidden = !(m === "daily" && playedToday());
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
    const v = await api("/run", { mode, ...setup, ranked: mode !== "daily" || !playedToday() });
    if (mode === "daily") store.set("played." + daily.date, true);
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
  pop.className = "";
  const usable = s.can_use === k.id && !busy;
  pop.replaceChildren(
    el("p", {}, el("b", { text: `${k.emoji} ${k.name}` }), " " + k.desc),
    usable ? el("button", { class: "primary", text: "Use it", onclick: () => { pop.hidden = true; send(99, null); } }) : null,
    el("button", { text: "Close", onclick: () => (pop.hidden = true) }));
  pop.hidden = false;
}

// The sponsors: the crowd's stars pay for a parachute, which drops that night.
function openSponsors(s) {
  const pop = $("#slotpop");
  pop.className = "";
  const rows = s.menu.map((m) => {
    const b = el("button", { class: "buy", onclick: () => { pop.hidden = true; send(m.index, null); } },
      el("span", { class: "bname", text: `${m.icon} ${m.label}` }),
      el("span", { class: "bprice", text: m.note || `⭐ ${m.price}` }));
    b.disabled = !m.can || busy;
    return b;
  });
  pop.replaceChildren(
    el("p", {}, el("b", { text: `Sponsors · ⭐ ${s.stars} of ${s.stars_max}` })),
    el("p", { class: "dim small", text: s.order
      ? `Tonight's parachute: ${s.order.split(":")[0].toLowerCase()}. One a night.`
      : s.fight ? "The whole Capitol is watching the final fight. A parachute lands at once."
      : "Your stars pay for one parachute a night. It drops at nightfall. In the final fight it lands at once. Every star you keep adds 10% to your score." }),
    el("div", { class: "buylist" }, ...rows),
    el("p", { class: "dim small", text: "Earn stars with long shots that land, kills, playing to the cameras, and sharing what you can't carry. Walking away costs one." }),
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
  if (s.menu && s.menu.some((m) => m.can) && !(prev && prev.menu && prev.menu.some((m) => m.can))) {
    const k = "tip.sponsors";
    if (!store.get(k)) { store.set(k, true); setTimeout(() => toast("Tip: tap ⭐ to spend the crowd's stars on a sponsor gift."), 1200); }
  }
  // The first time the medkit can be used, say how.
  if (s.can_use === "medkit" && !(prev && prev.can_use)) {
    const k = "tip.medkit";
    if (!store.get(k)) { store.set(k, true); setTimeout(() => toast("Tip: tap your 🩹 medkit to heal 40."), 900); }
  }
  lastStatus = s;
  const who = [s.kills ? `⚔️ ${s.kills}` : "", s.allies.length ? "🤝 " + s.allies.join(", ") : ""].filter(Boolean).join(" · ");
  // With nothing to eat, what tonight will cost takes the place of who's with you.
  let whoEl = s.hunger ? el("span", { class: "who hunger", text: `No food: −${s.hunger} ❤️ tonight` }) : el("span", { class: "who", text: who });
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
      el("button", { class: "stars" + (s.order ? " ordered" : "") + (s.menu && s.menu.some((m) => m.can) ? " can" : ""),
        title: "The crowd's stars: spend them on sponsor gifts", onclick: () => openSponsors(view.status) },
        "⭐ ", el("b", { text: s.stars }), s.order ? el("small", { text: " 🪂 tonight" }) : null),
      whoEl,
      el("button", { class: "mute", title: "Settings", text: "⚙️", onclick: () => openSettings(true) })),
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
    if (entry.game || entry.head) {
      head = entry.head || entry.label;
      note = entry.note;
    }
    node = el("p", { class: "entry roll " + tone + (entry.dig ? " digline" : "") }, el("b", { text: head }), el("span", { text: note }));
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
    else if (e.kind === "roll") bits.push(e.head || (e.game ? e.label : e.ok ? "made it" : e.near ? "close call" : "missed"));
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
  const dh = num(/❤️\s*([+−-])(\d+)/), ds = num(/🎒\s*([+−-])(\d+)/), dst = num(/⭐\s*([+−-])(\d+)/);
  if (!dh && !ds && !dst) return;
  const s = { ...lastStatus, health: Math.max(0, lastStatus.health + dh), supplies: Math.max(0, lastStatus.supplies + ds),
    stars: Math.max(0, (lastStatus.stars || 0) + dst) };
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
    if (!first && entry.kind === "roll" && !entry.game) await Wheel.spin(entry, hurry || !!entry.dig);
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
  box.classList.toggle("digging", !!v.dig);
  const delay = 80;
  if (v.over) {
    box.append(el("button", { class: "big primary appear", style: `animation-delay:${delay}ms`, text: v.result.won ? "Claim your crown" : "See how you did", onclick: () => showResults(v) }));
    return;
  }
  if (v.dig) box.append(digPanel(v.dig, delay));
  if (v.card === "draft") return box.append(hornDraft(v, delay));
  v.options.forEach((o, i) => box.append(o.pack ? packScreen(o, delay) : optionButton(o, i, delay, v.card)));
  // What an item you don't have would let you do here.
  for (const l of v.locked || []) {
    box.append(el("div", { class: "option locked appear", style: `animation-delay:${delay + 200}ms` },
      el("span", { class: "key", text: "·" }),
      el("span", { class: "label" }, el("span", { class: "ltext", text: l.label }), el("small", { class: "hint", text: "Needs " + l.needs + "." }))));
  }
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

// The dig at the horn: push your luck. What's still buried, how close the Careers are, what you've
// dug out. Each dig spins a wheel with one slice per buried thing (wheel.js).
function digPanel(d, delay) {
  const row = (name, ...kids) => el("div", { class: "digrow" }, el("span", { class: "dname", text: name }), el("span", { class: "dwhat" }, ...kids));
  const ears = [];
  for (let i = 0; i < d.caught; i++) ears.push(el("i", { class: i < d.heard ? "on" : "", text: i < d.heard ? "●" : "○" }));
  const buried = d.buried.map((b) => el("span", { class: "bur" + (b.use ? "" : " useless"), title: b.use ? "" : "no use to you now",
    text: b.icon }));
  const useless = d.buried.some((b) => !b.use);
  return el("div", { class: "dig appear", style: `animation-delay:${delay}ms` },
    row("Buried", ...buried),
    useless ? el("p", { class: "dignote dim", text: "Dimmed: no use to you now (you'd share it)." }) : null,
    row("Noise", ...ears, el("span", { class: "dim", text: d.heard >= d.caught - 1 ? " the next one brings a Career" : " the third brings a Career" })),
    row("Yours", d.haul.length ? d.haul.join(" ") : el("span", { class: "dim", text: "nothing yet" })));
}

// The pack screen: everything you carry and everything you just found, together. Tap to keep up to
// three, then keep them. What you leave stays where it is.
function packScreen(o, delay) {
  const { items, slots } = o.pack;
  const keep = new Set(items.map((it, i) => (it.kit ? i : -1)).filter((i) => i >= 0).slice(0, slots));
  const wrap = el("div", { class: "packscreen appear", style: `animation-delay:${delay}ms` });
  const head = el("p", { class: "packhead" });
  const go = el("button", { class: "big primary" });
  const rows = items.map((it, i) => {
    const b = el("button", { class: "packitem" },
      el("span", { class: "pk-tick" }),
      el("span", { class: "pk-name" }, el("b", { text: `${it.emoji} ${it.name}` }), it.kit ? el("small", { class: "dim", text: " in your kit" }) : el("small", { class: "new", text: " new" })),
      el("small", { class: "pk-desc", text: it.desc }),
      it.note ? el("small", { class: "pk-note", text: it.note }) : null);
    b.onclick = () => { if (keep.has(i)) keep.delete(i); else if (keep.size < slots) keep.add(i); paint(); };
    return b;
  });
  function paint() {
    rows.forEach((b, i) => { b.classList.toggle("on", keep.has(i)); $(".pk-tick", b).textContent = keep.has(i) ? "✓" : "·"; });
    const left = items.filter((_, i) => !keep.has(i)).map((it) => it.name);
    head.textContent = `You can carry ${slots}. ${keep.size} chosen.` + (left.length ? ` Leaving the ${left.join(" and the ")}.` : "");
    go.textContent = `Keep ${keep.size === 1 ? "this" : "these"}`;
  }
  go.onclick = () => send(o.index, [...keep].sort((a, b) => a - b));
  wrap.append(head, ...rows, go);
  paint();
  return wrap;
}

function optionButton(o, i, delay, card) {
  const stakes = el("span", { class: "stakes" });
  if (o.place) {
    // Places say what they're for and what they risk, in words. The odds come with the event.
    stakes.append(el("span", { class: "win", text: "✓ " + o.win.join(", ") }));
    stakes.append(el("span", { class: "lose" + (o.lose.some((w) => w.includes("Gamemakers") || w.includes("seen here")) ? " deadly" : ""), text: "✗ " + o.lose.join(", ") }));
  } else if (o.chance != null || (o.minigame && o.lose.length)) {
    stakes.append(el("span", { class: "win", text: "✓ " + (o.win.length ? o.win.join(", ") : "you're fine") }));
    stakes.append(el("span", { class: "lose" + (o.deadly ? " deadly" : ""), text: "✗ " + (o.lose.length ? o.lose.join(", ") : "nothing lost") }));
  } else if (o.win.length || o.lose.length) {
    if (o.win.length) stakes.append(el("span", { class: o.win.every((w) => w.startsWith("−")) ? "lose" : "win", text: "→ " + o.win.join(", ") }));
    if (o.lose.length) stakes.append(el("span", { class: "lose", text: "✗ " + o.lose.join(", ") }));
  }
  const hint = o.place ? null : o.hint;  // places speak through their chips
  return el("button", { class: "option appear" + (o.place ? " where" : ""), style: `animation-delay:${delay + i * 60}ms`, onclick: () => pickOption(o) },
    el("span", { class: "key", text: i + 1 }),
    el("span", { class: "label" },
      el("span", { class: "ltext", text: o.label }),
      hint ? el("small", { class: "hint" + (o.danger ? " danger" : ""), text: hint }) : null,
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
  const opts = view.options.filter((o) => !o.pack);
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
    el("p", { class: "kicker", text: v.mode === "daily" ? `Arena #${v.number} · District ${r.district}` : `Practice · District ${r.district}` }),
    el("h2", { class: "place" + (r.won ? " won" : ""), text: r.won ? "VICTOR" : r.placement_text }),
    el("p", { class: "emoji", text: r.emoji }),
    el("div", { class: "facts" },
      el("span", {}, el("b", { text: r.kills }), r.kills === 1 ? " kill" : " kills"),
      el("span", {}, "Day ", el("b", { text: r.day })),
      el("span", {}, el("b", { text: r.score.toLocaleString() }), ` pts (×${r.multiplier})`)),
    el("p", { class: "cause", text: r.won ? "Won the Hunger Games." : r.cause === "giving up" ? "Gave up." : "Killed by " + r.cause + "." }),
    !r.won && r.recap ? el("p", { class: "recap dim", text: r.recap }) : null,
    r.score_parts ? el("p", { class: "scoreline dim", text: scoreLine(r.score_parts, r.district) }) : null,
    v.mode === "daily" && !r.ranked ? el("p", { class: "unranked small", text: "A replay of today's arena: not ranked. Only your first run goes on the board." }) : null,
  ].filter(Boolean)
  );
  $("#btn-retry").hidden = v.mode !== "daily";
  $("#btn-retry").textContent = "Play today's arena again (unranked)";
  const canSubmit = v.mode === "daily" && r.ranked && !store.get("submitted." + v.date);
  $("#submit-box").hidden = !canSubmit;
  $("#submit-msg").textContent = "";
  $("#nickname").value = store.get("nick") || "";
  $("#btn-results-board").hidden = v.mode !== "daily";
  show("results");
}

// How the score was built: points, then your district's multiplier, then 10% a star you kept.
function scoreLine(p, district) {
  const bits = [`${p.points.toLocaleString()} pts`, `× ${p.district} for District ${district}`];
  if (p.stars) bits.push(`× ${(1 + p.stars / 10).toFixed(1)} for ${p.stars} star${p.stars === 1 ? "" : "s"} kept`);
  return bits.join(" ") + ` = ${p.total.toLocaleString()}`;
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
  $("#board-date").textContent = `Arena #${daily.number}`;
  const rows = $("#board-rows");
  rows.replaceChildren(el("p", { class: "dim", text: "Reading the names..." }));
  try {
    const data = await api("/leaderboard?day=" + daily.date);
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

// ------------------------------------------------------------------ settings
// From the game: sound, back to the title (the run is saved), and a free restart. From the title:
// sound, and clearing what this device has saved.
function confirmButton(label, sure, act) {
  const b = el("button", { class: "danger", text: label });
  let armed = false;
  b.onclick = () => {
    if (!armed) { armed = true; b.textContent = sure; b.classList.add("armed"); return; }
    act();
  };
  return b;
}

function openSettings(inGame) {
  const pop = $("#slotpop");
  const close = () => (pop.hidden = true);
  const sound = el("button", { text: "Sound: " + (Sound.on() ? "on" : "off") });
  sound.onclick = () => { Sound.toggle(); sound.textContent = "Sound: " + (Sound.on() ? "on" : "off"); };
  const rows = [el("p", {}, el("b", { text: "Settings" })), sound];
  if (inGame && view && !view.over) {
    rows.push(el("button", { text: "Main menu (your run is saved)", onclick: () => { close(); refreshTitle(); show("title"); } }));
    // Restarting is free, in practice and in today's arena: a fresh run from the reaping.
    const daily = view.mode === "daily";
    rows.push(confirmButton(daily ? "Restart today's run (unranked)" : "Restart this practice run", "Tap again to restart", () => {
      close(); store.del(daily ? dailyKey() : "practice"); openReaping(view.mode);
    }));
  } else if (!inGame) {
    rows.push(el("p", { class: "dim small", text: "Clears this device's saved runs, tips and nickname. Use it if a run is stuck." }));
    rows.push(confirmButton("Reset this device", "Tap again to reset", () => {
      Object.keys(localStorage).filter((k) => k.startsWith("htb.")).forEach((k) => localStorage.removeItem(k));
      close(); refreshTitle(); toast("Reset. Everything saved on this device is gone.");
    }));
  }
  rows.push(el("button", { text: "Close", onclick: close }));
  pop.replaceChildren(...rows);
  pop.className = "settings";
  pop.hidden = false;
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
$("#btn-retry").onclick = () => { store.del(dailyKey()); openReaping("daily"); };
$("#btn-board").onclick = openBoard;
$("#btn-results-board").onclick = openBoard;
$("#btn-help").onclick = () => show("help");
$("#btn-settings").onclick = () => openSettings(false);
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
