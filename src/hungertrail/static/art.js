// Tiny procedural pixel art. Everything is drawn on a small canvas and scaled up crisp.
const Art = (() => {
  const C = {
    sky0: "#070a07", sky1: "#0f1a10", sky2: "#18301a", star: "#c8f5b4", moon: "#e8f5c8",
    hill: "#0d1a0e", hill2: "#112412", pine: "#0a140b", pine2: "#16301a", ground: "#13200f",
    gold: "#ffb347", gold2: "#ff8a3d", gold3: "#a8501c", hole: "#1a0d05", stone: "#7d8a78",
    stone2: "#5b6657", text: "#2b3229", grass: "#2f5a2a", blood: "#b3261e", fire: "#ffd23d",
  };

  function rng(seed) {
    return () => {
      seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function canvas(w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    return [c, ctx];
  }

  const px = (ctx, x, y, col, w = 1, h = 1) => { ctx.fillStyle = col; ctx.fillRect(x, y, w, h); };

  function grid(ctx, rows, pal, ox, oy) {
    rows.forEach((row, y) => [...row].forEach((ch, x) => { if (pal[ch]) px(ctx, ox + x, oy + y, pal[ch]); }));
  }

  function sky(ctx, w, h, seed) {
    const r = rng(seed);
    for (let y = 0; y < h; y++) px(ctx, 0, y, y < h * 0.35 ? C.sky0 : y < h * 0.7 ? C.sky1 : C.sky2, w, 1);
    for (let i = 0; i < w / 4; i++) px(ctx, Math.floor(r() * w), Math.floor(r() * h * 0.6), C.star);
  }

  function hills(ctx, w, h, base, amp, col, seed) {
    const r = rng(seed);
    let y = base;
    for (let x = 0; x < w; x++) {
      y += Math.round((r() - 0.5) * 2);
      y = Math.max(base - amp, Math.min(base + amp, y));
      px(ctx, x, y, col, 1, h - y);
    }
  }

  function pine(ctx, x, base, height, col) {
    for (let i = 0; i < height; i++) {
      const half = Math.floor((i / height) * height * 0.35) + ((i % 3 === 0) ? 0 : 1);
      px(ctx, x - half, base - height + i, col, half * 2 + 1, 1);
    }
    px(ctx, x, base, C.pine, 1, 2);
  }

  function horn(ctx, ox, oy, len) {
    // A golden cone that widens to the right, with a curled tail on the left.
    for (let x = 0; x < len; x++) {
      const r = 1 + Math.floor(x * 0.33);
      const cy = oy - Math.round(Math.sin((x / len) * Math.PI * 0.6) * 3);
      for (let y = -r; y <= r; y++) {
        const col = y < -r / 3 ? C.gold : y > r / 2 ? C.gold3 : C.gold2;
        px(ctx, ox + x, cy + y, col);
      }
    }
    const end = ox + len - 1, r = 1 + Math.floor((len - 1) * 0.33);
    const cy = oy - Math.round(Math.sin(0.6 * Math.PI) * 3);
    for (let y = -r + 1; y < r; y++) px(ctx, end, cy + y, C.hole);
    px(ctx, end - 1, cy - 1, C.hole, 1, 3);
    [[-1, 0], [-2, -1], [-2, -2], [-1, -3], [0, -3]].forEach(([dx, dy]) => px(ctx, ox + dx, oy + dy, C.gold2));
    // spilled supplies
    px(ctx, end + 2, oy + 3, "#6f8f62", 2, 1); px(ctx, end + 5, oy + 4, "#8a6a3a", 2, 2); px(ctx, end + 1, oy + 5, "#c8f5b4");
  }

  function title(seed = 74) {
    const [c, ctx] = canvas(120, 52);
    sky(ctx, 120, 52, seed);
    for (let y = -4; y <= 4; y++) for (let x = -4; x <= 4; x++) if (x * x + y * y <= 16) px(ctx, 100 + x, 9 + y, C.moon);
    px(ctx, 101, 8, "#cfe0b0"); px(ctx, 98, 10, "#cfe0b0");
    hills(ctx, 120, 52, 30, 4, C.hill, seed + 1);
    hills(ctx, 120, 52, 38, 2, C.hill2, seed + 2);
    const r = rng(seed + 3);
    for (let x = 2; x < 120; x += 5 + Math.floor(r() * 4)) {
      if (x > 38 && x < 84) continue;
      pine(ctx, x, 44 + Math.floor(r() * 3), 10 + Math.floor(r() * 10), r() > 0.5 ? C.pine : C.pine2);
    }
    px(ctx, 0, 45, C.ground, 120, 7);
    horn(ctx, 46, 41, 26);
    // tributes on their plates, in a ring
    [[40, 46], [48, 48], [58, 49], [68, 49], [78, 48], [86, 46]].forEach(([x, y]) => {
      px(ctx, x - 1, y + 1, "#3c4a38", 3, 1); px(ctx, x, y - 2, "#c8f5b4", 1, 3);
    });
    return c;
  }

  const TOMB = [
    "....ggggggg....",
    "..ggggggggggg..",
    ".ggggggggggggg.",
    ".ggggggggggggg.",
    "gggdddgdgdddggg",
    "gggdgdgdgdgdggg",
    "gggddggdgdddggg",
    "gggdgdgdgdggggg",
    "gggdgdgdgdggggg",
    "gggggggggggggggs",
    "gggggggggggggggs",
    "gggggggggggggggs",
    "gggggggggggggggs",
    "gggggggggggggggs",
  ];

  function tomb(seed = 1) {
    const [c, ctx] = canvas(80, 40);
    sky(ctx, 80, 40, seed);
    hills(ctx, 80, 40, 26, 2, C.hill, seed + 5);
    const r = rng(seed + 9);
    for (let x = 3; x < 80; x += 9 + Math.floor(r() * 6)) if (x < 25 || x > 56) pine(ctx, x, 31, 8 + Math.floor(r() * 6), C.pine2);
    px(ctx, 0, 32, C.ground, 80, 8);
    grid(ctx, TOMB, { g: C.stone, d: C.text, s: C.stone2 }, 32, 19);
    for (let x = 28; x < 52; x += 2) px(ctx, x, 32 - (x % 4 === 0 ? 1 : 0), C.grass);
    px(ctx, 50, 30, C.blood, 2, 2); px(ctx, 51, 29, C.grass);
    return c;
  }

  const CROWN = [
    "y....y....y",
    "yy..yyy..yy",
    "yyy.yyy.yyy",
    "yyyyyyyyyyy",
    "yyryyyyyryy",
    "yyyyyyyyyyy",
    "ooooooooooo",
  ];

  function victory(seed = 2) {
    const [c, ctx] = canvas(80, 40);
    sky(ctx, 80, 40, seed);
    hills(ctx, 80, 40, 28, 2, C.hill, seed + 5);
    px(ctx, 0, 33, C.ground, 80, 7);
    horn(ctx, 26, 30, 22);
    grid(ctx, CROWN, { y: C.fire, o: C.gold2, r: C.blood }, 35, 6);
    const r = rng(seed + 3);
    for (let i = 0; i < 18; i++) px(ctx, Math.floor(r() * 80), Math.floor(r() * 24), r() > 0.5 ? C.fire : C.gold);
    return c;
  }

  const SCENES = { title, tomb, victory };

  function render(el, name, seed) {
    el.replaceChildren(SCENES[name](seed));
  }

  return { render };
})();
