from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import connect, init_db
from app.routes import bags, scan


def create_app() -> FastAPI:
    settings = get_settings()
    with connect(settings.db_path) as conn:
        init_db(conn)

    app = FastAPI(title="ROS 2 Bag Browser")
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(bags.router)
    app.include_router(scan.router)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/bags", status_code=303)

    return app


app = create_app()
