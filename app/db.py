from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bags (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          storage_identifier TEXT,
          starting_time TEXT,
          duration_ns INTEGER,
          message_count INTEGER,
          size_bytes INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'broken',
          error_message TEXT,
          note TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '[]',
          indexed_at TEXT NOT NULL,
          modified_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS topics (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bag_id INTEGER NOT NULL REFERENCES bags(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          type TEXT,
          serialization_format TEXT,
          message_count INTEGER,
          UNIQUE (bag_id, name, type)
        );

        CREATE INDEX IF NOT EXISTS idx_bags_status ON bags(status);
        CREATE INDEX IF NOT EXISTS idx_topics_name ON topics(name);
        CREATE INDEX IF NOT EXISTS idx_topics_type ON topics(type);
        CREATE INDEX IF NOT EXISTS idx_topics_bag_id ON topics(bag_id);
        """
    )
    conn.commit()
