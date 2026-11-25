# Hunger Trail

The Hunger Games meets The Oregon Trail: a daily, five-minute, text survival game at
[hungertrail.com](https://hungertrail.com).

24 tributes. One arena, the same for everyone each day. Pick a district and a skill, name your
friends as allies, and try to be the last one standing. Then share your run:

```
HUNGER TRAIL #1 · District 12
Placed 4th of 24 · 3 kills · Day 6
🩸🌲💧🎁🔥☠️
Killed by: tracker jackers
hungertrail.com
```

## Run locally

```sh
uv sync
uv run uvicorn hungertrail.app:app --reload   # http://127.0.0.1:8000
uv run pytest
uv run python -m hungertrail.game.bots        # balance report
```

## Configuration

Settings come from environment variables; see `.env.example`. Set a real `HT_SEED_SECRET`
anywhere public, or anyone can compute the daily arenas from the source.

## Layout

- `src/hungertrail/game/`: rules engine, event cards (`cards.yaml`), names (`lore.py`), balance bots
- `src/hungertrail/runs.py`: signed run tokens, replayed on the server
- `src/hungertrail/app.py`: FastAPI routes
- `src/hungertrail/static/`, `templates/`: the phone-first web page
- `ops/keepalive/`, `ops/leaderboard/`: Cloudflare Workers (keep Render awake; leaderboard on D1)
- `docs/PLAN.md`: design and roadmap

## Hosting

Render free tier (`render.yaml`), with Cloudflare DNS. See `CLAUDE.md` for the details.
