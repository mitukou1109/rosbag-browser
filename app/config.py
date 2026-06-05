from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bag_root: Path
    db_path: Path


def get_settings() -> Settings:
    return Settings(
        bag_root=Path(os.environ.get("BAG_ROOT", "/bags")).resolve(),
        db_path=Path(os.environ.get("DB_PATH", "/data/rosbag-browser.sqlite3")).resolve(),
    )
