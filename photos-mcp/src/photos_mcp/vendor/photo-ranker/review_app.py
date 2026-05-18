"""Standalone review web app for classified photo results."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from artifacts import DEFAULT_ARTIFACT_ROOT
from db import JobDB
from server import _build_face_items, _build_review_items

app = FastAPI(title="photo-ranker review", version="0.1.0")


class ReviewUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list)
    selected: bool = False
    note: str = ""


class FaceLabelUpdate(BaseModel):
    name: str
    register_known_face: bool = True


class ExportSelectedRequest(BaseModel):
    output_dir: str
    min_score: float = 0.0
    group_by_date: bool = False
    mode: str = "copy"


def _get_db() -> JobDB:
    return JobDB()


def _build_job_summaries(db: JobDB, limit: int = 20, status: str | None = None) -> list[dict]:
  jobs = db.list_jobs(status=status)[:limit]
  summaries: list[dict] = []
  for job in jobs:
    results = db.load_photo_results(job.id)
    assets = db.list_job_assets(job.id)
    selected_count = sum(1 for asset in assets.values() if asset.get("selected"))
    preview_path = next(
      (asset.get("preview_path", "") for asset in assets.values() if asset.get("preview_path")),
      "",
    )
    summaries.append(
      {
        "job_id": job.id,
        "source": job.source,
        "source_path": job.source_path,
        "request_options": job.request_options,
        "status": job.status.value,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "progress": job.progress.to_dict(),
        "result_summary": job.result_summary,
        "error_message": job.error_message,
        "photo_count": len(results),
        "selected_count": selected_count,
        "preview_path": preview_path,
      }
    )
  return summaries


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs_api(limit: int = 20, status: str | None = None) -> list[dict]:
  db = _get_db()
  try:
    return _build_job_summaries(db, limit=limit, status=status)
  finally:
    db.close()


@app.get("/api/jobs/{job_id}")
def get_job_api(job_id: str) -> dict:
    db = _get_db()
    try:
        for job in _build_job_summaries(db, limit=1000):
            if job["job_id"] == job_id:
                return job
        raise HTTPException(status_code=404, detail="job not found")
    finally:
        db.close()


@app.get("/api/jobs/{job_id}/items")
def get_review_items_api(
    job_id: str,
    top_n: int = 100,
    selected_only: bool = False,
) -> list[dict]:
    db = _get_db()
    try:
        return _build_review_items(db, job_id, top_n=top_n, selected_only=selected_only)
    finally:
        db.close()


@app.post("/api/jobs/{job_id}/items/{photo_id:path}/review")
def update_review_item(job_id: str, photo_id: str, payload: ReviewUpdate) -> dict:
    db = _get_db()
    try:
        return db.update_photo_review(
            job_id,
            photo_id,
            tags=payload.tags,
            selected=payload.selected,
            note=payload.note,
        )
    finally:
        db.close()


@app.get("/api/jobs/{job_id}/items/{photo_id:path}/faces")
def get_photo_faces_api(job_id: str, photo_id: str) -> list[dict]:
    db = _get_db()
    try:
        return _build_face_items(db, job_id, photo_id)
    finally:
        db.close()


@app.post("/api/jobs/{job_id}/items/{photo_id:path}/faces/{face_idx}/label")
def label_face_api(
    job_id: str,
    photo_id: str,
    face_idx: int,
    payload: FaceLabelUpdate,
) -> dict:
    db = _get_db()
    try:
        cached = db.load_face_embeddings(photo_id)
        match = next((item for item in cached if item["face_idx"] == face_idx), None)
        if not match:
            raise HTTPException(status_code=404, detail="face not found")

        db.label_face_review(job_id, photo_id, face_idx, payload.name)
        registration = None
        if payload.register_known_face:
            registration = {
                "name": payload.name,
                "face_idx": db.save_known_face(payload.name, match["embedding"]),
                "embedding_dim": len(match["embedding"]),
            }
        return {
            "job_id": job_id,
            "photo_id": photo_id,
            "face_idx": face_idx,
            "label_name": payload.name,
            "known_face_registration": registration,
        }
    finally:
        db.close()


@app.post("/api/jobs/{job_id}/export-selected")
def export_selected_api(job_id: str, payload: ExportSelectedRequest) -> dict:
    from local_writer import LocalDirectoryWriter

    db = _get_db()
    try:
        selected_items = _build_review_items(db, job_id, top_n=100000, selected_only=True)
        if not selected_items:
            return {
                "job_id": job_id,
                "selected_count": 0,
                "exported": 0,
                "message": "No selected photos found",
            }

        exportable = []
        missing_paths = []
        for item in selected_items:
            source_path = item.get("source_photo_path", "")
            if not source_path:
                missing_paths.append(item["photo_id"])
                continue
            exportable.append({**item, "photo_id": source_path})

        result = LocalDirectoryWriter().organize_by_classification(
            exportable,
            payload.output_dir,
            min_score=payload.min_score,
            group_by_date=payload.group_by_date,
            mode=payload.mode,
        )
        result["job_id"] = job_id
        result["selected_count"] = len(selected_items)
        if missing_paths:
            result["missing_source_paths"] = missing_paths
        return result
    finally:
        db.close()


@app.get("/artifacts/{job_id}/{kind}/{filename}")
def get_artifact(job_id: str, kind: str, filename: str) -> FileResponse:
    if kind not in {"previews", "faces"}:
        raise HTTPException(status_code=404, detail="artifact kind not found")
    base_dir = (DEFAULT_ARTIFACT_ROOT / job_id / kind).resolve()
    path = (base_dir / filename).resolve()
    if base_dir not in path.parents:
        raise HTTPException(status_code=403, detail="invalid artifact path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path)


@app.get("/review/{job_id}", response_class=HTMLResponse)
def review_page(job_id: str, base_path: str = "", auth_token: str = "") -> HTMLResponse:
    base_path = base_path.rstrip("/")
    html = f"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Photos Review {job_id}</title>
  <style>
    :root {{
      --bg: #f4f0e8;
      --panel: #fffaf2;
      --ink: #241c15;
      --accent: #c45f3c;
      --muted: #6d6258;
      --line: #e4d8c8;
      --chip: #efe3d2;
      --good: #23643c;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background: radial-gradient(circle at top, #fff6e7, var(--bg) 65%); color: var(--ink); }}
    header {{ padding: 24px 28px 12px; display: flex; gap: 12px; align-items: end; justify-content: space-between; }}
    h1 {{ margin: 0; font-size: 28px; }}
    .muted {{ color: var(--muted); }}
    .toolbar {{ display: flex; gap: 10px; padding: 0 28px 18px; flex-wrap: wrap; }}
    input, button, textarea {{ font: inherit; }}
    button {{ border: 1px solid var(--line); background: var(--panel); color: var(--ink); padding: 10px 14px; border-radius: 999px; cursor: pointer; }}
    button.primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 18px; padding: 0 28px 28px; }}
    .card {{ background: rgba(255,250,242,0.94); border: 1px solid var(--line); border-radius: 22px; overflow: hidden; box-shadow: 0 12px 24px rgba(36,28,21,0.08); }}
    .thumb {{ width: 100%; aspect-ratio: 4/3; object-fit: cover; background: #e9dccd; display: block; }}
    .body {{ padding: 14px; display: grid; gap: 10px; }}
    .row {{ display: flex; gap: 8px; align-items: center; justify-content: space-between; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip {{ background: var(--chip); border-radius: 999px; padding: 4px 10px; font-size: 12px; }}
    .score {{ font-weight: 700; color: var(--good); }}
    .faces {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .face {{ width: 56px; height: 56px; border-radius: 14px; object-fit: cover; border: 1px solid var(--line); cursor: pointer; }}
    .meta {{ font-size: 13px; color: var(--muted); }}
    textarea {{ width: 100%; min-height: 72px; border-radius: 14px; border: 1px solid var(--line); padding: 10px; background: white; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    dialog {{ border: 1px solid var(--line); border-radius: 18px; padding: 18px; width: min(560px, calc(100vw - 24px)); }}
    dialog::backdrop {{ background: rgba(36,28,21,0.4); }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Photos Review</h1>
      <div class="muted">job_id: {job_id}</div>
    </div>
    <button class="primary" id="export-selected">Selected Export</button>
  </header>
  <div class="toolbar">
    <button id="reload">Reload</button>
    <button id="selected-only-toggle">Selected Only: Off</button>
  </div>
  <section class="grid" id="grid"></section>
  <dialog id="face-dialog">
    <div id="face-dialog-body"></div>
    <div class="actions" style="margin-top:12px"><button onclick="document.getElementById('face-dialog').close()">Close</button></div>
  </dialog>
  <script>
    const jobId = {job_id!r};
    const basePath = {base_path!r};
    const authToken = {auth_token!r};
    let selectedOnly = false;

    function withAuth(path) {{
      if (!authToken) return path;
      const separator = path.includes('?') ? '&' : '?';
      return `${{path}}${{separator}}token=${{encodeURIComponent(authToken)}}`;
    }}

    function assetUrl(path) {{
      if (!path) return "";
      const marker = `/` + jobId + `/`;
      const idx = path.indexOf(marker);
      if (idx === -1) return "";
      const rest = path.slice(idx + marker.length);
      const parts = rest.split('/');
      return withAuth(`${{basePath}}/artifacts/${{jobId}}/${{parts[0]}}/${{parts.slice(1).join('/')}}`);
    }}

    async function loadItems() {{
      const res = await fetch(withAuth(`${{basePath}}/api/jobs/${{jobId}}/items?selected_only=${{selectedOnly}}&top_n=200`));
      const items = await res.json();
      const grid = document.getElementById('grid');
      grid.innerHTML = '';
      for (const item of items) {{
        const card = document.createElement('article');
        card.className = 'card';
        const preview = assetUrl(item.preview_path);
        card.innerHTML = `
          <img class="thumb" src="${{preview}}" alt="preview" />
          <div class="body">
            <div class="row"><strong>${{item.event_type || 'other'}}</strong><span class="score">${{Number(item.total_score || 0).toFixed(1)}}</span></div>
            <div class="meta">${{item.scene_description || ''}}</div>
            <div class="chips">
              <span class="chip">faces ${{item.faces_detected || 0}}</span>
              <span class="chip">meaningful ${{item.meaningful_score || 0}}</span>
              ${{(item.review_tags || []).map((tag) => `<span class="chip">#${{tag}}</span>`).join('')}}
            </div>
            <label><input type="checkbox" data-role="selected" ${{item.selected ? 'checked' : ''}} /> selected</label>
            <input data-role="tags" value="${{(item.review_tags || []).join(', ')}}" placeholder="family, best, print" style="width:100%;border:1px solid var(--line);border-radius:14px;padding:10px" />
            <textarea data-role="note" placeholder="메모">${{item.note || ''}}</textarea>
            <div class="faces" data-role="faces"></div>
            <div class="actions">
              <button data-role="save">Save</button>
              <button data-role="faces-btn">Faces</button>
            </div>
          </div>`;
        grid.appendChild(card);

        card.querySelector('[data-role="save"]').addEventListener('click', async () => {{
          const payload = {{
            selected: card.querySelector('[data-role="selected"]').checked,
            tags: card.querySelector('[data-role="tags"]').value.split(',').map((x) => x.trim()).filter(Boolean),
            note: card.querySelector('[data-role="note"]').value,
          }};
          await fetch(withAuth(`${{basePath}}/api/jobs/${{jobId}}/items/${{encodeURIComponent(item.photo_id)}}/review`), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify(payload),
          }});
          await loadItems();
        }});

        card.querySelector('[data-role="faces-btn"]').addEventListener('click', async () => openFaces(item.photo_id));
        await renderFaces(card.querySelector('[data-role="faces"]'), item.photo_id);
      }}
    }}

    async function renderFaces(container, photoId) {{
      const res = await fetch(withAuth(`${{basePath}}/api/jobs/${{jobId}}/items/${{encodeURIComponent(photoId)}}/faces`));
      const faces = await res.json();
      container.innerHTML = faces.map((face) => {{
        const url = assetUrl(face.crop_path);
        return url ? `<img class="face" src="${{url}}" title="${{face.label_name || 'unlabeled'}}" />` : '';
      }}).join('');
    }}

    async function openFaces(photoId) {{
      const res = await fetch(withAuth(`${{basePath}}/api/jobs/${{jobId}}/items/${{encodeURIComponent(photoId)}}/faces`));
      const faces = await res.json();
      const body = document.getElementById('face-dialog-body');
      body.innerHTML = `<h3 style="margin-top:0">Faces</h3>` + faces.map((face) => {{
        const url = assetUrl(face.crop_path);
        return `
          <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
            <img class="face" src="${{url}}" />
            <div style="flex:1">
              <div class="meta">face #${{face.face_idx}} / ${{face.expression}} / ${{face.age || '-'}} / ${{face.gender || '-'}}</div>
              <input id="face-name-${{face.face_idx}}" value="${{face.label_name || ''}}" placeholder="이름 입력" style="width:100%;border:1px solid var(--line);border-radius:12px;padding:8px" />
              <button style="margin-top:8px" onclick="labelFace(${{JSON.stringify(photoId)}}, ${{face.face_idx}})">Save Label</button>
            </div>
          </div>`;
      }}).join('');
      document.getElementById('face-dialog').showModal();
    }}

    async function labelFace(photoId, faceIdx) {{
      const input = document.getElementById(`face-name-${{faceIdx}}`);
      await fetch(withAuth(`${{basePath}}/api/jobs/${{jobId}}/items/${{encodeURIComponent(photoId)}}/faces/${{faceIdx}}/label`), {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ name: input.value, register_known_face: true }}),
      }});
      await loadItems();
      await openFaces(photoId);
    }}

    document.getElementById('reload').addEventListener('click', loadItems);
    document.getElementById('selected-only-toggle').addEventListener('click', async (event) => {{
      selectedOnly = !selectedOnly;
      event.target.textContent = `Selected Only: ${{selectedOnly ? 'On' : 'Off'}}`;
      await loadItems();
    }});
    document.getElementById('export-selected').addEventListener('click', async () => {{
      const outputDir = window.prompt('Export output directory');
      if (!outputDir) return;
      const response = await fetch(withAuth(`${{basePath}}/api/jobs/${{jobId}}/export-selected`), {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ output_dir: outputDir, group_by_date: true, mode: 'copy' }}),
      }});
      const data = await response.json();
      window.alert(`exported=${{data.copied || data.exported || 0}}, skipped=${{data.skipped || 0}}`);
    }});
    loadItems();
  </script>
</body>
</html>
"""
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("review_app:app", host="127.0.0.1", port=8765, reload=False)