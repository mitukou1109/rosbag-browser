from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.models import BagRecord, ScanResult, TopicRecord
from app.repository import bag_record_for_root, delete_stale_bag_indexes, upsert_bag


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
        dirnames[:] = [dirname for dirname in dirnames if dirname != ".rosbag-browser"]
        if not _is_bag_candidate(Path(dirpath), filenames):
            continue
        bag_dir = Path(dirpath)
        current_paths.add(str(bag_dir.resolve()))
        bag = bag_record_for_root(parse_bag_directory(bag_dir), bag_root)
        upsert_bag(conn, bag)
        result = result.increment(bag.status)
    delete_stale_bag_indexes(
        conn,
        bag_root,
        current_paths,
        prune_by_relative_paths=prune_by_relative_paths,
    )
    conn.commit()
    return ScanResult(
        scanned=result.scanned,
        valid=result.valid,
        broken=result.broken,
        duration_seconds=time.monotonic() - start,
    )


def parse_bag_directory(bag_dir: Path) -> BagRecord:
    metadata_path = bag_dir / "metadata.yaml"
    if not metadata_path.exists():
        return _error_bag(bag_dir, "metadata.yaml is missing")
    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except OSError as exc:
        return _error_bag(bag_dir, f"metadata.yaml is unreadable: {exc}")
    except yaml.YAMLError as exc:
        return _error_bag(bag_dir, f"metadata.yaml is invalid YAML: {exc}")

    if not isinstance(loaded, dict):
        return _error_bag(bag_dir, "metadata.yaml root is not a mapping")

    info = loaded.get("rosbag2_bagfile_information")
    if not isinstance(info, dict):
        return _error_bag(
            bag_dir,
            "rosbag2_bagfile_information is missing or not a mapping",
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
        topics=topics,
    )


def _error_bag(bag_dir: Path, error_message: str) -> BagRecord:
    return BagRecord(
        path=str(bag_dir),
        name=bag_dir.name,
        status="broken",
        error_message=error_message,
        size_bytes=_directory_size(bag_dir),
    )


def _is_bag_candidate(directory: Path, filenames: list[str]) -> bool:
    if "metadata.yaml" in filenames:
        return True
    return any(_looks_like_rosbag_file(directory, filename) for filename in filenames)


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
