from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path


DEFAULT_COMMAND = [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
]


def main() -> None:
    app_user = os.environ.get("APP_USER", "appuser")
    user_info = pwd.getpwnam(app_user)
    db_path = Path(os.environ.get("DB_PATH", "/data/rosbag-browser.sqlite3")).expanduser()

    prepare_db_path(db_path.resolve(), user_info.pw_uid, user_info.pw_gid)
    drop_privileges(user_info.pw_uid, user_info.pw_gid)

    command = sys.argv[1:] or DEFAULT_COMMAND
    os.execvp(command[0], command)


def prepare_db_path(db_path: Path, uid: int, gid: int) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(db_path.parent, uid, gid)
    for path in _sqlite_paths(db_path):
        if path.exists():
            os.chown(path, uid, gid)


def drop_privileges(uid: int, gid: int) -> None:
    os.setgid(gid)
    os.setuid(uid)


def _sqlite_paths(db_path: Path) -> list[Path]:
    return [
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ]


if __name__ == "__main__":
    main()
