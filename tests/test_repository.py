from __future__ import annotations

from pathlib import Path

from app.db import connect, init_db
from app.models import BagRecord, TopicRecord
from app.repository import (
    add_excluded_directory,
    excluded_directory_paths_to_text,
    add_tag,
    get_bag,
    list_excluded_directories,
    parse_excluded_directory_paths,
    list_tags,
    remove_excluded_directory,
    remove_tags,
    search_bags,
    set_excluded_directories,
    update_note,
    update_tags,
    upsert_bag,
)


def test_search_by_topic_keyword_tag_and_period(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite3"
    with connect(db_path) as conn:
        init_db(conn)
        bag_id = upsert_bag(
            conn,
            BagRecord(
                path="/bags/run_a",
                name="run_a",
                storage_identifier="mcap",
                starting_time="2026/06/04 16:44:18",
                message_count=10,
                size_bytes=100,
                status="valid",
                topics=[
                    TopicRecord(
                        name="/camera/front",
                        type="sensor_msgs/msg/Image",
                        serialization_format="cdr",
                        message_count=10,
                    ),
                    TopicRecord(
                        name="/tf_static",
                        type="tf2_msgs/msg/TFMessage",
                        serialization_format="cdr",
                        message_count=1,
                    ),
                ],
            ),
        )
        upsert_bag(
            conn,
            BagRecord(
                path="/bags/run_b",
                name="run_b",
                storage_identifier="sqlite3",
                starting_time="2026/06/05 09:00:00",
                message_count=3,
                size_bytes=50,
                status="broken",
                topics=[
                    TopicRecord(
                        name="/tf",
                        type="tf2_msgs/msg/TFMessage",
                        serialization_format="cdr",
                        message_count=3,
                    )
                ],
            ),
        )
        update_note(conn, bag_id, "sunny calibration")
        update_tags(conn, bag_id, "field, camera")
        conn.commit()

        assert [bag["name"] for bag in search_bags(conn, topic="camera")] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, topic="camera tf_static")
        ] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, topic="camera OR tf")
        ] == ["run_b", "run_a"]
        assert [bag["name"] for bag in search_bags(conn, topic="camera NOT odom")] == [
            "run_a"
        ]
        assert [bag["name"] for bag in search_bags(conn, topic="camera NOT tf")] == []
        assert [
            bag["name"] for bag in search_bags(conn, topic="camera NOT (odom OR lidar)")
        ] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, topic="camera NOT (tf OR lidar)")
        ] == []
        assert [
            bag["name"] for bag in search_bags(conn, topic="(camera OR imu) tf_static")
        ] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, topic="camera) OR tf")
        ] == ["run_b", "run_a"]
        assert [bag["name"] for bag in search_bags(conn, q="calibration")] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, q="run_a calibration")
        ] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, q="calibration OR run_b")
        ] == ["run_b", "run_a"]
        assert [bag["name"] for bag in search_bags(conn, q="run_a NOT missing")] == [
            "run_a"
        ]
        assert [bag["name"] for bag in search_bags(conn, q="run_a NOT sunny")] == []
        assert [
            bag["name"] for bag in search_bags(conn, q="run_a NOT (missing OR failed)")
        ] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, q="run_a NOT (sunny OR failed)")
        ] == []
        assert [
            bag["name"] for bag in search_bags(conn, q="run_a) OR run_b")
        ] == ["run_b", "run_a"]
        assert [bag["name"] for bag in search_bags(conn, q="Image")] == []
        assert [bag["name"] for bag in search_bags(conn, tag="field")] == ["run_a"]
        assert [
            bag["name"]
            for bag in search_bags(conn, start_from="2026-06-05", start_to="2026-06-05")
        ] == [
            "run_b",
            "run_a",
        ]
        assert [
            bag["name"]
            for bag in search_bags(
                conn,
                start_from="2026-06-05T00:00",
                start_to="2026-06-05T01:44",
            )
        ] == ["run_a"]
        assert [
            bag["name"] for bag in search_bags(conn, start_from="2026-06-05T02:00")
        ] == ["run_b"]
        assert [
            bag["name"] for bag in search_bags(conn, start_from="not-a-date")
        ] == ["run_b", "run_a"]
        assert [bag["name"] for bag in search_bags(conn, start_to="not-a-date")] == [
            "run_b",
            "run_a",
        ]
        assert get_bag(conn, bag_id)["starting_time_text"] == "2026/06/05 01:44:18"
        assert list_tags(conn) == ["camera", "field"]

        unchanged_modified_at = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE bags SET modified_at = ? WHERE id = ?",
            (unchanged_modified_at, bag_id),
        )
        add_tag(conn, bag_id, "")
        add_tag(conn, bag_id, "camera")
        remove_tags(conn, bag_id, [])
        remove_tags(conn, bag_id, ["missing"])
        conn.commit()
        assert get_bag(conn, bag_id)["modified_at"] == unchanged_modified_at

        add_tag(conn, bag_id, "night")
        remove_tags(conn, bag_id, ["field"])
        conn.commit()
        updated = get_bag(conn, bag_id)

        assert updated is not None
        assert updated["tag_list"] == ["camera", "night"]


def test_upsert_uses_root_relative_path_as_stable_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite3"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    with connect(db_path) as conn:
        init_db(conn)
        bag_id = upsert_bag(
            conn,
            BagRecord(
                path=str(first_root / "run_a"),
                root_relative_path="run_a",
                name="run_a",
                status="valid",
            ),
        )
        update_note(conn, bag_id, "keep me")
        update_tags(conn, bag_id, "portable")
        conn.commit()

        updated_id = upsert_bag(
            conn,
            BagRecord(
                path=str(second_root / "run_a"),
                root_relative_path="run_a",
                name="run_a",
                status="broken",
            ),
        )
        conn.commit()
        updated = get_bag(conn, updated_id, bag_root=second_root)

    assert updated_id == bag_id
    assert updated is not None
    assert updated["path"] == str(second_root / "run_a")
    assert updated["path_display"] == "run_a"
    assert updated["note"] == "keep me"
    assert updated["tag_list"] == ["portable"]


def test_excluded_directories_are_normalized_and_stored(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite3"
    with connect(db_path) as conn:
        init_db(conn)
        assert add_excluded_directory(conn, " archive/old-runs/ ") == "archive/old-runs"
        add_excluded_directory(conn, "archive/old-runs")
        add_excluded_directory(conn, "tmp")
        conn.commit()

        assert list_excluded_directories(conn) == ["archive/old-runs", "tmp"]

        remove_excluded_directory(conn, "archive/old-runs/")
        conn.commit()

        assert list_excluded_directories(conn) == ["tmp"]


def test_excluded_directory_paths_text_supports_spaces_and_quotes(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite3"
    with connect(db_path) as conn:
        init_db(conn)
        assert parse_excluded_directory_paths(
            'archive "runs with spaces" archive'
        ) == ["archive", "runs with spaces"]
        assert excluded_directory_paths_to_text(
            ["archive", "runs with spaces"]
        ) == 'archive "runs with spaces"'

        set_excluded_directories(conn, 'archive "runs with spaces"')
        conn.commit()

        assert list_excluded_directories(conn) == ["archive", "runs with spaces"]

        set_excluded_directories(conn, "")
        conn.commit()

        assert list_excluded_directories(conn) == []
