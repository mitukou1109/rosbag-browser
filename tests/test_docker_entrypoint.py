from __future__ import annotations

import os
from pathlib import Path

from app.docker_entrypoint import prepare_db_path


def test_prepare_db_path_creates_parent_and_chowns_sqlite_files(
    monkeypatch, tmp_path: Path
) -> None:
    chowned: list[Path] = []

    def fake_chown(path: str | bytes | os.PathLike, uid: int, gid: int) -> None:
        assert uid == 123
        assert gid == 456
        chowned.append(Path(path))

    monkeypatch.setattr(os, "chown", fake_chown)

    db_path = tmp_path / "data" / "rosbag-browser.sqlite3"
    wal_path = db_path.with_name(f"{db_path.name}-wal")
    wal_path.parent.mkdir()
    wal_path.write_bytes(b"wal")

    prepare_db_path(db_path, 123, 456)

    assert db_path.parent.is_dir()
    assert chowned == [db_path.parent, wal_path]
