from __future__ import annotations

import inspect
from pathlib import Path

import pytest

rosbag2 = pytest.importorskip("rosbags.rosbag2")
typesys = pytest.importorskip("rosbags.typesys")

from app.indexer import parse_bag_directory
from app.merger import BagMergeError, merge_bag_directories


def test_merge_bag_directories_writes_ros2_output(tmp_path: Path) -> None:
    first = _make_ros2_bag(tmp_path, "first", 10)
    second = _make_ros2_bag(tmp_path, "second", 20)

    target = merge_bag_directories([first, second], tmp_path, "merged")

    parsed = parse_bag_directory(target)
    assert parsed.status == "valid"
    assert parsed.message_count == 2
    assert [topic.name for topic in parsed.topics] == ["/chatter"]


def test_merge_bag_directories_rejects_existing_output(tmp_path: Path) -> None:
    first = _make_ros2_bag(tmp_path, "first", 10)
    second = _make_ros2_bag(tmp_path, "second", 20)
    (tmp_path / "merged").mkdir()

    with pytest.raises(BagMergeError, match="already exists"):
        merge_bag_directories([first, second], tmp_path, "merged")


def _make_ros2_bag(root: Path, name: str, timestamp: int) -> Path:
    typestore = typesys.get_typestore(typesys.Stores.ROS2_FOXY)
    path = root / name
    writer = _new_test_writer(path)
    writer.open()
    try:
        connection = writer.add_connection(
            "/chatter",
            "std_msgs/msg/String",
            typestore=typestore,
        )
        writer.write(connection, timestamp, b"\x00\x00\x00\x00")
    finally:
        writer.close()
    return path


def _new_test_writer(path: Path):
    writer_class = rosbag2.Writer
    parameters = inspect.signature(writer_class).parameters
    if "version" in parameters:
        return writer_class(path, version=9)
    return writer_class(path)
