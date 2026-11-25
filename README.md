# Hunger Trail

The Hunger Games meets The Oregon Trail: a daily-seeded, text-based survival game at hungertrail.com.

## Run locally

```sh
uv sync
uv run hungertrail          # serves http://127.0.0.1:8090
uv run pytest
```

For auto-reload while developing:

```sh
uv run uvicorn hungertrail.app:app --reload
```

## Configuration

Settings come from environment variables; see `.env.example`. Set a real `HT_SEED_SECRET` on the
host, or anyone can compute future daily seeds from the source.

## Hosting (Cloudflare Tunnel)

The app binds to `127.0.0.1` only. `cloudflared` runs on the same machine and forwards
`hungertrail.com` to it, so no router ports are opened.

```sh
cloudflared tunnel login
cloudflared tunnel create hungertrail
cloudflared tunnel route dns hungertrail hungertrail.com
cloudflared tunnel run --url http://127.0.0.1:8090 hungertrail
```

## Layout

- `src/hungertrail/app.py`: FastAPI routes
- `src/hungertrail/daily.py`: daily seed (HMAC of the date; never sent to the browser)
- `src/hungertrail/db.py`: SQLite (runs and leaderboard)
- `src/hungertrail/templates/`, `static/`: front end
