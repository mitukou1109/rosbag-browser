from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOCAL_APP_DIR_NAME = ".rosbag-browser"
LOCAL_DB_FILENAME = "rosbag-browser.sqlite3"
LOCAL_STATE_FILENAME = "local-settings.json"
LOCAL_ROOT_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class LocalRootState:
    current_bag_root: Path | None
    recent_bag_roots: list[Path]


@dataclass(frozen=True)
class Settings:
    fixed_bag_root: Path | None
    db_path: Path
    local_state_path: Path

    @property
    def is_fixed_root(self) -> bool:
        return self.fixed_bag_root is not None


def get_settings() -> Settings:
    fixed_bag_root = _optional_path_from_env("BAG_ROOT")
    return Settings(
        fixed_bag_root=fixed_bag_root,
        db_path=Path(os.environ.get("DB_PATH", "/data/rosbag-browser.sqlite3")).resolve(),
        local_state_path=local_state_path(),
    )


def _optional_path_from_env(name: str) -> Path | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def local_state_path() -> Path:
    explicit = os.environ.get("ROSBAG_BROWSER_LOCAL_SETTINGS")
    if explicit and explicit.strip():
        return Path(explicit).expanduser().resolve()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home and xdg_data_home.strip():
        data_dir = Path(xdg_data_home).expanduser()
    else:
        data_dir = Path.home() / ".local" / "share"
    return (data_dir / "rosbag-browser" / LOCAL_STATE_FILENAME).resolve()


def load_local_root_state(settings: Settings) -> LocalRootState:
    try:
        raw = json.loads(settings.local_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    current = _state_path(raw.get("current_bag_root"))
    recent = _recent_paths(raw.get("recent_bag_roots"))
    if current is not None and current not in recent:
        recent.insert(0, current)
    return LocalRootState(current_bag_root=current, recent_bag_roots=recent)


def set_local_bag_root(settings: Settings, raw_path: str) -> Path:
    if settings.is_fixed_root:
        raise ValueError("BAG_ROOT is fixed for this server")

    bag_root = normalize_bag_root_input(raw_path)
    app_dir = bag_root / LOCAL_APP_DIR_NAME
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create {app_dir}: {exc}") from exc
    if not app_dir.is_dir():
        raise ValueError(f"{app_dir} is not a directory")

    previous = load_local_root_state(settings)
    recent = [bag_root, *[path for path in previous.recent_bag_roots if path != bag_root]]
    recent = recent[:LOCAL_ROOT_HISTORY_LIMIT]
    save_local_root_state(settings, LocalRootState(bag_root, recent))
    return bag_root


def save_local_root_state(settings: Settings, state: LocalRootState) -> None:
    settings.local_state_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "current_bag_root": str(state.current_bag_root)
        if state.current_bag_root is not None
        else None,
        "recent_bag_roots": [str(path) for path in state.recent_bag_roots],
    }
    settings.local_state_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def normalize_bag_root_input(raw_path: str) -> Path:
    text = raw_path.strip()
    if not text:
        raise ValueError("Bag root path is required")
    try:
        bag_root = Path(text).expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"Cannot resolve bag root path: {exc}") from exc
    if not bag_root.exists():
        raise ValueError(f"{bag_root} does not exist")
    if not bag_root.is_dir():
        raise ValueError(f"{bag_root} is not a directory")
    return bag_root


def current_bag_root(settings: Settings) -> Path | None:
    if settings.fixed_bag_root is not None:
        return settings.fixed_bag_root
    bag_root = load_local_root_state(settings).current_bag_root
    if bag_root is None or not bag_root.is_dir():
        return None
    return bag_root


def db_path_for_bag_root(settings: Settings, bag_root: Path) -> Path:
    if settings.fixed_bag_root is not None:
        return settings.db_path
    return bag_root / LOCAL_APP_DIR_NAME / LOCAL_DB_FILENAME


def _state_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve()
    except OSError:
        return None


def _recent_paths(value: Any) -> list[Path]:
    if not isinstance(value, list):
        return []
    recent: list[Path] = []
    for item in value:
        path = _state_path(item)
        if path is None or path in recent:
            continue
        recent.append(path)
        if len(recent) >= LOCAL_ROOT_HISTORY_LIMIT:
            break
    return recent
