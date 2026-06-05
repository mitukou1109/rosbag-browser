from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db import connect
from app.repository import get_bag, get_topics, search_bags, update_note, update_tags

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/bags", response_class=HTMLResponse)
def list_bags(
    request: Request,
    topic: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    with connect(request.app.state.settings.db_path) as conn:
        bags = search_bags(
            conn,
            topic=_clean(topic),
            message_type=_clean(type),
            q=_clean(q),
            tag=_clean(tag),
            status=_clean(status),
        )
    return templates.TemplateResponse(
        name="bags.html",
        request=request,
        context={
            "request": request,
            "bags": bags,
            "filters": {
                "topic": topic or "",
                "type": type or "",
                "q": q or "",
                "tag": tag or "",
                "status": status or "",
            },
            "statuses": ["", "valid", "broken", "missing_files", "unreadable", "unknown"],
        },
    )


@router.get("/bags/{bag_id}", response_class=HTMLResponse)
def bag_detail(request: Request, bag_id: int) -> HTMLResponse:
    with connect(request.app.state.settings.db_path) as conn:
        bag = get_bag(conn, bag_id)
        if bag is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        topics = get_topics(conn, bag_id)
    return templates.TemplateResponse(
        name="bag_detail.html",
        request=request,
        context={"request": request, "bag": bag, "topics": topics},
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


@router.post("/bags/{bag_id}/tags")
def save_tags(
    request: Request,
    bag_id: int,
    tags: Annotated[str, Form()] = "",
) -> RedirectResponse:
    with connect(request.app.state.settings.db_path) as conn:
        if get_bag(conn, bag_id) is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        update_tags(conn, bag_id, tags)
        conn.commit()
    return RedirectResponse(url=f"/bags/{bag_id}", status_code=303)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
