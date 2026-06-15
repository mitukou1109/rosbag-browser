from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import current_bag_root, db_path_for_bag_root, get_settings
from app.db import connect, init_db
from app.routes import bags


def create_app() -> FastAPI:
    settings = get_settings()
    bag_root = current_bag_root(settings)
    if bag_root is not None:
        _init_active_db(db_path_for_bag_root(settings, bag_root))

    app = FastAPI(title="rosbag Browser")
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(bags.router)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/bags", status_code=303)

    return app


def _init_active_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        init_db(conn)


app = create_app()
