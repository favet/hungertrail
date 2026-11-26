# Hunger Trail: design answers and build plan

A daily, 5–10 minute, text-first survival run. The Hunger Games' premise (24 enter, one leaves),
played with Oregon Trail's on-rails, deadpan, resource-juggling feel. Everyone gets the same arena
each day; the share card is the payoff.

## Status (2026-11-25): MVP live

Phases 0-7 shipped as an MVP at hungertrail.com. The human fun check (Phase 1) was deferred to
after launch at favet's request; bots and phone-browser walkthroughs stood in for it.

- Done: engine, 50 cards (3 bloodbaths with 3 follow-ups, 35 events, 5 Gamemaker hazards, feast,
  betrayal, 2 finale cards),
  balance bots, phone-first web game, hunting minigame, results card and share text, daily seed
  with one ranked run, practice mode, leaderboard on Cloudflare D1, pixel art, cannon sound.
- Design calls made without a human playtest (review these first): exact odds shown as
  percentages; rations are automatic (1 food + 1 water a night) rather than a setting; allies are
  named tributes who can betray you and become your final opponent; a good player wins about 1
  run in 6.
- Deferred: share image (Open Graph card), tombstones with other players' epitaphs, analytics,
  more arena types, sound beyond the cannon.

## Beta 1 (2026-11-26): beta.hungertrail.com

After the first playtests (runs felt the same, odds were confusing, favor and heat went unnoticed),
beta 1 rebuilds the run around a small screen and visible stakes. The full critique and the
reasoning are in the conversation of 2026-11-25; the decisions:

- Three stats on screen only: health, supplies (food and water merged, in days), a 3-slot kit.
- Bloodbath draft: seeded loot around the Cornucopia; the closer, the better and deadlier. Two grabs.
- Items have lasting effects. A full kit means choosing what to drop.
- Every option shows its odds, what you win and what you lose; ☠️ marks options that could kill.
- Near misses: a fail within 10 points halves the damage.
- Favor and heat are merged into a hidden crowd. Bold moves (🎥) bring sponsor gifts at once;
  hiding draws Caesar's warning at night, then the Gamemakers.
- Districts give a perk instead of a score multiplier (bots showed the perks balance).
- The final duel lists your edges (weapon, health, kills, rope, supplies, crowd); they set the odds.
- Kills take the tribute the card names. The log folds finished days to one line.

Beta 2 (next, if beta 1 tests well): the daily move (inner/outer loop, camp merged in), arena
themes as rule changes, a daily nemesis. Beta 3: marks across days, titles, the share grid.

## Design answers

### 1. What is one run?

A fixed sequence of about **15–20 decision cards**, 5–10 minutes total:

1. **Reaping (30 seconds).** Pick a district (difficulty and score multiplier) and one trait.
   Name your district partner and up to two allies. Names are cosmetic but show up in the story
   ("Jake was stung to death by tracker jackers").
2. **Bloodbath (2–3 cards).** How close to the Cornucopia you go: the bigger the risk, the better the loot.
3. **Arena days (about 8 days, 1–2 cards each).** Each day: a camp step (ration food and water,
   rest or move on), then that day's event card.
4. **Finale.** When the tribute count reaches the last few, the Gamemakers force a final event.

There is no free movement and no map to explore. Other tributes are not simulated.

### 2. What does the player manage?

Six visible values, always on screen:

| Stat | Role |
|---|---|
| Health | Good / Fair / Poor / Very poor (Oregon Trail style); death at zero |
| Food, Water | Drain daily; rationing trades health for supply |
| Gear | A few item slots (bow, knife, rope, ointment...); items unlock card options |
| Favor | Sponsor goodwill; earned by drama, spent automatically as gifts at night |
| Heat | Gamemaker attention; rises when you're boring or hiding, triggers hazards |

Hiding is always available and always safe in the short term, but it costs Favor and raises Heat.
That tension is the core of the game.

### 3. Where do skill and luck come from?

