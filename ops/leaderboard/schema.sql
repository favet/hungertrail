CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scores (
  date TEXT NOT NULL, nickname TEXT NOT NULL, nick_key TEXT NOT NULL,
  run_id TEXT NOT NULL UNIQUE, score INTEGER NOT NULL, placement INTEGER NOT NULL,
  kills INTEGER NOT NULL, day INTEGER NOT NULL, district INTEGER NOT NULL,
  cause TEXT, emoji TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (date, nick_key)
);
CREATE INDEX IF NOT EXISTS scores_by_day ON scores (date, score DESC);
