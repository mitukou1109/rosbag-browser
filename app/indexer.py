from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

from app.models import BagRecord, ScanResult, TopicRecord
from app.repository import upsert_bag


def scan_bags(conn: sqlite3.Connection, bag_root: Path) -> ScanResult:
    start = time.monotonic()
    result = ScanResult()
    if not bag_root.exists():
        return ScanResult(scanned=0, unknown=1, duration_seconds=time.monotonic() - start)

    for dirpath, _, filenames in os.walk(bag_root):
        if "metadata.yaml" not in filenames:
            continue
        bag = parse_bag_directory(Path(dirpath))
        upsert_bag(conn, bag)
        result = result.increment(bag.status)
    conn.commit()
    return ScanResult(
        scanned=result.scanned,
        valid=result.valid,
        broken=result.broken,
        missing_files=result.missing_files,
        unreadable=result.unreadable,
        unknown=result.unknown,
        duration_seconds=time.monotonic() - start,
    )


def parse_bag_directory(bag_dir: Path) -> BagRecord:
    metadata_path = bag_dir / "metadata.yaml"
    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except OSError as exc:
        return _error_bag(bag_dir, "unreadable", f"metadata.yaml is unreadable: {exc}")
    except yaml.YAMLError as exc:
        return _error_bag(bag_dir, "unreadable", f"metadata.yaml is invalid YAML: {exc}")

    if not isinstance(loaded, dict):
        return _error_bag(bag_dir, "broken", "metadata.yaml root is not a mapping")

    info = loaded.get("rosbag2_bagfile_information")
    if not isinstance(info, dict):
        return _error_bag(
            bag_dir,
            "broken",
            "rosbag2_bagfile_information is missing or not a mapping",
        )

    topics_result = _parse_topics(info.get("topics_with_message_count"))
    topics = topics_result[0]
    errors = topics_result[1]

    relative_paths = info.get("relative_file_paths")
    file_status, file_error, size_bytes = _check_bag_files(bag_dir, relative_paths)
    if errors:
        status = "broken"
        error_message = "; ".join(errors)
    elif file_status != "valid":
        status = file_status
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


def _error_bag(bag_dir: Path, status: str, error_message: str) -> BagRecord:
    return BagRecord(
        path=str(bag_dir),
        name=bag_dir.name,
        status=status,
        error_message=error_message,
        size_bytes=_directory_size(bag_dir),
    )


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
) -> tuple[str, str | None, int]:
    if not isinstance(relative_file_paths, list) or not relative_file_paths:
        return (
            "missing_files",
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
        return "missing_files", "; ".join(problems), size_bytes
    return "valid", None, size_bytes


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
            return str(value["nanoseconds_since_epoch"])
        if "sec" in value or "nanosec" in value:
            sec = value.get("sec", 0)
            nanosec = value.get("nanosec", 0)
            return f"{sec}.{str(nanosec).zfill(9)}"
    return str(value)
