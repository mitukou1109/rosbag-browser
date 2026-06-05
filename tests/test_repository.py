from __future__ import annotations

from pathlib import Path

from app.db import connect, init_db
from app.models import BagRecord, TopicRecord
from app.repository import search_bags, update_note, update_tags, upsert_bag


def test_search_by_topic_type_keyword_tag_and_status(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite3"
    with connect(db_path) as conn:
        init_db(conn)
        bag_id = upsert_bag(
            conn,
            BagRecord(
                path="/bags/run_a",
                name="run_a",
                storage_identifier="mcap",
                message_count=10,
                size_bytes=100,
                status="valid",
                topics=[
                    TopicRecord(
                        name="/camera/front",
                        type="sensor_msgs/msg/Image",
                        serialization_format="cdr",
                        message_count=10,
                    )
                ],
            ),
        )
        upsert_bag(
            conn,
            BagRecord(
                path="/bags/run_b",
                name="run_b",
                storage_identifier="sqlite3",
                message_count=3,
                size_bytes=50,
                status="missing_files",
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
        assert [bag["name"] for bag in search_bags(conn, message_type="Image")] == [
            "run_a"
        ]
        assert [bag["name"] for bag in search_bags(conn, q="calibration")] == ["run_a"]
        assert [bag["name"] for bag in search_bags(conn, tag="field")] == ["run_a"]
        assert [bag["name"] for bag in search_bags(conn, status="missing_files")] == [
            "run_b"
        ]
