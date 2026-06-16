from __future__ import annotations

import inspect
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


class BagMergeError(RuntimeError):
    """Raised when selected bags cannot be merged."""


def default_merge_name() -> str:
    return f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def merge_bag_directories(
    bag_dirs: list[Path],
    output_root: Path,
    output_name: str,
) -> Path:
    if len(bag_dirs) < 2:
        raise BagMergeError("Select at least two bags to merge")

    target = _target_path(output_root, output_name)
    output_root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise BagMergeError(f"{target} already exists")

    try:
        _write_merged_bag(bag_dirs, target)
    except BagMergeError:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise
    except Exception as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise BagMergeError(f"Merge failed: {exc}") from exc
    return target


def normalized_output_name(value: str) -> str:
    name = value.strip() or default_merge_name()
    path = Path(name)
    if path.name != name or name in {".", ".."} or "/" in name or "\\" in name:
        raise BagMergeError("Output name must be a directory name, not a path")
    return name


def _target_path(output_root: Path, output_name: str) -> Path:
    root = output_root.expanduser().resolve()
    target = (root / normalized_output_name(output_name)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BagMergeError("Output path escapes the output root") from exc
    return target


def _write_merged_bag(bag_dirs: list[Path], target: Path) -> None:
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag2 import Writer
    except ImportError as exc:
        raise BagMergeError("Python package 'rosbags' is required to merge bags") from exc

    with AnyReader([path.resolve() for path in bag_dirs]) as reader:
        writer = _new_writer(Writer, target)
        writer.open()
        try:
            connections: dict[tuple[str, str, str, str], Any] = {}
            for connection, timestamp, rawdata in reader.messages():
                writer_connection = _writer_connection(writer, connections, connection)
                writer.write(writer_connection, timestamp, rawdata)
        except Exception:
            try:
                writer.close()
            except Exception:
                pass
            raise
        else:
            writer.close()


def _new_writer(writer_class: Any, target: Path) -> Any:
    parameters = inspect.signature(writer_class).parameters
    if "version" in parameters:
        return writer_class(target, version=9)
    return writer_class(target)


def _writer_connection(
    writer: Any,
    connections: dict[tuple[str, str, str, str], Any],
    connection: Any,
) -> Any:
    ext = getattr(connection, "ext", None)
    serialization_format = getattr(ext, "serialization_format", "")
    offered_qos_profiles = getattr(ext, "offered_qos_profiles", "")
    if not serialization_format:
        raise BagMergeError("Only ROS 2 bags can be merged into a ROS 2 output bag")

    key = (
        str(connection.topic),
        str(connection.msgtype),
        str(serialization_format),
        repr(offered_qos_profiles),
    )
    existing = connections.get(key)
    if existing is not None:
        _ensure_compatible_connection(existing, connection)
        return existing

    writer_connection = writer.add_connection(
        str(connection.topic),
        str(connection.msgtype),
        msgdef=_message_definition_data(getattr(connection, "msgdef", None)),
        rihs01=getattr(connection, "digest", None) or None,
        serialization_format=str(serialization_format),
        offered_qos_profiles=offered_qos_profiles,
    )
    connections[key] = writer_connection
    return writer_connection


def _message_definition_data(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    data = getattr(value, "data", None)
    if isinstance(data, str):
        return data or None
    return None


def _ensure_compatible_connection(writer_connection: Any, connection: Any) -> None:
    writer_digest = getattr(writer_connection, "digest", None) or ""
    reader_digest = getattr(connection, "digest", None) or ""
    if writer_digest != reader_digest:
        raise BagMergeError(
            f"Topic {connection.topic} has conflicting message definitions"
        )
