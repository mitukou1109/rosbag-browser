from __future__ import annotations

import io
import importlib
from pathlib import Path
from zipfile import ZipFile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient


def test_bag_pages_scan_and_edit_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bag_root = tmp_path / "bags"
    data_dir = tmp_path / "data"
    db_path = data_dir / "app.sqlite3"
    _make_bag(bag_root, "web_bag")
    monkeypatch.setenv("BAG_ROOT", str(bag_root))
    monkeypatch.setenv("DB_PATH", str(db_path))

    main = importlib.import_module("app.main")
    client = TestClient(main.create_app())

    initial_bags = client.get("/bags")
    assert initial_bags.status_code == 200
    assert "Scan" in initial_bags.text
    assert "Last scanned: Never" in initial_bags.text
    assert "Current bag root" not in initial_bags.text
    assert "OR" in initial_bags.text
    assert "NOT" in initial_bags.text
    assert client.post("/settings/bag-root", data={"bag_root": str(tmp_path)}).status_code == 403
    scan_response = client.post("/bags/scan", follow_redirects=False)
    assert scan_response.status_code == 303

    bags_response = client.get("/bags")
    assert bags_response.status_code == 200
    assert "web_bag" in bags_response.text
    filtered_response = client.get("/bags?topic=camera%20OR%20missing")
    assert filtered_response.status_code == 200
    assert "web_bag" in filtered_response.text

    detail_response = client.get("/bags/1")
    assert detail_response.status_code == 200
    assert "Download zip" in detail_response.text
    assert "/camera/front" in detail_response.text

    download_response = client.get("/bags/1/download")
    assert download_response.status_code == 200
    assert download_response.headers["content-type"] == "application/zip"
    assert "web_bag.zip" in download_response.headers["content-disposition"]
    with ZipFile(io.BytesIO(download_response.content)) as archive:
        assert sorted(archive.namelist()) == [
            "web_bag/metadata.yaml",
            "web_bag/web_0.mcap",
        ]

    assert client.post("/bags/1/note", data={"note": "route note"}).status_code == 200
    assert client.post("/bags/1/tags/add", data={"tag": "field"}).status_code == 200
    assert client.post("/bags/1/tags/add", data={"tag": "route"}).status_code == 200
    updated = client.get("/bags/1")
    assert "route note" in updated.text
    assert "field" in updated.text
    assert "route" in updated.text

    assert (
        client.post("/bags/1/tags/remove", data={"tags_to_remove": "field"}).status_code
        == 200
    )
    removed = client.get("/bags/1")
    assert "route" in removed.text


def test_local_mode_selects_root_and_uses_root_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bag_root = tmp_path / "portable-bags"
    other_root = tmp_path / "other-bags"
    _make_bag(bag_root, "local_bag")
    _make_bag(other_root, "other_bag")
    invalid_file = tmp_path / "not-a-directory"
    invalid_file.write_text("x", encoding="utf-8")
    monkeypatch.delenv("BAG_ROOT", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    main = importlib.import_module("app.main")
    client = TestClient(main.create_app())

    initial_bags = client.get("/bags")
    assert initial_bags.status_code == 200
    assert "Current bag root" in initial_bags.text
    assert "Not selected" in initial_bags.text

    scan_without_root = client.post("/bags/scan", follow_redirects=False)
    assert scan_without_root.status_code == 303
    assert "root_error=" in scan_without_root.headers["location"]

    invalid_response = client.post(
        "/settings/bag-root",
        data={"bag_root": str(invalid_file)},
        follow_redirects=True,
    )
    assert invalid_response.status_code == 200
    assert "is not a directory" in invalid_response.text

    select_response = client.post(
        "/settings/bag-root",
        data={"bag_root": str(bag_root)},
        follow_redirects=False,
    )
    assert select_response.status_code == 303
    assert (bag_root / ".rosbag-browser" / "rosbag-browser.sqlite3").exists()

    selected_bags = client.get("/bags")
    assert str(bag_root) in selected_bags.text
    assert "local_bag" not in selected_bags.text

    assert client.post("/bags/scan", follow_redirects=False).status_code == 303
    scanned_bags = client.get("/bags")
    assert "local_bag" in scanned_bags.text
    assert str(bag_root) in scanned_bags.text

    detail_response = client.get("/bags/1")
    assert detail_response.status_code == 200
    assert "local_bag" in detail_response.text

    other_select_response = client.post(
        "/settings/bag-root",
        data={"bag_root": str(bag_root), "recent_bag_root": str(other_root)},
        follow_redirects=False,
    )
    assert other_select_response.status_code == 303
    other_bags = client.get("/bags")
    assert str(other_root) in other_bags.text
    assert str(bag_root) in other_bags.text


def _make_bag(root: Path, name: str) -> Path:
    bag_dir = root / name
    bag_dir.mkdir(parents=True)
    (bag_dir / "web_0.mcap").write_bytes(b"abc")
    (bag_dir / "metadata.yaml").write_text(
        """
rosbag2_bagfile_information:
  storage_identifier: mcap
  relative_file_paths:
    - web_0.mcap
  duration:
    nanoseconds: 1000000000
  starting_time:
    nanoseconds_since_epoch: 1780578258000000000
  message_count: 1
  topics_with_message_count:
    - topic_metadata:
        name: /camera/front
        type: sensor_msgs/msg/Image
        serialization_format: cdr
      message_count: 1
""".lstrip(),
        encoding="utf-8",
    )
    return bag_dir
