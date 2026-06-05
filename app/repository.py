from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from app.db import has_fts
from app.models import BAG_STATUSES, BagRecord, TopicRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_tags(raw: str | Iterable[str]) -> list[str]:
    if isinstance(raw, str):
        items = raw.split(",")
    else:
        items = raw
    seen: set[str] = set()
    tags: list[str] = []
    for item in items:
        tag = str(item).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def tags_to_text(tags: Iterable[str]) -> str:
    return json.dumps(list(tags), ensure_ascii=False)


def tags_from_text(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item).strip()]


def upsert_bag(conn: sqlite3.Connection, bag: BagRecord) -> int:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO bags (
          path, name, storage_identifier, starting_time, duration_ns,
          message_count, size_bytes, status, error_message, indexed_at, modified_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          name = excluded.name,
          storage_identifier = excluded.storage_identifier,
          starting_time = excluded.starting_time,
          duration_ns = excluded.duration_ns,
          message_count = excluded.message_count,
          size_bytes = excluded.size_bytes,
          status = excluded.status,
          error_message = excluded.error_message,
          indexed_at = excluded.indexed_at
        """,
        (
            bag.path,
            bag.name,
            bag.storage_identifier,
            bag.starting_time,
            bag.duration_ns,
            bag.message_count,
            bag.size_bytes,
            bag.status,
            bag.error_message,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM bags WHERE path = ?", (bag.path,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to upsert bag: {bag.path}")
    bag_id = int(row["id"])
    conn.execute("DELETE FROM topics WHERE bag_id = ?", (bag_id,))
    conn.executemany(
        """
        INSERT INTO topics (bag_id, name, type, serialization_format, message_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                bag_id,
                topic.name,
                topic.type,
                topic.serialization_format,
                topic.message_count,
            )
            for topic in bag.topics
        ],
    )
    refresh_search_row(conn, bag_id)
    return bag_id


def refresh_search_row(conn: sqlite3.Connection, bag_id: int) -> None:
    if not has_fts(conn):
        return
    bag = conn.execute("SELECT * FROM bags WHERE id = ?", (bag_id,)).fetchone()
    if bag is None:
        return
    topics = conn.execute(
        "SELECT name, type FROM topics WHERE bag_id = ? ORDER BY name", (bag_id,)
    ).fetchall()
    tags = " ".join(tags_from_text(bag["tags"]))
    topic_names = " ".join(str(row["name"] or "") for row in topics)
    topic_types = " ".join(str(row["type"] or "") for row in topics)
    conn.execute("DELETE FROM bag_search WHERE rowid = ?", (bag_id,))
    conn.execute(
        """
        INSERT INTO bag_search (rowid, name, path, note, tags, topic_names, topic_types)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bag_id,
            bag["name"],
            bag["path"],
            bag["note"],
            tags,
            topic_names,
            topic_types,
        ),
    )


def search_bags(
    conn: sqlite3.Connection,
    *,
    topic: str | None = None,
    message_type: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    joins: list[str] = []

    if topic:
        joins.append("JOIN topics topic_filter ON topic_filter.bag_id = bags.id")
        clauses.append("topic_filter.name LIKE ?")
        params.append(f"%{topic}%")
    if message_type:
        joins.append("JOIN topics type_filter ON type_filter.bag_id = bags.id")
        clauses.append("type_filter.type LIKE ?")
        params.append(f"%{message_type}%")
    if status and status in BAG_STATUSES:
        clauses.append("bags.status = ?")
        params.append(status)
    if tag:
        clauses.append("bags.tags LIKE ?")
        params.append(f"%{tag}%")
    if q:
        if has_fts(conn):
            joins.append("JOIN bag_search ON bag_search.rowid = bags.id")
            clauses.append("bag_search MATCH ?")
            params.append(_fts_query(q))
        else:
            like = f"%{q}%"
            clauses.append(
                "(bags.name LIKE ? OR bags.path LIKE ? OR bags.note LIKE ? OR bags.tags LIKE ?)"
            )
            params.extend([like, like, like, like])

    sql = [
        "SELECT DISTINCT bags.* FROM bags",
        *joins,
    ]
    if clauses:
        sql.append("WHERE " + " AND ".join(clauses))
    sql.append("ORDER BY bags.starting_time DESC, bags.name ASC")
    rows = conn.execute(" ".join(sql), params).fetchall()
    return [_bag_row_to_dict(row) for row in rows]


def _fts_query(q: str) -> str:
    tokens = [token.replace('"', " ").strip() for token in q.split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        return '""'
    return " ".join(f'"{token}"' for token in tokens)


def get_bag(conn: sqlite3.Connection, bag_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM bags WHERE id = ?", (bag_id,)).fetchone()
    if row is None:
        return None
    return _bag_row_to_dict(row)


def get_topics(conn: sqlite3.Connection, bag_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, type, serialization_format, message_count
        FROM topics
        WHERE bag_id = ?
        ORDER BY name, type
        """,
        (bag_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_note(conn: sqlite3.Connection, bag_id: int, note: str) -> None:
    conn.execute(
        "UPDATE bags SET note = ?, modified_at = ? WHERE id = ?",
        (note, utc_now(), bag_id),
    )
    refresh_search_row(conn, bag_id)


def update_tags(conn: sqlite3.Connection, bag_id: int, raw_tags: str) -> None:
    tags = tags_to_text(normalize_tags(raw_tags))
    conn.execute(
        "UPDATE bags SET tags = ?, modified_at = ? WHERE id = ?",
        (tags, utc_now(), bag_id),
    )
    refresh_search_row(conn, bag_id)


def _bag_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["tag_list"] = tags_from_text(item.get("tags"))
    item["tags_csv"] = ", ".join(item["tag_list"])
    item["duration_text"] = format_duration(item.get("duration_ns"))
    item["size_text"] = format_bytes(int(item.get("size_bytes") or 0))
    return item


def format_duration(duration_ns: int | None) -> str:
    if duration_ns is None:
        return ""
    seconds = duration_ns / 1_000_000_000
    if seconds < 1:
        return f"{seconds:.3f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rem = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, rem_minutes = divmod(minutes, 60)
    return f"{hours}h {rem_minutes}m"


def format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
