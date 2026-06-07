from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import (
    Settings,
    current_bag_root,
    db_path_for_bag_root,
    load_local_root_state,
    set_local_bag_root,
)
from app.db import connect, init_db
from app.indexer import scan_bags
from app.repository import (
    add_tag,
    get_bag,
    get_last_scanned_at,
    get_topics,
    list_tags,
    remove_tags,
    search_bags,
    update_note,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/bags", response_class=HTMLResponse)
def list_bags(
    request: Request,
    topic: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    start_from: Annotated[str | None, Query()] = None,
    start_to: Annotated[str | None, Query()] = None,
    root_error: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    settings = request.app.state.settings
    bag_root = current_bag_root(settings)
    bags = []
    tags = []
    last_scanned_at = ""
    if bag_root is not None:
        with _active_db(settings) as (active_root, conn):
            bags = search_bags(
                conn,
                topic=_clean(topic),
                q=_clean(q),
                tag=_clean(tag),
                start_from=_clean(start_from),
                start_to=_clean(start_to),
                bag_root=active_root,
            )
            tags = list_tags(conn, bag_root=active_root)
            last_scanned_at = get_last_scanned_at(conn, bag_root=active_root)
    local_state = load_local_root_state(settings) if not settings.is_fixed_root else None
    return templates.TemplateResponse(
        name="bags.html",
        request=request,
        context={
            "request": request,
            "bags": bags,
            "filters": {
                "topic": topic or "",
                "q": q or "",
                "tag": tag or "",
                "start_from": start_from or "",
                "start_to": start_to or "",
            },
            "tags": tags,
            "last_scanned_at": last_scanned_at,
            "root_selector": {
                "enabled": not settings.is_fixed_root,
                "current": str(bag_root) if bag_root is not None else "",
                "recent": [str(path) for path in local_state.recent_bag_roots]
                if local_state is not None
                else [],
                "error": root_error or "",
            },
        },
    )


@router.post("/settings/bag-root")
def select_bag_root(
    request: Request,
    bag_root: Annotated[str, Form()] = "",
    recent_bag_root: Annotated[str, Form()] = "",
) -> RedirectResponse:
    settings = request.app.state.settings
    if settings.is_fixed_root:
        raise HTTPException(status_code=403, detail="BAG_ROOT is fixed")
    raw_path = recent_bag_root.strip() or bag_root.strip()
    try:
        selected_root = set_local_bag_root(settings, raw_path)
        conn = connect(db_path_for_bag_root(settings, selected_root))
        try:
            init_db(conn)
        finally:
            conn.close()
    except (ValueError, OSError, sqlite3.Error) as exc:
        query = urlencode({"root_error": str(exc)})
        return RedirectResponse(url=f"/bags?{query}", status_code=303)
    return RedirectResponse(url="/bags", status_code=303)


@router.post("/bags/scan")
def scan_bags_from_list(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if current_bag_root(settings) is None:
        query = urlencode({"root_error": "Select a bag root before scanning"})
        return RedirectResponse(url=f"/bags?{query}", status_code=303)
    with _active_db(settings) as (bag_root, conn):
        scan_bags(
            conn,
            bag_root,
            prune_by_relative_paths=not settings.is_fixed_root,
        )
    return RedirectResponse(url=_bags_referrer_path(request), status_code=303)


@router.get("/bags/{bag_id}", response_class=HTMLResponse)
def bag_detail(request: Request, bag_id: int) -> HTMLResponse:
    settings = request.app.state.settings
    with _active_db(settings) as (bag_root, conn):
        bag = get_bag(conn, bag_id, bag_root=bag_root)
        if bag is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        topics = get_topics(conn, bag_id)
        tags = list_tags(conn, bag_root=bag_root)
    return templates.TemplateResponse(
        name="bag_detail.html",
        request=request,
        context={"request": request, "bag": bag, "topics": topics, "tags": tags},
    )


@router.post("/bags/{bag_id}/note")
def save_note(
    request: Request,
    bag_id: int,
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    settings = request.app.state.settings
    with _active_db(settings) as (bag_root, conn):
        if get_bag(conn, bag_id, bag_root=bag_root) is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        update_note(conn, bag_id, note)
        conn.commit()
    return RedirectResponse(url=f"/bags/{bag_id}", status_code=303)


@router.post("/bags/{bag_id}/tags/add")
def add_bag_tag(
    request: Request,
    bag_id: int,
    tag: Annotated[str, Form()] = "",
) -> RedirectResponse:
    settings = request.app.state.settings
    with _active_db(settings) as (bag_root, conn):
        if get_bag(conn, bag_id, bag_root=bag_root) is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        if tag.strip():
            add_tag(conn, bag_id, tag)
            conn.commit()
    return RedirectResponse(url=f"/bags/{bag_id}", status_code=303)


@router.post("/bags/{bag_id}/tags/remove")
def remove_bag_tags(
    request: Request,
    bag_id: int,
    tags_to_remove: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    with _active_db(settings) as (bag_root, conn):
        if get_bag(conn, bag_id, bag_root=bag_root) is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        remove_tags(conn, bag_id, tags_to_remove or [])
        conn.commit()
    return RedirectResponse(url=f"/bags/{bag_id}", status_code=303)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@contextmanager
def _active_db(settings: Settings) -> Iterator[tuple[Path, sqlite3.Connection]]:
    bag_root = current_bag_root(settings)
    if bag_root is None:
        raise HTTPException(status_code=404, detail="Bag root is not selected")
    conn = connect(db_path_for_bag_root(settings, bag_root))
    try:
        yield bag_root, conn
    finally:
        conn.close()


def _bags_referrer_path(request: Request) -> str:
    referrer = request.headers.get("referer")
    if not referrer:
        return "/bags"
    parsed = urlsplit(referrer)
    if parsed.path != "/bags":
        return "/bags"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"/bags{query}"