- **Skill:** every option shows its cost, and risky options show telegraphed odds ("the berries
  look like nightlock", "the Career pack is close"). Gear and stats carry forward, so earlier
  choices open or close later options. Skill builds up across days because the rules never change.
- **Luck:** the day's seed picks which cards appear and in what order. That is identical for everyone.
- **Close calls** (fights, escapes) roll dice with the odds shown, salted per run. Two players
  making the same choices face the same odds, not the same outcome. This stops a "solved path"
  spreading after the first players post their runs.

### 4. How do other tributes exist without simulating them?

As a **seeded death schedule** plus cards that reference them. The schedule says roughly how many
tributes fall each day (11 on Day 1, then 0–2 a day, spikes at Gamemaker events). Your kills come
out of that pool. Cannons fire on each death; each night the sky shows the faces of the fallen.
That's all the player could ever perceive anyway.

### 5. What keeps it Oregon Trail?

- District works like profession: Careers ×1, middle districts ×2, District 11 and 12 ×3.
- Status bar in the Oregon Trail style: day, weather, health, food, water, tributes remaining.
- Rations and pace as the daily camp step.
- Classic cards: river fording (ford / float / wait), hunting (short timing minigame with capped
  yield, and the noise attracts trouble), illness and injury, landmarks.
- Deadpan one-line deaths for you and your named companions.
- Later: tombstones with other players' epitaphs at the spot they died.

### 6. Score and share card

- **Placement** is primary (1st of 24 is the win), then kills, then days survived, multiplied by
  the district multiplier.
- The share text is one emoji per day, the cause of death, and no arena spoilers:

```
HUNGER TRAIL #3 · District 12
Placed 4th of 24 · 3 kills · Day 6
🩸🌲💧🎁🔥☠️
Killed by: tracker jackers
hungertrail.com
```

- A results screen shows the same information large enough to screenshot, plus an image version later.

### 7. Cheating and spoilers

- The game runs on the server. The browser only sends "I picked option 2"; the server decides
  what happens. Seeds never reach the browser.
- One ranked attempt per day per browser and nickname. This is casual protection, which is fine
  for a nickname leaderboard. Practice mode uses random seeds and isn't ranked.
- Per-run salt on close-call rolls (above) blunts copying.

### 8. Tech shape

- **Engine:** a pure Python module with no I/O. `start(seed, salt, setup) -> state` and
  `choose(state, option) -> state`. Deterministic, so a run can be replayed from its choices.
- **Content as data:** cards, items and death lines live in plain data files (YAML or JSON), not
  in code. Adding a card means adding data, not code.
- **API:** FastAPI. Run state is stored server-side and keyed by a run id cookie.
- **Front end:** one HTML page with vanilla JS. No build step, number keys pick options.
- **Storage:** SQLite locally. Render's free disk is wiped on deploy, so the leaderboard phase
  either picks a free hosted database (Turso is SQLite-compatible) or moves hosting back to the
  home PC. Access goes through one small module so that switch is cheap.

## Build plan

Each phase ends with something playable or testable. Don't start a phase before the previous one's
"done when" is met.

### Phase 0: housekeeping
- Merge `initial-site` into `main`; point Render at `main`.
- Add a `CLAUDE.md` with run, test and deploy commands.
- **Done when:** pushing to `main` deploys, and tests run with one command.

### Phase 1: paper prototype (no code)
- Write one full run as text: reaping, bloodbath, 3–4 days, finale. Show the stats and the odds.
- Play it in the thread with favet and Jake. Iterate on the cards and stat model until it's fun.
- **Done when:** both players want to play it again with a different seed.

### Phase 2: engine core
- State model, card format, option conditions (needs item X, Health above Y), effects, seeded RNG.
- The day loop: camp step, event card, night (cannons, sky, sponsor gift).
- The death schedule and finale trigger.
- Unit tests: determinism (same seed, salt and choices give the same run), no dead ends, every
  run terminates.
- **Done when:** a full run can be played in the terminal (`uv run hungertrail-cli`).

### Phase 3: content, first pass
- About 40 event cards, 3 bloodbath variants, 10 items, 30 death lines, 5 landmark cards.
- River fording and the hunting card (hunting resolves as a simple choice for now; the minigame
  comes in Phase 5).
- **Done when:** 10 different seeds feel different from each other.

### Phase 4: balance harness
- Bots with fixed strategies: hider, aggressor, hoarder, random, greedy-expected-value.
- Run thousands of headless games per strategy and report placement distribution, average decisions
  per run, causes of death, and win rate.
- Targets: 15–20 decisions per run; good play wins about 5–10% of the time; no single strategy
  dominates; the hider never wins by doing nothing.
- **Done when:** the targets hold across 30 seeds.

### Phase 5: web game
- Terminal-style page: status bar, story text, numbered options. Keyboard and tap both work.
- API: start run, choose option, get current state. Run id in a cookie; refreshing the page resumes.
- Results screen with the share text and a copy button.
- Hunting minigame (a short timing bar, yield capped on the server).
- **Done when:** a full run works on phone and desktop at hungertrail.com.

### Phase 6: daily and modes
- Daily seed (already built), one ranked attempt per day, practice mode with random seeds.
- Puzzle number and countdown to the next arena.
- **Done when:** a second ranked attempt on the same day is refused and practice still works.

### Phase 7: leaderboard and persistence
- Choose storage (hosted database or home PC). Nickname entry, daily board, all-time stats.
- **Done when:** scores survive a redeploy.

### Phase 8: polish
- Simple pixel art: portraits for the reaping, small scene art for landmarks.
- A share image (Open Graph card), sounds (the cannon), and deadpan death screens.
- Tombstones: show an epitaph from another player at the spot they died (moderated).
- **Done when:** the results screen is something people want to screenshot.

### Phase 9: launch
- Final hosting decision, an error page, basic analytics (runs started and finished, where people quit).
- Name review: direct Hunger Games names are in one lore file, so swapping them stays cheap.

## Guardrails against scope creep

- No free movement, no open map, no simulated tributes. If an idea needs one of those, it waits
  until after launch.
- New mechanics must show up as a visible stat or a card option. If the player can't see it,
  don't build it.
- Every phase ends playable. If a phase stalls, cut content, not the "done when".
