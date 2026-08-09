from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import types

from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


def _load_artifacts_module():
    prepare_vendor_runtime("photo-ranker")
    module = importlib.import_module("photos_mcp_vendor_photo_ranker.artifacts")
    return importlib.reload(module)


def test_save_job_results_writes_results_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PHOTO_RANKER_RUNTIME_ROOT", str(tmp_path))
    pil_module = types.ModuleType("PIL")
    pil_module.Image = object
    monkeypatch.setitem(sys.modules, "PIL", pil_module)
    artifacts = _load_artifacts_module()

    saved_path = artifacts.save_job_results(
        "job-1234",
        job={"id": "job-1234", "status": "completed"},
        summary={"selected_count": 0, "selection_profile": "general"},
        results=[{"photo_id": "photo-a", "total_score": 42.5}],
        assets={
            "photo-a": {
                "preview_path": "/tmp/preview.jpg",
                "source_photo_path": "/tmp/source.jpg",
                "selected": False,
                "note": "",
                "tags": ["auto-curated"],
            }
        },
    )

    path = Path(saved_path)
    assert path == tmp_path / "artifacts" / "job-1234" / "results.json"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["job_id"] == "job-1234"
    assert payload["summary"]["selected_count"] == 0
    assert payload["results"][0]["photo_id"] == "photo-a"
    assert payload["results"][0]["preview_path"] == "/tmp/preview.jpg"
    assert payload["results"][0]["source_photo_path"] == "/tmp/source.jpg"
    assert payload["results"][0]["selected"] is False
    assert payload["results"][0]["tags"] == ["auto-curated"]