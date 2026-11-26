"use strict";
// The wheel: every roll in the beta is a spin you watch. Green wins, red loses, and yellow (when a
// near miss counts) is the close call that halves the damage. The server rolls 1 to 100; the wheel
// only shows that roll, landing on it. Each edge you have (a weapon, an ally, your wounds) is its
// own stretch of the wheel with its icon on it, so you can see why the odds are what they are.

const Wheel = (() => {
  const NEAR = 10;
  let cur = null;  // the open wheel: { box, disc, label }

  const svgNS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    const e = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  }
  function mk(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  // A point on the wheel: p is 0-100 around from the top, clockwise.
  const pt = (p, r) => {
    const a = (p / 100) * 2 * Math.PI;
    return [(r * Math.sin(a)).toFixed(2), (-r * Math.cos(a)).toFixed(2)];
  };
  function slice(p0, p1, r, cls) {
    if (p1 - p0 >= 100) return svg("circle", { cx: 0, cy: 0, r, class: cls });
    const [x0, y0] = pt(p0, r), [x1, y1] = pt(p1, r);
    return svg("path", { d: `M0,0 L${x0},${y0} A${r},${r} 0 ${p1 - p0 > 50 ? 1 : 0} 1 ${x1},${y1} Z`, class: cls });
  }

  // Each edge's stretch of the wheel, in the order they're added to the card's base.
  function spans(odds) {
    let g = odds.base;
    return odds.edges.map((e) => {
      const a = Math.max(0, Math.min(100, e.delta > 0 ? g : g + e.delta));
      const b = Math.max(0, Math.min(100, e.delta > 0 ? g + e.delta : g));
      g += e.delta;
      return { ...e, a, b };
    }).filter((s) => s.b > s.a);
  }

  function draw(odds, near) {
    const c = odds.chance;
    const wrap = mk("div", "wheel");
    const disc = mk("div", "wheel-disc");
    const s = svg("svg", { viewBox: "-104 -104 208 208", class: "wheel-svg" });
    s.append(slice(0, 100, 100, "wr"));
    if (near) s.append(slice(c, Math.min(100, c + NEAR), 100, "wy"));
    s.append(slice(0, c, 100, "wg"));
    for (const sp of spans(odds)) {
      s.append(slice(sp.a, sp.b, 100, "wedge " + (sp.delta > 0 ? "up" : "down")));
      const [x, y] = pt((sp.a + sp.b) / 2, 74);
      const t = svg("text", { x, y, class: "wicon", "text-anchor": "middle", "dominant-baseline": "central" });
      t.textContent = sp.icon;
      s.append(t);
    }
    for (let i = 0; i < 20; i++) {
      const [x0, y0] = pt(i * 5, 100), [x1, y1] = pt(i * 5, 88);
      s.append(svg("line", { x1: x0, y1: y0, x2: x1, y2: y1, class: "wtick" }));
    }
    s.append(svg("circle", { cx: 0, cy: 0, r: 100, class: "wrim" }));
    disc.append(s);
    const hub = mk("div", "wheel-hub", c + "%");
    wrap.append(disc, mk("div", "wheel-pointer"), hub);
    return { wrap, disc };
  }

  // What built the odds: the card's base, then each edge, green or red.
  function edgeList(odds) {
    const row = mk("div", "wheel-edges");
    row.append(mk("span", "base", `${odds.base}% to start`));
    for (const e of odds.edges) row.append(mk("span", e.delta > 0 ? "up" : "down", `${e.icon} ${e.label} ${e.delta > 0 ? "+" : "−"}${Math.abs(e.delta)}`));
    if (odds.chance !== odds.base + odds.edges.reduce((n, e) => n + e.delta, 0)) row.append(mk("span", "base", `capped at ${odds.chance}%`));
    return row;
  }

  function show(title, hint) {
    const box = document.querySelector("#mg .mg-box");
    box.replaceChildren(mk("p", "hunt-title", title));
    if (hint) box.append(mk("p", "dim small", hint));
    document.getElementById("mg").hidden = false;
    return box;
  }

  // Before you commit: the wheel for this option, what it wins and loses, Spin or Back.
  function open(o, onSpin) {
    const odds = o.wheel;
    const box = show(o.label, o.hint || "");
    const { wrap, disc } = draw(odds, odds.near);
    box.append(wrap, edgeList(odds));
    const stakes = mk("div", "wheel-stakes");
    stakes.append(mk("span", "win", "Green: " + (o.win.length ? o.win.join(", ") : "you're fine")));
    const lose = o.lose;
    stakes.append(mk("span", "lose", "Red: " + (lose.length ? lose.join(", ") : "nothing lost")));
    if (odds.near) stakes.append(mk("span", "near", "Yellow: " + (o.near_text || "a close call, half the damage")));
    box.append(stakes);
    const row = mk("div", "wheel-buttons");
    const back = mk("button", "big", "Back");
    const spin = mk("button", "big primary", "Spin");
    back.onclick = close;
    spin.onclick = () => { back.disabled = spin.disabled = true; spin.textContent = "Spinning..."; disc.classList.add("wobble"); onSpin(); };
    row.append(back, spin);
    box.append(row);
    cur = { box, disc, label: o.label, row };
    setTimeout(() => spin.focus(), 50);
  }

  function close() {
    cur = null;
    document.getElementById("mg").hidden = true;
  }

  // Land on the server's roll, then say what it means. Resolves once the wheel is closed.
  function spin(entry, quick = false) {
    return new Promise((done) => {
      if (!cur || cur.label !== entry.label) {
        const box = show(entry.label || "A roll", "");
        const odds = { chance: entry.chance, base: entry.base ?? entry.chance, edges: entry.edges || [] };
        const { wrap, disc } = draw(odds, entry.near_zone);
        box.append(wrap, edgeList(odds));
        cur = { box, disc, label: entry.label };
      }
      const { box, disc } = cur;
      if (cur.row) cur.row.remove();
      disc.classList.remove("wobble");
      const turn = 360 * (quick ? 2 : 4) + (360 - (entry.roll - 0.5) * 3.6);
      const ms = quick ? 700 : 1900;
      disc.style.transition = `transform ${ms}ms cubic-bezier(0.12, 0.75, 0.18, 1)`;
      requestAnimationFrame(() => requestAnimationFrame(() => (disc.style.transform = `rotate(${turn}deg)`)));
      setTimeout(() => {
        const tone = entry.ok ? "ok" : entry.near ? "near" : "bad";
        const head = entry.ok ? "MADE IT" : entry.near ? (entry.got ? "GOT IT, BUT CUT" : "CLOSE CALL") : "MISSED";
        const v = mk("div", "mg-verdict wheel-verdict " + tone);
        v.append(mk("b", "", head), mk("span", "", `${entry.chance}% · landed on ${entry.roll}` + (entry.near ? " · half damage" : "")));
        box.append(v);
        if (navigator.vibrate) navigator.vibrate(entry.ok ? 15 : [30, 40, 30]);
        const finish = () => { if (!cur) return; document.removeEventListener("pointerdown", finish); close(); done(); };
        setTimeout(() => document.addEventListener("pointerdown", finish), 150);
        setTimeout(finish, quick ? 600 : 1500);
      }, ms + 80);
    });
  }

  return { open, spin, close, active: () => !!cur };
})();
