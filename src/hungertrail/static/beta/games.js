"use strict";
// Beta minigames: fishing, hunting, hold still and the final fight. Each one plays in the overlay,
// steps the same integer simulation as the server (minigames.js), and hands back only what the
// player did: the ticks they pressed, released or tapped. The server replays those.

const Games = (() => {
  const T = Sim.TPS / 1000;  // ticks per millisecond
  const pct = (v) => (v / Sim.SPAN) * 100 + "%";
  let stop = null;  // ends the game that's running
  let keys = null;  // the running game's keyboard handler

  function mk(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function open(title, help) {
    const box = document.querySelector("#mg .mg-box");
    box.replaceChildren(mk("p", "hunt-title", title), mk("p", "dim small", help));
    document.getElementById("mg").hidden = false;
    return box;
  }

  function close(after) {
    stop = null;
    keys = null;
    setTimeout(() => { document.getElementById("mg").hidden = true; after(); }, 900);
  }

  // A fixed-rate clock: calls step(tick) once per simulated tick and draw() once per frame.
  function clock(step, draw) {
    const t0 = performance.now();
    let done = false, raf = 0, t = 0;
    function frame(now) {
      const want = Math.floor((now - t0) * T);
      while (!done && t < want) { if (step(t) === false) done = true; t++; }
      draw(t);
      if (!done) raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    return { tick: () => t, end: () => { done = true; cancelAnimationFrame(raf); } };
  }

  // Hold-to-act button (fishing and hiding). Records press and release ticks, strictly increasing.
  function holdButton(label, onToggle) {
    const b = mk("button", "big primary mg-hold", label);
    let down = false;
    const press = (v) => { if (v !== down) { down = v; onToggle(); } };
    b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture?.(e.pointerId); press(true); });
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) b.addEventListener(ev, () => press(false));
    b.addEventListener("contextmenu", (e) => e.preventDefault());
    keys = (e, isDown) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); press(isDown); } };
    return b;
  }

  function toggler(sim) {
    const toggles = [];
    return {
      toggles,
      flip() {
        let k = sim.tick();
        if (toggles.length && k <= toggles[toggles.length - 1]) k = toggles[toggles.length - 1] + 1;
        if (toggles.length < 400) toggles.push(k);
      },
      holding(t) { let n = 0; for (const x of toggles) if (x <= t) n++; return n % 2 === 1; },
    };
  }

  function verdict(box, ok, text) {
    const v = mk("p", "mg-verdict " + (ok ? "ok" : "bad"), text);
    box.append(v);
    if (navigator.vibrate) navigator.vibrate(ok ? 15 : [30, 40, 30]);
  }

  // ---------------------------------------------------------------- fishing
  function fish(p, done) {
    const box = open("FISHING", "Hold to raise your bar, let go to drop it. Keep the fish inside the bar until the line fills."
      + (p.kind === "leech" ? " This one's a 🪱 leech: let it go. Don't reel it in." : ""));
    const well = mk("div", "fishwell"), bar = mk("div", "fbar"), fishy = mk("div", "ffish", p.emoji);
    const meter = mk("div", "fmeter"), fill = mk("i");
    meter.append(fill);
    well.append(bar, fishy);
    const scene = mk("div", "fishscene");
    scene.append(well, meter);
    const line = mk("p", "hunt-shots", "Get ready...");
    let sim = null, tg = null;
    const s = Sim.fishInit(p);
    // Nothing moves until you've had a moment to look, or until you press, whichever comes first.
    const begin = () => {
      if (sim) return;
      sim = clock((t) => {
        Sim.fishStep(p, s, tg.holding(t));
        if (s.done) return false;
      }, draw);
      tg = toggler(sim);
      stop = () => sim.end();
    };
    const btn = holdButton("Hold to reel", () => { begin(); tg.flip(); });
    box.append(scene, line, btn);
    function draw() {
      bar.style.height = pct(p.bar);
      bar.style.bottom = pct(s.y);
      bar.classList.toggle("on", !!s.on);
      fishy.style.bottom = `calc(${pct(Sim.pathAt(p.path, s.t))} - 0.7rem)`;
      fill.style.height = Math.max(0, s.c) / 10 + "%";
      if (!sim) return;
      line.textContent = "Line: " + Math.max(0, Math.round(s.c / 10)) + "%" + (s.t < (p.grace || 0) ? " · reel in" : "");
      if (s.done && !box.querySelector(".mg-verdict")) {
        sim.end();
        btn.disabled = true;
        const caught = s.done === "caught";
        verdict(box, caught ? p.kind !== "leech" : p.kind === "leech",
          caught ? (p.kind === "leech" ? "✗ A LEECH" : "✓ CAUGHT") : (p.kind === "leech" ? "✓ LET IT GO" : "✗ IT GOT AWAY"));
        const used = tg.toggles.filter((x) => x < s.t);
        close(() => done(used));
      }
    }
    draw();
    const wait = setTimeout(begin, 1500);
    stop = () => { clearTimeout(wait); if (sim) sim.end(); };
  }

  // ---------------------------------------------------------------- hunting
  function hunt(p, done) {
    const many = p.shots.length > 1;
    const box = open("HUNTING", (many ? "Three arrows. " : "One throw. ") + "Shoot when the marker is on an animal. "
      + "The 🦌 feeds you more, but it bolts at your first miss.");
    const track = mk("div", "track hunt-track"), marker = mk("div", "marker");
    const line = mk("p", "hunt-shots");
    const btn = mk("button", "big primary", many ? "Shoot" : "Throw");
    btn.id = "btn-shoot";
    box.append(track, line, btn);
    const taps = [];
    let n = 0, deer = true, food = 0, sim = null, zones = [], locked = false;

    function label() {
      const left = p.shots.length - n;
      line.textContent = (many ? "Arrows: " + "➶".repeat(left).padEnd(3, "·") : "Spear: " + (left ? "ready" : "thrown"))
        + "   Food: " + food;
    }
    function shot() {
      const s = p.shots[n];
      track.replaceChildren();
      zones = s.animals.filter((a) => deer || a.kind !== "deer").map((a) => {
        const z = mk("div", "zone " + a.kind, a.emoji);
        z.style.width = pct(2 * a.half);
        track.append(z);
        return [a, z];
      });
      track.append(marker);
      label();
      locked = false;
      sim = clock((t) => { if (t >= s.duration) { fire(-1); return false; } }, (t) => {
        marker.style.left = pct(Sim.tri(s.speed * t));
        for (const [a, z] of zones) z.style.left = pct(Sim.pathAt(a.path, t) - a.half);
      });
    }
    function fire(t) {
      if (locked) return;
      locked = true;
      sim.end();
      const s = p.shots[n];
      const a = Sim.huntHit(s, t, deer);
      taps.push(t);
      if (a) { food += a.food; if (a.kind === "deer") deer = false; } else deer = false;
      track.classList.add(a ? "hit" : "miss");
      line.textContent = a ? `${a.emoji} Hit!` : t < 0 ? "Too slow. It's gone." : "Missed.";
      n++;
      setTimeout(() => {
        track.classList.remove("hit", "miss");
        if (n < p.shots.length) shot();
        else {
          label();
          btn.disabled = true;
          const room = p.room == null ? food : Math.max(0, Math.min(food, p.room));
          verdict(box, food > 0, food > 0 ? (room < food ? `✓ 🎒+${room} (pack full)` : `✓ 🎒+${food}`) : "✗ NOTHING");
          close(() => done(taps));
        }
      }, 650);
    }
    const shoot = () => { if (sim && !locked) fire(Math.min(sim.tick(), p.shots[n].duration - 1)); };
    btn.addEventListener("pointerdown", (e) => { e.preventDefault(); shoot(); });
    keys = (e, isDown) => { if (isDown && (e.key === " " || e.key === "Enter")) { e.preventDefault(); shoot(); } };
    shot();
    stop = () => sim && sim.end();
  }

  // ---------------------------------------------------------------- hold still
  function hide(p, done, ctx) {
    const who = p.boss ? ctx.nemesis || "They" : "Someone";
    const box = open("HOLD STILL", "Hold to stay down. Your breath runs out while you hold, so let go to breathe "
      + "when the light is away from you. Never while it's on you.");
    const field = mk("div", "hidefield");
    const walker = mk("div", "walker", p.boss ? "🥷" : "🧍"), cone = mk("div", "cone"), you = mk("div", "you", "😮‍💨");
    const rock = mk("div", "rock", "🪨");
    field.append(cone, walker, you, rock);
    you.style.left = rock.style.left = pct(p.me);
    cone.style.width = pct(2 * p.half);
    const breath = mk("div", "breath"), bfill = mk("i");
    breath.append(bfill);
    const line = mk("p", "hunt-shots", `${who} is looking for you.`);
    let sim, tg;
    const s = Sim.hideInit(p);
    const btn = holdButton("Hold to stay down", () => tg.flip());
    box.append(field, mk("p", "dim small mg-label", "Breath"), breath, line, btn);
    sim = clock((t) => {
      Sim.hideStep(p, s, tg.holding(t));
      if (s.done) return false;
    }, (t) => {
      const c = Sim.pathAt(p.path, Math.min(s.t, p.duration - 1));
      walker.style.left = cone.style.left = pct(c);
      const down = tg.holding(s.t);
      you.textContent = down ? "" : "😮‍💨";
      you.classList.toggle("down", down);
      cone.classList.toggle("lit", Math.abs(c - p.me) <= p.half);
      bfill.style.width = Math.max(0, s.b) / p.breath * 100 + "%";
      breath.classList.toggle("low", s.b < p.breath / 4);
      if (!s.done) line.textContent = `${Math.ceil((p.duration - s.t) / Sim.TPS)}s until they move on.`;
      if (s.done && !box.querySelector(".mg-verdict")) {
        sim.end();
        btn.disabled = true;
        if (s.done !== "hidden") { you.textContent = "😱"; field.classList.add("caught"); }
        verdict(box, s.done === "hidden", { hidden: "✓ HIDDEN", spotted: "✗ SPOTTED", gasp: "✗ YOU GASPED" }[s.done]);
        const used = tg.toggles.filter((x) => x <= s.t && x < p.duration);
        close(() => done(used));
      }
    });
    tg = toggler(sim);
    stop = () => sim.end();
  }

  // ---------------------------------------------------------------- the final fight
  function fight(p, done, ctx) {
    const box = open(p.stance === "careful" ? "HOLD YOUR GROUND" : "PRESS THE ATTACK",
      "Watch which side they wind up on. Block that side when the ring closes, then strike while the gap is open.");
    const field = mk("div", "fightfield");
    const foe = mk("div", "foe", ctx.foe || "🧍"), blade = mk("div", "blade", "🗡️"), ring = mk("div", "ring");
    const name = mk("div", "foename", ctx.opponent || "");
    field.append(ring, foe, blade, name);
    const row = mk("div", "blockrow");
    const left = mk("button", "big blockbtn", "◀ Block"), right = mk("button", "big blockbtn", "Block ▶");
    row.append(left, right);
    const strike = mk("button", "big primary strikebtn", "Strike");
    strike.disabled = true;
    const line = mk("p", "hunt-shots", "Watch them.");
    box.append(field, line, row, strike);
    let bt = -1, bs = -1, st = -1, blocked = null, sim = null, over = false;

    function block(side) {
      if (bt >= 0 || over) return;
      bt = sim.tick(); bs = side;
      blocked = Sim.fightRun(p, [bt, bs, -1])[0];
      (side ? right : left).classList.add(blocked ? "good" : "bad");
      line.textContent = blocked ? "Blocked! Now strike." : bt < p.block[0] ? "Too early." : bt > p.block[1] ? "Too late." : "Wrong side.";
      if (!blocked) finish();
    }
    function hit() {
      if (!blocked || st >= 0 || over) return;
      const t = sim.tick();
      if (t < p.open[0] || t >= p.open[1]) return;
      st = t;
      finish();
    }
    function finish() {
      if (over) return;
      over = true;
      sim.end();
      const [b, s] = Sim.fightRun(p, [bt, bs, st]);
      left.disabled = right.disabled = strike.disabled = true;
      field.classList.add(b ? (s ? "struck" : "parried") : "hurt");
      verdict(box, b, b ? (s ? "✓ BLOCKED · HIT" : "✓ BLOCKED") : "✗ THEY HIT YOU");
      close(() => done([bt, bs, st]));
    }
    sim = clock((t) => {
      if (bt < 0 && t > p.block[1]) { line.textContent = "Too slow."; finish(); return false; }
      if (blocked && t >= p.open[1]) { line.textContent = "The gap closes."; finish(); return false; }
    }, (t) => {
      const side = p.feint >= 0 && t >= p.feint ? 1 - p.first : p.first;
      const winding = t >= p.tell && t < p.impact + 6;
      blade.hidden = !winding;
      blade.classList.toggle("right", side === 1);
      foe.classList.toggle("lean-left", winding && side === 0);
      foe.classList.toggle("lean-right", winding && side === 1);
      // The ring closes on the blow; it turns green while a block would hold.
      if (t >= p.tell && bt < 0) {
        const k = Math.max(0, (p.impact - t) / (p.impact - p.tell));
        ring.style.transform = `translate(-50%, -50%) scale(${0.55 + 1.2 * k})`;
        ring.hidden = false;
        ring.classList.toggle("now", t >= p.block[0] && t <= p.block[1]);
      } else ring.hidden = true;
      if (blocked && !over) {
        const live = t >= p.open[0] && t < p.open[1];
        strike.disabled = !live;
        strike.classList.toggle("glow", live);
      }
    });
    left.addEventListener("pointerdown", (e) => { e.preventDefault(); block(0); });
    right.addEventListener("pointerdown", (e) => { e.preventDefault(); block(1); });
    strike.addEventListener("pointerdown", (e) => { e.preventDefault(); hit(); });
    keys = (e, isDown) => {
      if (!isDown) return;
      if (e.key === "ArrowLeft" || e.key === "a") { e.preventDefault(); block(0); }
      if (e.key === "ArrowRight" || e.key === "d") { e.preventDefault(); block(1); }
      if (e.key === " " || e.key === "ArrowUp" || e.key === "Enter") { e.preventDefault(); hit(); }
    };
    stop = () => sim && sim.end();
  }

  const GAMES = { fish, hunt, hide, fight };
  function play(p, done, ctx = {}) {
    if (stop) stop();
    GAMES[p.game](p, done, ctx);
  }
  const active = () => !document.getElementById("mg").hidden;
  document.addEventListener("keydown", (e) => { if (keys && active() && !e.repeat) keys(e, true); });
  document.addEventListener("keyup", (e) => { if (keys && active()) keys(e, false); });
  return { play, active, ICON: { fish: "🎣", hunt: "🏹", hide: "🤫", fight: "⚔️" } };
})();
