from __future__ import annotations

import importlib
from pathlib import Path

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
    assert "Scan</button>" in initial_bags.text
    assert "OR" in initial_bags.text
    assert "NOT" in initial_bags.text
    modal_response = client.get("/scan/modal")
    assert modal_response.status_code == 200
    assert "Scan" in modal_response.text
    scan_response = client.post("/scan")
    assert scan_response.status_code == 200
    assert "web_bag" not in scan_response.text
    refresh_response = client.post("/bags/scan", follow_redirects=False)
    assert refresh_response.status_code == 303

    bags_response = client.get("/bags")
    assert bags_response.status_code == 200
    assert "web_bag" in bags_response.text
    filtered_response = client.get("/bags?topic=camera%20OR%20missing")
    assert filtered_response.status_code == 200
    assert "web_bag" in filtered_response.text

    detail_response = client.get("/bags/1")
    assert detail_response.status_code == 200
    assert "/camera/front" in detail_response.text

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
