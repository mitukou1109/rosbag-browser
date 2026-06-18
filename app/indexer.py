from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.models import BagRecord, ScanResult, TopicRecord
from app.repository import (
    bag_record_for_root,
    delete_stale_bag_indexes,
    find_indexed_bag,
    is_excluded_relative_path,
    list_excluded_directories,
    root_relative_path,
    update_bag_location,
    update_last_scanned_at,
    upsert_bag,
)


BAG_FILE_SUFFIXES = {".mcap", ".db3"}


def scan_bags(
    conn: sqlite3.Connection,
    bag_root: Path,
    *,
    prune_by_relative_paths: bool = False,
) -> ScanResult:
    start = time.monotonic()
    result = ScanResult()
    current_paths: set[str] = set()
    excluded_relative_paths = set(list_excluded_directories(conn))
    if not bag_root.exists():
        delete_stale_bag_indexes(
            conn,
            bag_root,
            current_paths,
            prune_by_relative_paths=prune_by_relative_paths,
        )
        conn.commit()
        return ScanResult(duration_seconds=time.monotonic() - start)

    for dirpath, dirnames, filenames in os.walk(bag_root):
        current_dir = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname != ".rosbag-browser"
            and not _is_excluded_child_directory(
                current_dir,
                dirname,
                bag_root,
                excluded_relative_paths,
            )
        ]
        if not _is_bag_candidate(Path(dirpath), filenames):
            continue
        bag_dir = Path(dirpath)
        current_paths.add(str(bag_dir.resolve()))
        relative_path = root_relative_path(bag_dir, bag_root)
        index_signature = _index_signature(bag_dir, filenames)
        existing = find_indexed_bag(
            conn,
            path=str(bag_dir),
            root_relative_path=relative_path,
        )
        if existing is not None and existing["index_signature"] == index_signature:
            _refresh_bag_location(conn, existing, bag_dir, relative_path)
            result = result.increment(str(existing["status"]))
            continue

        bag = bag_record_for_root(
            parse_bag_directory(bag_dir, index_signature=index_signature),
            bag_root,
        )
        upsert_bag(conn, bag)
        result = result.increment(bag.status)
    delete_stale_bag_indexes(
        conn,
        bag_root,
        current_paths,
        prune_by_relative_paths=prune_by_relative_paths,
    )
    update_last_scanned_at(conn, bag_root)
    conn.commit()
    return ScanResult(
        scanned=result.scanned,
        valid=result.valid,
        broken=result.broken,
        duration_seconds=time.monotonic() - start,
    )


def parse_bag_directory(bag_dir: Path, *, index_signature: str = "") -> BagRecord:
    metadata_path = bag_dir / "metadata.yaml"
    if not metadata_path.exists():
        return _error_bag(
            bag_dir,
            "metadata.yaml is missing",
            index_signature=index_signature,
        )
    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except OSError as exc:
        return _error_bag(
            bag_dir,
            f"metadata.yaml is unreadable: {exc}",
            index_signature=index_signature,
        )
    except yaml.YAMLError as exc:
        return _error_bag(
            bag_dir,
            f"metadata.yaml is invalid YAML: {exc}",
            index_signature=index_signature,
        )

    if not isinstance(loaded, dict):
        return _error_bag(
            bag_dir,
            "metadata.yaml root is not a mapping",
            index_signature=index_signature,
        )

    info = loaded.get("rosbag2_bagfile_information")
    if not isinstance(info, dict):
        return _error_bag(
            bag_dir,
            "rosbag2_bagfile_information is missing or not a mapping",
            index_signature=index_signature,
        )

    topics_result = _parse_topics(info.get("topics_with_message_count"))
    topics = topics_result[0]
    errors = topics_result[1]

    relative_paths = info.get("relative_file_paths")
    files_are_valid, file_error, size_bytes = _check_bag_files(bag_dir, relative_paths)
    if errors:
        status = "broken"
        error_message = "; ".join(errors)
    elif not files_are_valid:
        status = "broken"
        error_message = file_error
    else:
        status = "valid"
        error_message = None

    return BagRecord(
        path=str(bag_dir),
        name=bag_dir.name,
        storage_identifier=_optional_str(info.get("storage_identifier")),
        starting_time=_normalize_starting_time(info.get("starting_time")),
        duration_ns=_extract_nanoseconds(info.get("duration")),
        message_count=_optional_int(info.get("message_count")),
        size_bytes=size_bytes,
        status=status,
        error_message=error_message,
        index_signature=index_signature,
        topics=topics,
    )


def _error_bag(
    bag_dir: Path,
    error_message: str,
    *,
    index_signature: str = "",
) -> BagRecord:
    return BagRecord(
        path=str(bag_dir),
        name=bag_dir.name,
        status="broken",
        error_message=error_message,
        index_signature=index_signature,
        size_bytes=_directory_size(bag_dir),
    )


