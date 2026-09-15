"""Loopback-only preview of the current renderer using a read-only Story snapshot.

Run with PYTHONPATH=src .venv/bin/python scripts/preview_story_design.py.
Existing authenticated owner asset routes supply derivatives; no analysis runs
or presentation writes are made. The preview has no POST handlers.
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import uvicorn
from PIL import Image
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.routing import Route

from photos_mcp.interfaces.http.story_web import (
    PUBLIC_HEADERS,
    STORY_CSS,
    STORY_JS,
    render_story,
    swiper_asset_path,
)


def load_story() -> dict:
    path = Path.home() / ".photos-mcp/runtime/photo-ranker/jobs.db"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT manifest_json FROM story_manifests ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("No saved Story is available for preview")
    return json.loads(row[0])


story = load_story()
asset_ids = {str(photo.get("asset_id")) for photo in story.get("photos", [])}


async def page(request):
    theme = request.query_params.get("theme", "scroll_cinema")
    mode = request.query_params.get("mode", "")
    snapshot = dict(story)
    selected_date = request.query_params.get("date", "")
    if selected_date:
        snapshot["photos"] = [p for p in story["photos"] if p.get("capture_date") == selected_date]
        snapshot["chapters"] = [c for c in story["chapters"] if c.get("date") == selected_date]
        snapshot["title"] = selected_date + "의 기억"
        snapshot["subtitle"] = "사진 속에 남은 하루를 다시 펼쳐봅니다."
        snapshot["date_from"] = selected_date
        snapshot["date_to"] = selected_date
    if mode == "empty":
        snapshot.update(photos=[], chapters=[])
    if mode == "single":
        snapshot.update(photos=story["photos"][:1], chapters=[])
    return HTMLResponse(render_story(snapshot, public=False, presentation={"theme_id": theme}, theme_action="/preview"), headers=PUBLIC_HEADERS)


async def asset(request):
    asset_id, kind = request.path_params["asset_id"], request.path_params["kind"]
    if asset_id not in asset_ids or kind not in {"thumb", "gallery", "preview"}:
        return Response(status_code=404)
    def fetch():
        upstream = "preview" if kind == "gallery" else kind
        with urlopen(f"http://127.0.0.1:18791/photos/assets/{asset_id}/{upstream}", timeout=20) as response:
            body = response.read()
            if kind == "gallery":
                with Image.open(io.BytesIO(body)) as image:
                    image.thumbnail((768, 768), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    image.convert("RGB").save(output, format="JPEG", quality=88, exif=b"")
                    body = output.getvalue()
            return Response(body, media_type=response.headers.get("Content-Type"), headers={"Cache-Control": "private, max-age=600"})
    try:
        return await run_in_threadpool(fetch)
    except HTTPError as error:
        return Response(status_code=error.code)


async def static(request):
    name = request.path_params["name"]
    if name == "story.css":
        return Response(STORY_CSS, media_type="text/css")
    if name == "story.js":
        return Response(STORY_JS, media_type="application/javascript")
    vendor = swiper_asset_path(name)
    return FileResponse(vendor) if vendor else Response(status_code=404)


app = Starlette(routes=[Route("/preview", page), Route("/photos/assets/{asset_id}/{kind}", asset), Route("/story-assets/{name}", static)])
if __name__ == "__main__":
    print(f"Read-only preview: {len(asset_ids)} photos at http://127.0.0.1:18809/preview")
    uvicorn.run(app, host="127.0.0.1", port=18809, access_log=False)
