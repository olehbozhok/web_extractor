-- 001: initial schema (docs/02_architecture.md, section 6).

CREATE TABLE cache_pages (
  url_key        TEXT PRIMARY KEY,
  final_url      TEXT NOT NULL,
  status         TEXT NOT NULL,
  http_status    INTEGER,
  fetcher        TEXT NOT NULL,
  fetched_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL,
  content_sha256 TEXT,
  html           BLOB,
  text           TEXT,
  metadata_json  TEXT NOT NULL,
  error_kind     TEXT,
  error_message  TEXT
);

CREATE TABLE cursors (
  id             TEXT PRIMARY KEY,
  url_key        TEXT NOT NULL,
  text_sha256    TEXT NOT NULL,
  offset         INTEGER NOT NULL,
  max_chars      INTEGER NOT NULL,
  format         TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL,
  UNIQUE (url_key, text_sha256, offset, max_chars, format)
);

CREATE TABLE events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts             TEXT NOT NULL,
  agent          TEXT NOT NULL,
  task           TEXT,
  mcp_session_id TEXT,
  request_id     TEXT NOT NULL,
  tool           TEXT NOT NULL,
  url            TEXT NOT NULL,
  final_url      TEXT,
  cache_hit      INTEGER NOT NULL,
  fresh          INTEGER NOT NULL,
  fetcher        TEXT,
  status         TEXT NOT NULL,
  http_status    INTEGER,
  duration_ms    INTEGER NOT NULL,
  queue_ms       INTEGER NOT NULL,
  bytes          INTEGER,
  content_sha256 TEXT,
  error_kind     TEXT
);

CREATE INDEX events_ts ON events(ts);
CREATE INDEX events_agent_ts ON events(agent, ts);
CREATE INDEX events_status_ts ON events(status, ts);