def _index_signature(bag_dir: Path, filenames: list[str]) -> str:
    metadata_path = bag_dir / "metadata.yaml"
    has_metadata = "metadata.yaml" in filenames
    parts = ["v1"]
    if has_metadata:
        parts.append(_file_signature(metadata_path, "metadata.yaml"))
    for filename in sorted(filenames):
        path = Path(filename)
        if path.suffix not in BAG_FILE_SUFFIXES:
            continue
        if not has_metadata and not _looks_like_rosbag_file(bag_dir, filename):
            continue
        parts.append(_file_signature(bag_dir / filename, filename))
    return "\n".join(parts)


def _file_signature(path: Path, label: str) -> str:
    try:
        stat_result = path.stat()
    except OSError as exc:
        return f"{label}:error:{exc.__class__.__name__}"
    return f"{label}:{stat_result.st_size}:{stat_result.st_mtime_ns}"


def _refresh_bag_location(
    conn: sqlite3.Connection,
    existing: sqlite3.Row,
    bag_dir: Path,
    relative_path: str | None,
) -> None:
    path = str(bag_dir)
    if existing["path"] == path and existing["root_relative_path"] == relative_path:
        return
    update_bag_location(
        conn,
        int(existing["id"]),
        path=path,
        root_relative_path=relative_path,
    )


def _is_bag_candidate(directory: Path, filenames: list[str]) -> bool:
    if "metadata.yaml" in filenames:
        return True
    return any(_looks_like_rosbag_file(directory, filename) for filename in filenames)


def _is_excluded_child_directory(
    current_dir: Path,
    dirname: str,
    bag_root: Path,
    excluded_relative_paths: set[str],
) -> bool:
    if not excluded_relative_paths:
        return False
    relative_path = root_relative_path(current_dir / dirname, bag_root)
    return is_excluded_relative_path(relative_path, excluded_relative_paths)


def _looks_like_rosbag_file(directory: Path, filename: str) -> bool:
    path = Path(filename)
    if path.suffix not in BAG_FILE_SUFFIXES:
        return False
    return path.stem == directory.name or path.stem.startswith(f"{directory.name}_")


def _parse_topics(value: Any) -> tuple[list[TopicRecord], list[str]]:
    if not isinstance(value, list):
        return [], ["topics_with_message_count is missing or not a list"]
    topics: list[TopicRecord] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"topic entry {index} is not a mapping")
            continue
        metadata = item.get("topic_metadata")
        if not isinstance(metadata, dict):
            errors.append(f"topic entry {index} has no topic_metadata mapping")
            continue
        name = _optional_str(metadata.get("name"))
        if not name:
            errors.append(f"topic entry {index} has no topic name")
            continue
        topics.append(
            TopicRecord(
                name=name,
                type=_optional_str(metadata.get("type")),
                serialization_format=_optional_str(metadata.get("serialization_format")),
                message_count=_optional_int(item.get("message_count")),
            )
        )
    return topics, errors


def _check_bag_files(
    bag_dir: Path, relative_file_paths: Any
) -> tuple[bool, str | None, int]:
    if not isinstance(relative_file_paths, list) or not relative_file_paths:
        return (
            False,
            "relative_file_paths is missing or empty",
            _directory_size(bag_dir),
        )

    size_bytes = 0
    problems: list[str] = []
    for raw_path in relative_file_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            problems.append("relative_file_paths contains a non-string path")
            continue
        bag_file = (bag_dir / raw_path).resolve()
        try:
            bag_file.relative_to(bag_dir.resolve())
        except ValueError:
            problems.append(f"{raw_path} escapes the bag directory")
            continue
        if not bag_file.exists():
            problems.append(f"{raw_path} is missing")
            continue
        if not bag_file.is_file():
            problems.append(f"{raw_path} is not a file")
            continue
        file_size = bag_file.stat().st_size
        if file_size <= 0:
            problems.append(f"{raw_path} is empty")
        size_bytes += file_size

    if problems:
        return False, "; ".join(problems), size_bytes
    return True, None, size_bytes


def _directory_size(directory: Path) -> int:
    total = 0
    try:
        for root, _, files in os.walk(directory):
            for filename in files:
                path = Path(root) / filename
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_nanoseconds(value: Any) -> int | None:
    if isinstance(value, dict):
        return _optional_int(value.get("nanoseconds"))
    return _optional_int(value)


def _normalize_starting_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if "nanoseconds_since_epoch" in value:
            ns = _optional_int(value["nanoseconds_since_epoch"])
            if ns is not None:
                return _format_epoch_ns(ns)
        if "sec" in value or "nanosec" in value:
            sec = _optional_int(value.get("sec")) or 0
            nanosec = _optional_int(value.get("nanosec")) or 0
            return _format_epoch_ns(sec * 1_000_000_000 + nanosec)
    numeric = _optional_int(value)
    if numeric is not None and numeric > 10_000_000_000:
        return _format_epoch_ns(numeric)
    return str(value)


def _format_epoch_ns(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc).strftime(
        "%Y/%m/%d %H:%M:%S"
    )
