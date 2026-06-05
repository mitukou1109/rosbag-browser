from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import connect, init_db
from app.indexer import parse_bag_directory, scan_bags
from app.repository import get_bag, search_bags, update_note, update_tags


def test_parse_valid_mcap_metadata(tmp_path: Path) -> None:
    bag_dir = _make_bag(
        tmp_path,
        "rosbag2_2026_06_04-16_44_18-5",
        storage_identifier="mcap",
        file_name="rosbag2_2026_06_04-16_44_18-5_0.mcap",
    )

    bag = parse_bag_directory(bag_dir)

    assert bag.status == "valid"
    assert bag.storage_identifier == "mcap"
    assert bag.duration_ns == 12_345_000_000
    assert bag.message_count == 42
    assert bag.size_bytes == 6
    assert bag.topics[0].name == "/camera/image_raw"
    assert bag.topics[0].type == "sensor_msgs/msg/Image"


def test_parse_valid_sqlite3_metadata(tmp_path: Path) -> None:
    bag_dir = _make_bag(
        tmp_path,
        "sqlite_bag",
        storage_identifier="sqlite3",
        file_name="sqlite_bag_0.db3",
    )

    bag = parse_bag_directory(bag_dir)

    assert bag.status == "valid"
    assert bag.storage_identifier == "sqlite3"


def test_invalid_yaml_is_unreadable(tmp_path: Path) -> None:
    bag_dir = tmp_path / "bad_yaml"
    bag_dir.mkdir()
    (bag_dir / "metadata.yaml").write_text(":\n  - invalid", encoding="utf-8")

    bag = parse_bag_directory(bag_dir)

    assert bag.status == "unreadable"
    assert "invalid YAML" in (bag.error_message or "")


def test_missing_information_is_broken(tmp_path: Path) -> None:
    bag_dir = tmp_path / "broken"
    bag_dir.mkdir()
    (bag_dir / "metadata.yaml").write_text("not_rosbag: true\n", encoding="utf-8")

    bag = parse_bag_directory(bag_dir)

    assert bag.status == "broken"
    assert "rosbag2_bagfile_information" in (bag.error_message or "")


def test_missing_relative_file_is_missing_files(tmp_path: Path) -> None:
    bag_dir = _make_bag(tmp_path, "missing_file", write_file=False)

    bag = parse_bag_directory(bag_dir)

    assert bag.status == "missing_files"
    assert "is missing" in (bag.error_message or "")


def test_scan_preserves_note_and_tags(tmp_path: Path) -> None:
    bag_root = tmp_path / "bags"
    db_path = tmp_path / "data.sqlite3"
    _make_bag(bag_root, "keeps_metadata")

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, bag_root)
        assert result.scanned == 1
        bag = search_bags(conn)[0]
        update_note(conn, bag["id"], "important run")
        update_tags(conn, bag["id"], "field, camera")
        conn.commit()

        result = scan_bags(conn, bag_root)
        assert result.scanned == 1
        updated = get_bag(conn, bag["id"])

    assert updated is not None
    assert updated["note"] == "important run"
    assert updated["tag_list"] == ["field", "camera"]


def _make_bag(
    root: Path,
    name: str,
    *,
    storage_identifier: str = "mcap",
    file_name: str = "bag_0.mcap",
    write_file: bool = True,
) -> Path:
    bag_dir = root / name
    bag_dir.mkdir(parents=True)
    if write_file:
        (bag_dir / file_name).write_bytes(b"abcdef")
    bag_dir.joinpath("metadata.yaml").write_text(
        f"""
rosbag2_bagfile_information:
  version: 8
  storage_identifier: {storage_identifier}
  relative_file_paths:
    - {file_name}
  duration:
    nanoseconds: 12345000000
  starting_time:
    nanoseconds_since_epoch: 1780578258000000000
  message_count: 42
  topics_with_message_count:
    - topic_metadata:
        name: /camera/image_raw
        type: sensor_msgs/msg/Image
        serialization_format: cdr
      message_count: 40
    - topic_metadata:
        name: /tf
        type: tf2_msgs/msg/TFMessage
        serialization_format: cdr
      message_count: 2
""".lstrip(),
        encoding="utf-8",
    )
    return bag_dir
