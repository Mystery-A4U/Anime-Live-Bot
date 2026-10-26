CREATE TABLE IF NOT EXISTS anime (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  synopsis TEXT DEFAULT '',
  poster_url TEXT DEFAULT '',
  type TEXT DEFAULT 'anime-series',
  genre TEXT DEFAULT '',
  release_year INTEGER,
  language TEXT DEFAULT '',
  published INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seasons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  anime_id TEXT NOT NULL,
  season_number INTEGER NOT NULL,
  title TEXT DEFAULT '',
  UNIQUE(anime_id, season_number)
);

CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  anime_id TEXT NOT NULL,
  season_id INTEGER NOT NULL,
  episode_number INTEGER NOT NULL,
  title TEXT DEFAULT '',
  published INTEGER DEFAULT 0,
  UNIQUE(anime_id, season_id, episode_number)
);
