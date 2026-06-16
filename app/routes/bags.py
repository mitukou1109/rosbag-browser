from __future__ import annotations

import binascii
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import sqlite3
import struct
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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
ZIP_CHUNK_SIZE = 1024 * 1024
ZIP32_LIMIT = 0xFFFFFFFF
ZIP16_LIMIT = 0xFFFF


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


@router.get("/bags/{bag_id}/download")
def download_bag(request: Request, bag_id: int) -> StreamingResponse:
    settings = request.app.state.settings
    with _active_db(settings) as (bag_root, conn):
        bag = get_bag(conn, bag_id, bag_root=bag_root)
        if bag is None:
            raise HTTPException(status_code=404, detail="Bag not found")
        bag_dir = _bag_directory_path(bag, bag_root)
    return StreamingResponse(
        _iter_bag_archive(bag_dir),
        media_type="application/zip",
        headers={"Content-Disposition": _download_content_disposition(bag_dir.name)},
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


def _bag_directory_path(bag: dict[str, object], bag_root: Path) -> Path:
    root = bag_root.resolve()
    relative_path = bag.get("root_relative_path")
    if relative_path:
        bag_dir = (root / str(relative_path)).resolve()
    else:
        path_value = bag.get("path")
        if not path_value:
            raise HTTPException(status_code=404, detail="Bag files not found")
        bag_dir = Path(str(path_value)).resolve()
    try:
        bag_dir.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Bag files not found") from exc
    if not bag_dir.is_dir():
        raise HTTPException(status_code=404, detail="Bag files not found")
    return bag_dir


def _iter_bag_archive(bag_dir: Path) -> Iterator[bytes]:
    root = bag_dir.resolve()
    offset = 0
    central_directory: list[dict[str, object]] = []
    for path in sorted(bag_dir.rglob("*")):
        if not path.is_file():
            continue
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(root)
        except ValueError:
            continue
        archive_name = (Path(bag_dir.name) / path.relative_to(bag_dir)).as_posix()
        stat_result = path.stat()
        size = stat_result.st_size
        local_header_offset = offset
        local_header = _zip_local_file_header(
            archive_name,
            size=size,
            mtime=stat_result.st_mtime,
        )
        yield local_header
        offset += len(local_header)

        crc = 0
        with path.open("rb") as handle:
            while chunk := handle.read(ZIP_CHUNK_SIZE):
                crc = binascii.crc32(chunk, crc)
                yield chunk
                offset += len(chunk)
        crc &= ZIP32_LIMIT

        data_descriptor = _zip_data_descriptor(crc=crc, size=size)
        yield data_descriptor
        offset += len(data_descriptor)

        central_directory.append(
            {
                "name": archive_name,
                "crc": crc,
                "size": size,
                "mtime": stat_result.st_mtime,
                "offset": local_header_offset,
            }
        )

    central_directory_offset = offset
    for entry in central_directory:
        header = _zip_central_directory_header(entry)
        yield header
        offset += len(header)
    central_directory_size = offset - central_directory_offset

    footer = _zip_end_of_central_directory(
        entries=len(central_directory),
        central_directory_size=central_directory_size,
        central_directory_offset=central_directory_offset,
        current_offset=offset,
    )
    yield footer


def _zip_local_file_header(name: str, *, size: int, mtime: float) -> bytes:
    name_bytes = name.encode("utf-8")
    dos_time, dos_date = _zip_dos_datetime(mtime)
    use_zip64 = size >= ZIP32_LIMIT
    extra = _zip64_extra_field(size, size) if use_zip64 else b""
    return struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        45 if use_zip64 else 20,
        0x0808,
        0,
        dos_time,
        dos_date,
        0,
        ZIP32_LIMIT if use_zip64 else 0,
        ZIP32_LIMIT if use_zip64 else 0,
        len(name_bytes),
        len(extra),
    ) + name_bytes + extra


def _zip_data_descriptor(*, crc: int, size: int) -> bytes:
    if size >= ZIP32_LIMIT:
        return struct.pack("<IIQQ", 0x08074B50, crc, size, size)
    return struct.pack("<IIII", 0x08074B50, crc, size, size)


def _zip_central_directory_header(entry: dict[str, object]) -> bytes:
    name = str(entry["name"])
    name_bytes = name.encode("utf-8")
    size = int(entry["size"])
    offset = int(entry["offset"])
    dos_time, dos_date = _zip_dos_datetime(float(entry["mtime"]))
    use_zip64 = size >= ZIP32_LIMIT or offset >= ZIP32_LIMIT
    extra = _zip64_extra_field(
        size if size >= ZIP32_LIMIT else None,
        size if size >= ZIP32_LIMIT else None,
        offset if offset >= ZIP32_LIMIT else None,
    )
    return struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        (3 << 8) | 45,
        45 if use_zip64 else 20,
        0x0808,
        0,
        dos_time,
        dos_date,
        int(entry["crc"]),
        ZIP32_LIMIT if size >= ZIP32_LIMIT else size,
        ZIP32_LIMIT if size >= ZIP32_LIMIT else size,
        len(name_bytes),
        len(extra),
        0,
        0,
        0,
        0,
        ZIP32_LIMIT if offset >= ZIP32_LIMIT else offset,
    ) + name_bytes + extra


def _zip_end_of_central_directory(
    *,
    entries: int,
    central_directory_size: int,
    central_directory_offset: int,
    current_offset: int,
) -> bytes:
    use_zip64 = (
        entries >= ZIP16_LIMIT
        or central_directory_size >= ZIP32_LIMIT
        or central_directory_offset >= ZIP32_LIMIT
    )
    if not use_zip64:
        return struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            entries,
            entries,
            central_directory_size,
            central_directory_offset,
            0,
        )

    zip64_end_offset = current_offset
    zip64_end = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        44,
        45,
        45,
        0,
        0,
        entries,
        entries,
        central_directory_size,
        central_directory_offset,
    )
    zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, zip64_end_offset, 1)
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        ZIP16_LIMIT,
        ZIP16_LIMIT,
        ZIP32_LIMIT,
        ZIP32_LIMIT,
        0,
    )
    return zip64_end + zip64_locator + end


def _zip64_extra_field(*values: int | None) -> bytes:
    data = b"".join(struct.pack("<Q", value) for value in values if value is not None)
    if not data:
        return b""
    return struct.pack("<HH", 0x0001, len(data)) + data


def _zip_dos_datetime(timestamp: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(timestamp)
    year = min(max(dt.year, 1980), 2107)
    dos_time = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    dos_date = ((year - 1980) << 9) | (dt.month << 5) | dt.day
    return dos_time, dos_date


def _download_content_disposition(bag_name: str) -> str:
    filename = f"{bag_name}.zip"
    fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in '"\\' else "_"
        for char in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


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
