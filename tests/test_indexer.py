from __future__ import annotations

import shutil
from pathlib import Path

from app.db import connect, init_db
from app.indexer import parse_bag_directory, scan_bags
from app.repository import get_bag, search_bags, update_note, update_tags


def test_parse_valid_mcap_metadata(tmp_path: Path) -> None:
    bag_dir = _make_bag(
        tmp_path,
        "rosbag2_2026_06_04-16_44_18",
        storage_identifier="mcap",
        file_name="rosbag2_2026_06_04-16_44_18_0.mcap",
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

    assert bag.status == "broken"
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

    assert bag.status == "broken"
    assert "is missing" in (bag.error_message or "")


def test_scan_indexes_bag_file_directory_without_metadata_as_broken(tmp_path: Path) -> None:
    bag_root = tmp_path / "bags"
    bag_dir = bag_root / "missing_metadata"
    bag_dir.mkdir(parents=True)
    (bag_dir / "missing_metadata_0.mcap").write_bytes(b"abcdef")
    db_path = tmp_path / "data.sqlite3"

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, bag_root)
        bags = search_bags(conn)

    assert result.scanned == 1
    assert result.broken == 1
    assert bags[0]["name"] == "missing_metadata"
    assert bags[0]["status"] == "broken"


def test_scan_missing_bag_root_has_consistent_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite3"

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, tmp_path / "missing-root")
        bags = search_bags(conn)

    assert result.scanned == 0
    assert result.valid == 0
    assert result.broken == 0
    assert bags == []


def test_scan_ignores_unrelated_sqlite_files_without_metadata(tmp_path: Path) -> None:
    bag_root = tmp_path / "bags"
    sqlite_dir = bag_root / "app_data"
    sqlite_dir.mkdir(parents=True)
    (sqlite_dir / "index.sqlite3").write_bytes(b"abcdef")
    app_data_dir = bag_root / ".rosbag-browser"
    app_data_dir.mkdir(parents=True)
    (app_data_dir / "metadata.yaml").write_text("not a bag\n", encoding="utf-8")
    (app_data_dir / "rosbag-browser.sqlite3").write_bytes(b"abcdef")
    db_path = tmp_path / "data.sqlite3"

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, bag_root)
        bags = search_bags(conn)

    assert result.scanned == 0
    assert result.broken == 0
    assert bags == []


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


def test_scan_skips_unchanged_bag_index(
    monkeypatch, tmp_path: Path
) -> None:
    bag_root = tmp_path / "bags"
    db_path = tmp_path / "data.sqlite3"
    bag_dir = _make_bag(bag_root, "fast_bag")

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, bag_root)
        assert result.scanned == 1
        bag = search_bags(conn)[0]

        def fail_parse(*args, **kwargs):
            raise AssertionError("unchanged bag should not be parsed")

        monkeypatch.setattr("app.indexer.parse_bag_directory", fail_parse)
        result = scan_bags(conn, bag_root)
        assert result.scanned == 1
        assert result.valid == 1
        assert get_bag(conn, bag["id"])["size_bytes"] == 6

        monkeypatch.undo()
        (bag_dir / "bag_0.mcap").write_bytes(b"abcdefghi")
        result = scan_bags(conn, bag_root)
        updated = get_bag(conn, bag["id"])

    assert result.scanned == 1
    assert updated is not None
    assert updated["size_bytes"] == 9


def test_scan_removes_deleted_bag_from_index(tmp_path: Path) -> None:
    bag_root = tmp_path / "bags"
    external_root = tmp_path / "external-bags"
    db_path = tmp_path / "data.sqlite3"
    deleted_bag = _make_bag(bag_root, "deleted_bag")
    _make_bag(bag_root, "remaining_bag")
    _make_bag(external_root, "external_bag")

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, external_root)
        assert result.scanned == 1

        result = scan_bags(conn, bag_root)
        assert result.scanned == 2
        assert [bag["name"] for bag in search_bags(conn)] == [
            "deleted_bag",
            "external_bag",
            "remaining_bag",
        ]

        shutil.rmtree(deleted_bag)
        result = scan_bags(conn, bag_root)
        bags = search_bags(conn)

    assert result.scanned == 1
    assert [bag["name"] for bag in bags] == ["external_bag", "remaining_bag"]


def test_scan_preserves_metadata_after_root_path_changes(tmp_path: Path) -> None:
    original_root = tmp_path / "mount-a"
    moved_root = tmp_path / "mount-b"
    db_path = original_root / ".rosbag-browser" / "rosbag-browser.sqlite3"
    _make_bag(original_root, "portable_bag")

    with connect(db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, original_root)
        assert result.scanned == 1
        bag = search_bags(conn, bag_root=original_root)[0]
        update_note(conn, bag["id"], "portable note")
        update_tags(conn, bag["id"], "ssd, field")
        conn.commit()

    shutil.move(str(original_root), str(moved_root))
    moved_db_path = moved_root / ".rosbag-browser" / "rosbag-browser.sqlite3"

    with connect(moved_db_path) as conn:
        init_db(conn)
        result = scan_bags(conn, moved_root)
        bags = search_bags(conn, bag_root=moved_root)

    assert result.scanned == 1
    assert len(bags) == 1
    assert bags[0]["name"] == "portable_bag"
    assert bags[0]["note"] == "portable note"
    assert bags[0]["tag_list"] == ["ssd", "field"]
    assert bags[0]["path"] == str(moved_root / "portable_bag")
    assert bags[0]["path_display"] == "portable_bag"


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
