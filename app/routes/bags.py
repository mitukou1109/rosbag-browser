from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db import connect
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
) -> HTMLResponse:
    settings = request.app.state.settings
    with connect(request.app.state.settings.db_path) as conn:
        bags = search_bags(
            conn,
            topic=_clean(topic),
            q=_clean(q),
            tag=_clean(tag),
            start_from=_clean(start_from),
            start_to=_clean(start_to),
            bag_root=settings.bag_root,
        )
        tags = list_tags(conn)
        last_scanned_at = get_last_scanned_at(conn)
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
        },
    )


@router.post("/bags/scan")
def scan_bags_from_list(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    with connect(settings.db_path) as conn:
        scan_bags(conn, settings.bag_root)
    return RedirectResponse(url=_bags_referrer_path(request), status_code=303)


@router.get("/bags/{bag_id}", response_class=HTMLResponse)
def bag_detail(request: Request, bag_id: int) -> HTMLResponse:
    settings = request.app.state.settings
    with connect(request.app.state.settings.db_path) as conn:
        bag = get_bag(conn, bag_id, bag_root=settings.bag_root)
        if bag is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        topics = get_topics(conn, bag_id)
        tags = list_tags(conn)
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
    with connect(request.app.state.settings.db_path) as conn:
        if get_bag(conn, bag_id) is None:
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
    with connect(request.app.state.settings.db_path) as conn:
        if get_bag(conn, bag_id) is None:
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
    with connect(request.app.state.settings.db_path) as conn:
        if get_bag(conn, bag_id) is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        remove_tags(conn, bag_id, tags_to_remove or [])
        conn.commit()
    return RedirectResponse(url=f"/bags/{bag_id}", status_code=303)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bags_referrer_path(request: Request) -> str:
    referrer = request.headers.get("referer")
    if not referrer:
        return "/bags"
    parsed = urlsplit(referrer)
    if parsed.path != "/bags":
        return "/bags"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"/bags{query}"
