"use strict";
// The browser copy of the beta minigame simulations in beta/minigames.py. Integer math only, so
// the server replays exactly what the player saw. Keep in step with the Python (tests compare them).

const Sim = (() => {
  const TPS = 60, SPAN = 10000;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const div = (a, b) => Math.floor(a / b);  // Python's floor division, for the small numbers here

  function pathAt(path, t) {
    if (t <= path[0][0]) return path[0][1];
    for (let i = 1; i < path.length; i++) {
      const [t0, p0] = path[i - 1], [t1, p1] = path[i];
      if (t <= t1) return p0 + div((p1 - p0) * (t - t0), t1 - t0);
    }
    return path[path.length - 1][1];
  }

  function tri(x) {
    const d = x % (2 * SPAN);
    return d <= SPAN ? d : 2 * SPAN - d;
  }

  // Fishing: one tick of the bar. Returns the new state; c is the catch meter (0-1000).
  function fishInit(p) { return { y: p.y0 || 0, v: 0, c: p.start, t: 0, done: null }; }
  function fishStep(p, s, hold) {
    s.v = clamp(s.v + (hold ? p.up : -p.down), -p.vmax, p.vmax);
    s.y += s.v;
    if (s.y < 0) { s.y = 0; s.v = div(-s.v, 2); }
    const top = SPAN - p.bar;
    if (s.y > top) { s.y = top; s.v = 0; }
    const f = pathAt(p.path, s.t);
    s.on = s.y <= f && f <= s.y + p.bar;
    s.c += s.on ? p.gain : s.t >= (p.grace || 0) ? -p.loss : 0;
    s.t += 1;
    if (s.c >= 1000) s.done = "caught";
    else if (s.c <= 0 || s.t >= p.duration) s.done = "escaped";
    return s;
  }
  function fishRun(p, toggles) {
    const s = fishInit(p);
    let hold = false, i = 0;
    while (!s.done) {
      while (i < toggles.length && toggles[i] <= s.t) { hold = !hold; i++; }
      fishStep(p, s, hold);
    }
    return [s.done === "caught", s.t];
  }

  // Hunting: what a tap at tick t on shot n hits, given whether the deer is still around.
  function huntHit(shot, t, deer) {
    if (!(t >= 0 && t < shot.duration)) return null;
    const m = tri(shot.speed * t);
    let best = null, bd = 0;
    for (const a of shot.animals) {
      if (a.kind === "deer" && !deer) continue;
      const d = Math.abs(m - pathAt(a.path, t));
      if (d <= a.half && (best === null || d < bd)) { best = a; bd = d; }
    }
    return best;
  }
  function huntRun(p, taps) {
    let deer = true, food = 0;
    const hits = [];
    p.shots.forEach((s, n) => {
      const a = huntHit(s, n < taps.length ? taps[n] : -1, deer);
      if (a) { food += a.food; hits.push(a.kind); if (a.kind === "deer") deer = false; }
      else { hits.push(null); deer = false; }
    });
    return [food, hits];
  }

  // Hold still: one tick. Returns null while you're still hidden, else how it ended.
  function hideInit(p) { return { b: p.breath, t: 0, done: null }; }
  function hideStep(p, s, hold) {
    if (hold) {
      s.b -= 1;
      if (s.b <= 0) { s.done = "gasp"; return s; }
    } else s.b = Math.min(p.breath, s.b + p.refill);
    s.cone = pathAt(p.path, s.t);
    s.lit = Math.abs(s.cone - p.me) <= p.half;
    if (!hold && s.lit) { s.done = "spotted"; return s; }
    s.t += 1;
    if (s.t >= p.duration) s.done = "hidden";
    return s;
  }
  function hideRun(p, toggles) {
    const s = hideInit(p);
    let hold = false, i = 0;
    while (!s.done) {
      while (i < toggles.length && toggles[i] <= s.t) { hold = !hold; i++; }
      hideStep(p, s, hold);
    }
    return [s.done, s.t];
  }

  // Read and react: [block tick, block side, strike tick] -> [blocked, struck].
  function fightRun(p, inp) {
    const [bt, bs, st] = inp;
    const blocked = p.block[0] <= bt && bt <= p.block[1] && bs === p.side;
    const struck = blocked && p.open[0] <= st && st < p.open[1];
    return [blocked, struck];
  }

  return { TPS, SPAN, pathAt, tri, fishInit, fishStep, fishRun, huntHit, huntRun, hideInit, hideStep, hideRun, fightRun };
})();

if (typeof module !== "undefined") module.exports = Sim;
