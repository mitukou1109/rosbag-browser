from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import connect
from app.indexer import scan_bags

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/scan", response_class=HTMLResponse)
def scan_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        name="scan.html",
        request=request,
        context={
            "request": request,
            "result": None,
            "bag_root": request.app.state.settings.bag_root,
        },
    )


@router.post("/scan", response_class=HTMLResponse)
def run_scan(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    with connect(settings.db_path) as conn:
        result = scan_bags(conn, settings.bag_root)
    return templates.TemplateResponse(
        name="scan.html",
        request=request,
        context={"request": request, "result": result, "bag_root": settings.bag_root},
    )


@router.get("/scan/modal", response_class=HTMLResponse)
def scan_modal(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        name="scan_modal.html",
        request=request,
        context={
            "request": request,
            "result": None,
            "bag_root": request.app.state.settings.bag_root,
        },
    )


@router.post("/scan/modal/run", response_class=HTMLResponse)
def run_scan_modal(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    with connect(settings.db_path) as conn:
        result = scan_bags(conn, settings.bag_root)
    return templates.TemplateResponse(
        name="scan_modal.html",
        request=request,
        context={"request": request, "result": result, "bag_root": settings.bag_root},
    )
