from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import BagRecord, TopicRecord


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
    existing = _find_existing_bag_row(conn, bag)
    if existing is None:
        conn.execute(
            """
            INSERT INTO bags (
              path, root_relative_path, name, storage_identifier, starting_time,
              duration_ns, message_count, size_bytes, status, error_message,
              index_signature, indexed_at, modified_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bag.path,
                bag.root_relative_path,
                bag.name,
                bag.storage_identifier,
                bag.starting_time,
                bag.duration_ns,
                bag.message_count,
                bag.size_bytes,
                bag.status,
                bag.error_message,
                bag.index_signature,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT id FROM bags WHERE path = ?", (bag.path,)).fetchone()
    else:
        conn.execute(
            """
            UPDATE bags SET
              path = ?,
              root_relative_path = ?,
              name = ?,
              storage_identifier = ?,
              starting_time = ?,
              duration_ns = ?,
              message_count = ?,
              size_bytes = ?,
              status = ?,
              error_message = ?,
              index_signature = ?,
              indexed_at = ?
            WHERE id = ?
            """,
            (
                bag.path,
                bag.root_relative_path,
                bag.name,
                bag.storage_identifier,
                bag.starting_time,
                bag.duration_ns,
                bag.message_count,
                bag.size_bytes,
                bag.status,
                bag.error_message,
                bag.index_signature,
                now,
                existing["id"],
            ),
        )
        row = conn.execute("SELECT id FROM bags WHERE id = ?", (existing["id"],)).fetchone()
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
    return bag_id


def find_indexed_bag(
    conn: sqlite3.Connection,
    *,
    path: str,
    root_relative_path: str | None,
) -> sqlite3.Row | None:
    if root_relative_path:
        row = conn.execute(
            """
            SELECT id, path, root_relative_path, status, index_signature
            FROM bags
            WHERE root_relative_path = ?
            """,
            (root_relative_path,),
        ).fetchone()
        if row is not None:
            return row
    return conn.execute(
        """
        SELECT id, path, root_relative_path, status, index_signature
        FROM bags
        WHERE path = ?
        """,
        (path,),
    ).fetchone()


def update_bag_location(
    conn: sqlite3.Connection,
    bag_id: int,
    *,
    path: str,
    root_relative_path: str | None,
) -> None:
    conn.execute(
        "UPDATE bags SET path = ?, root_relative_path = ? WHERE id = ?",
        (path, root_relative_path, bag_id),
    )


def _find_existing_bag_row(
    conn: sqlite3.Connection, bag: BagRecord
) -> sqlite3.Row | None:
    if bag.root_relative_path:
        row = conn.execute(
            "SELECT id FROM bags WHERE root_relative_path = ?",
            (bag.root_relative_path,),
        ).fetchone()
        if row is not None:
            return row
    return conn.execute("SELECT id FROM bags WHERE path = ?", (bag.path,)).fetchone()


def root_relative_path(path_value: str | Path, bag_root: Path) -> str | None:
    path = Path(path_value).resolve()
    try:
        return path.relative_to(bag_root.resolve()).as_posix()
    except ValueError:
        return None


def bag_belongs_to_root(row_or_item: sqlite3.Row | dict[str, Any], bag_root: Path) -> bool:
    if row_or_item["root_relative_path"]:
        return True
    return root_relative_path(row_or_item["path"], bag_root) is not None


def bag_record_for_root(bag: BagRecord, bag_root: Path) -> BagRecord:
    return BagRecord(
        path=bag.path,
        root_relative_path=root_relative_path(bag.path, bag_root),
        name=bag.name,
        storage_identifier=bag.storage_identifier,
        starting_time=bag.starting_time,
        duration_ns=bag.duration_ns,
        message_count=bag.message_count,
        size_bytes=bag.size_bytes,
        status=bag.status,
        error_message=bag.error_message,
        index_signature=bag.index_signature,
        topics=bag.topics,
    )


def delete_stale_bag_indexes(
    conn: sqlite3.Connection,
    bag_root: Path,
    current_paths: set[str],
    *,
    prune_by_relative_paths: bool = False,
) -> int:
    root_path = bag_root.resolve()
    current_relative_paths = {
        relative for path in current_paths if (relative := root_relative_path(path, root_path))
    }
    rows = conn.execute("SELECT id, path, root_relative_path FROM bags").fetchall()
    deleted = 0
    for row in rows:
        row_relative_path = row["root_relative_path"]
        if row_relative_path:
            if row_relative_path in current_relative_paths:
                continue
            if not prune_by_relative_paths and root_relative_path(
                row["path"], root_path
            ) is None:
                continue
            conn.execute("DELETE FROM bags WHERE id = ?", (row["id"],))
            deleted += 1
            continue

        path_value = row["path"]
        if not path_value:
            continue
        bag_path = Path(path_value).resolve()
        try:
            bag_path.relative_to(root_path)
        except ValueError:
            continue
        if str(bag_path) in current_paths:
            continue
        conn.execute("DELETE FROM bags WHERE id = ?", (row["id"],))
        deleted += 1
    return deleted


def search_bags(
    conn: sqlite3.Connection,
    *,
    topic: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    start_from: str | None = None,
    start_to: str | None = None,
    bag_root: Path | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    topic_clause, topic_params = search_pattern_clause(topic, topic_predicate)
    if topic_clause:
        clauses.append(topic_clause)
        params.extend(topic_params)
    if tag:
        clauses.append("bags.tags LIKE ?")
        params.append(f"%{tag}%")
    keyword_clause, keyword_params = search_pattern_clause(q, keyword_predicate)
    if keyword_clause:
        clauses.append(keyword_clause)
        params.extend(keyword_params)
    if start_from:
        start_from_bound = local_datetime_bound_as_utc_text(
            start_from, upper_bound=False
        )
        if start_from_bound is not None:
            clauses.append("bags.starting_time >= ?")
            params.append(start_from_bound)
    if start_to:
        start_to_bound = local_datetime_bound_as_utc_text(start_to, upper_bound=True)
        if start_to_bound is not None:
            clauses.append("bags.starting_time <= ?")
            params.append(start_to_bound)

    sql = [
        "SELECT DISTINCT bags.* FROM bags",
    ]
    if clauses:
        sql.append("WHERE " + " AND ".join(clauses))
    sql.append("ORDER BY bags.starting_time DESC, bags.name ASC")
    rows = conn.execute(" ".join(sql), params).fetchall()
    if bag_root is not None:
        rows = [row for row in rows if bag_belongs_to_root(row, bag_root)]
    items = [_bag_row_to_dict(row, bag_root=bag_root) for row in rows]
    if tag:
        items = [item for item in items if tag in item["tag_list"]]
    return items


def search_pattern_clause(
    value: str | None,
    predicate_factory: Any,
) -> tuple[str | None, list[Any]]:
    node = parse_search_pattern(value)
    if node is None:
        return None, []
    return search_node_clause(node, predicate_factory)


def parse_search_pattern(value: str | None) -> tuple[str, Any] | None:
    tokens = tokenize_search_pattern(value)
    if not tokens:
        return None
    return SearchPatternParser(tokens).parse()


def tokenize_search_pattern(value: str | None) -> list[str]:
    if not value:
        return []
    tokens: list[str] = []
    current: list[str] = []
    for char in value:
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        if char in "()":
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(char)
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


class SearchPatternParser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0
        self.invalid = False

    def parse(self) -> tuple[str, Any] | None:
        node = self.parse_or()
        if self.invalid or self.peek() is not None:
            return None
        return node

    def parse_or(self) -> tuple[str, Any] | None:
        nodes: list[tuple[str, Any]] = []
        node = self.parse_and()
        if node is not None:
            nodes.append(node)
        while self.peek_upper() == "OR":
            self.index += 1
            node = self.parse_and()
            if node is None:
                self.invalid = True
                return None
            nodes.append(node)
        return combine_nodes("or", nodes)

    def parse_and(self) -> tuple[str, Any] | None:
        nodes: list[tuple[str, Any]] = []
        while self.peek() is not None and self.peek() != ")" and self.peek_upper() != "OR":
            if self.peek_upper() == "AND":
                self.index += 1
                continue
            node = self.parse_not()
            if node is not None:
                nodes.append(node)
        return combine_nodes("and", nodes)

    def parse_not(self) -> tuple[str, Any] | None:
        if self.peek_upper() == "NOT":
            self.index += 1
            node = self.parse_not()
            if node is None:
                return None
            return "not", node
        return self.parse_primary()

    def parse_primary(self) -> tuple[str, Any] | None:
        token = self.peek()
        if token is None:
            return None
        if token == "(":
            self.index += 1
            node = self.parse_or()
            if self.peek() == ")":
                self.index += 1
            else:
                self.invalid = True
                return None
            return node
        if token == ")" or token.upper() in {"AND", "OR"}:
            return None
        self.index += 1
        return "term", token

    def peek(self) -> str | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def peek_upper(self) -> str | None:
        token = self.peek()
        if token is None:
            return None
        return token.upper()


def combine_nodes(kind: str, nodes: list[tuple[str, Any]]) -> tuple[str, Any] | None:
    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes[0]
    return kind, nodes


def search_node_clause(
    node: tuple[str, Any],
    predicate_factory: Any,
) -> tuple[str | None, list[Any]]:
    kind, value = node
    if kind == "term":
        return predicate_factory(value)
    if kind == "not":
        clause, params = search_node_clause(value, predicate_factory)
        if clause is None:
            return None, []
        return f"NOT ({clause})", params
    if kind in {"and", "or"}:
        child_clauses: list[str] = []
        params: list[Any] = []
        for child in value:
            child_clause, child_params = search_node_clause(child, predicate_factory)
            if child_clause is None:
                continue
            child_clauses.append(child_clause)
            params.extend(child_params)
        if not child_clauses:
            return None, []
        operator = " AND " if kind == "and" else " OR "
        return "(" + operator.join(child_clauses) + ")", params
    return None, []


def topic_predicate(term: str) -> tuple[str, list[Any]]:
    return (
        "EXISTS (SELECT 1 FROM topics topic_filter "
        "WHERE topic_filter.bag_id = bags.id AND topic_filter.name LIKE ?)",
        [f"%{term}%"],
    )


def keyword_predicate(term: str) -> tuple[str, list[Any]]:
    like = f"%{term}%"
    return "(bags.name LIKE ? OR bags.note LIKE ?)", [like, like]


def get_bag(
    conn: sqlite3.Connection, bag_id: int, *, bag_root: Path | None = None
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM bags WHERE id = ?", (bag_id,)).fetchone()
    if row is None:
        return None
    if bag_root is not None and not bag_belongs_to_root(row, bag_root):
        return None
    return _bag_row_to_dict(row, bag_root=bag_root)


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


def update_tags(
    conn: sqlite3.Connection, bag_id: int, raw_tags: str | Iterable[str]
) -> None:
    tags = tags_to_text(normalize_tags(raw_tags))
    conn.execute(
        "UPDATE bags SET tags = ?, modified_at = ? WHERE id = ?",
        (tags, utc_now(), bag_id),
    )


def add_tag(conn: sqlite3.Connection, bag_id: int, tag: str) -> None:
    bag = get_bag(conn, bag_id)
    if bag is None:
        return
    tags_to_add = normalize_tags([tag])
    if not tags_to_add:
        return
    tag_to_add = tags_to_add[0]
    if tag_to_add in bag["tag_list"]:
        return
    update_tags(conn, bag_id, [*bag["tag_list"], tag_to_add])


def remove_tags(conn: sqlite3.Connection, bag_id: int, tags_to_remove: Iterable[str]) -> None:
    bag = get_bag(conn, bag_id)
    if bag is None:
        return
    removals = set(normalize_tags(tags_to_remove))
    if not removals:
        return
    remaining_tags = [tag for tag in bag["tag_list"] if tag not in removals]
    if remaining_tags == bag["tag_list"]:
        return
    update_tags(conn, bag_id, remaining_tags)


def list_tags(conn: sqlite3.Connection, *, bag_root: Path | None = None) -> list[str]:
    tags: set[str] = set()
    rows = conn.execute("SELECT path, root_relative_path, tags FROM bags ORDER BY name").fetchall()
    for row in rows:
        if bag_root is not None and not bag_belongs_to_root(row, bag_root):
            continue
        tags.update(tags_from_text(row["tags"]))
    return sorted(tags)


def get_last_scanned_at(conn: sqlite3.Connection, *, bag_root: Path | None = None) -> str:
    if bag_root is not None:
        row = conn.execute(
            "SELECT last_scanned_at FROM scan_state WHERE bag_root = ?",
            (str(bag_root.resolve()),),
        ).fetchone()
        if row is not None:
            return format_datetime_text(row["last_scanned_at"])
    if bag_root is None:
        row = conn.execute("SELECT MAX(indexed_at) AS last_scanned_at FROM bags").fetchone()
        if row is None:
            return ""
        return format_datetime_text(row["last_scanned_at"])
    rows = conn.execute(
        "SELECT path, root_relative_path, indexed_at FROM bags"
    ).fetchall()
    values = [
        row["indexed_at"]
        for row in rows
        if row["indexed_at"] and bag_belongs_to_root(row, bag_root)
    ]
    if not values:
        return ""
    return format_datetime_text(max(values))


def update_last_scanned_at(conn: sqlite3.Connection, bag_root: Path) -> None:
    conn.execute(
        """
        INSERT INTO scan_state (bag_root, last_scanned_at)
        VALUES (?, ?)
        ON CONFLICT(bag_root) DO UPDATE SET last_scanned_at = excluded.last_scanned_at
        """,
        (str(bag_root.resolve()), utc_now()),
    )


def _bag_row_to_dict(row: sqlite3.Row, *, bag_root: Path | None = None) -> dict[str, Any]:
    item = dict(row)
    item["status"] = "valid" if item.get("status") == "valid" else "broken"
    item["tag_list"] = tags_from_text(item.get("tags"))
    item["duration_text"] = format_duration(item.get("duration_ns"))
    item["size_text"] = format_bytes(int(item.get("size_bytes") or 0))
    item["starting_time_text"] = format_datetime_text(item.get("starting_time"))
    item["indexed_at_text"] = format_datetime_text(item.get("indexed_at"))
    item["modified_at_text"] = format_datetime_text(item.get("modified_at"))
    item["path_display"] = display_path(item, bag_root)
    return item


def display_path(item: dict[str, Any], bag_root: Path | None) -> str:
    root_relative = item.get("root_relative_path")
    if root_relative:
        return str(root_relative)
    return relative_path(item.get("path"), bag_root)


def relative_path(path_value: str | None, bag_root: Path | None) -> str:
    if not path_value:
        return ""
    if bag_root is None:
        return path_value
    path = Path(path_value)
    try:
        return str(path.relative_to(bag_root))
    except ValueError:
        return path_value


def format_datetime_text(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
            return parsed.astimezone(local_timezone()).strftime("%Y/%m/%d %H:%M:%S")
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(local_timezone()).strftime("%Y/%m/%d %H:%M:%S")
    except ValueError:
        pass
    try:
        numeric = int(text)
    except ValueError:
        return text
    if numeric > 10_000_000_000:
        return datetime.fromtimestamp(numeric / 1_000_000_000, timezone.utc).astimezone(
            local_timezone()
        ).strftime(
            "%Y/%m/%d %H:%M:%S"
        )
    return text


def local_datetime_bound_as_utc_text(value: str, *, upper_bound: bool) -> str | None:
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            local_dt = parsed.replace(tzinfo=local_timezone())
            return local_dt.astimezone(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            if upper_bound:
                parsed = parsed.replace(second=59)
            local_dt = parsed.replace(tzinfo=local_timezone())
            return local_dt.astimezone(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
        except ValueError:
            pass
    try:
        parsed_date = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    local_dt = datetime.combine(
        parsed_date,
        time.max if upper_bound else time.min,
        tzinfo=local_timezone(),
    )
    return local_dt.astimezone(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")


def local_timezone() -> timezone | ZoneInfo:
    timezone_name = os.environ.get("TZ") or "Asia/Tokyo"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        fixed_timezones = {
            "Asia/Tokyo": timezone(timedelta(hours=9), "JST"),
            "Japan": timezone(timedelta(hours=9), "JST"),
            "JST": timezone(timedelta(hours=9), "JST"),
            "UTC": timezone.utc,
            "Etc/UTC": timezone.utc,
        }
        return fixed_timezones.get(timezone_name, timezone.utc)


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
