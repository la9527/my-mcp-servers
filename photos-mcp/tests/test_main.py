from __future__ import annotations

from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import json

from photos_mcp.app.config import load_config
from photos_mcp.app.main import run_cli
from photos_mcp.interfaces.mcp.server import build_health_payload, build_http_app, build_server
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore
from photos_mcp.app.single_instance import AlreadyRunningError
from photos_mcp.infrastructure.vendor_adapter.loader import VENDOR_ROOT, load_vendor_server, prepare_vendor_runtime, resolve_vendor_root


def test_health_mode_returns_expected_payload(capsys) -> None:
    exit_code = run_cli(["--health"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"status": "ok"' in captured.out
    assert '"app_name": "PhotosMcp"' in captured.out
    assert '"endpoint": "http://127.0.0.1:18791/mcp"' in captured.out


def test_runtime_import_smoke_prepares_osxphotos(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "photos_mcp.app.main.prepare_photos_library_runtime",
        lambda: calls.append("osxphotos"),
    )

    exit_code = run_cli(["--runtime-import-smoke"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == ["osxphotos"]
    assert '"runtime": "osxphotos"' in captured.out


def test_vendor_runtime_smoke_loads_photo_source_and_pyobjc(monkeypatch, capsys) -> None:
    calls: list[str] = []
    prepared: list[str] = []
    imported: list[str] = []

    monkeypatch.setattr(
        "photos_mcp.app.main.load_vendor_server",
        lambda name: calls.append(name),
    )
    monkeypatch.setattr(
        "photos_mcp.app.main.prepare_vendor_runtime",
        lambda name: prepared.append(name),
    )
    monkeypatch.setitem(sys.modules, "FSEvents", object())
    monkeypatch.setitem(sys.modules, "osxphotos", object())
    monkeypatch.setitem(sys.modules, "Vision", object())
    monkeypatch.setattr(
        "photos_mcp.app.main.importlib.import_module",
        lambda name: imported.append(name),
    )

    exit_code = run_cli(["--vendor-runtime-smoke"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == ["photo-source"]
    assert prepared == ["photo-ranker"]
    assert imported == ["photos_mcp_vendor_photo_ranker.scene_selection"]
    assert '"runtime": "photo-source"' in captured.out
    assert '"scene_runtime": "photo-ranker-vision"' in captured.out


def test_run_cli_returns_locked_error_when_instance_is_already_running(monkeypatch, capsys) -> None:
    @contextmanager
    def raise_locked(_config):
        raise AlreadyRunningError("PhotosMcp is already running.")
        yield

    def fail_run_menu_app(*_args, **_kwargs):
        raise AssertionError("run_menu_app should not run when lock acquisition fails")

    monkeypatch.setattr("photos_mcp.app.main.acquire_single_instance_lock", raise_locked)
    monkeypatch.setattr("photos_mcp.app.main.run_menu_app", fail_run_menu_app)

    exit_code = run_cli([])
    captured = capsys.readouterr()

    assert exit_code == 75
    assert "already running" in captured.err.lower()


def test_single_instance_lock_rejects_second_process(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env["PHOTOS_MCP_RUNTIME_ROOT"] = str(tmp_path)

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys
                from photos_mcp.app.config import load_config
                from photos_mcp.app.single_instance import acquire_single_instance_lock

                with acquire_single_instance_lock(load_config()):
                    print('locked', flush=True)
                    sys.stdin.read()
                """
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        checker = subprocess.run(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    from photos_mcp.app.config import load_config
                    from photos_mcp.app.single_instance import AlreadyRunningError, acquire_single_instance_lock

                    try:
                        with acquire_single_instance_lock(load_config()):
                            raise SystemExit(0)
                    except AlreadyRunningError as exc:
                        print(str(exc))
                        raise SystemExit(75)
                    """
                ),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        assert checker.returncode == 75
        assert "already running" in checker.stdout.lower()
    finally:
        if holder.stdin is not None:
            holder.stdin.close()
        holder.wait(timeout=5)


def test_build_server_registers_facade_tools() -> None:
    mcp = build_server()

    assert sorted(mcp._tool_manager._tools) == [
        "photos_query",
        "photos_select",
        "photos_workflow",
        "photos_write",
    ]


def test_build_health_payload_reflects_state_store_status() -> None:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")
    state_store.run_repository.upsert_browser_mission_run(
        {
            "mission_run_id": "browser-mission-health",
            "status": "completed",
            "control_policy": "qwen-agent",
            "last_stage": "completed",
            "model_metrics": {"request_count": 4, "total_tokens": 321},
        }
    )

    payload = build_health_payload(load_config(), state_store)

    assert payload["status"] == "ok"
    assert payload["daemon_status"] == "ready"
    assert payload["preflight_status"] == "pending"
    assert payload["transport"]["status"] == "ok"
    assert payload["transport"]["daemon_status"] == "ready"
    assert payload["capabilities"]["status"] == "pending"
    assert payload["capabilities"]["latest_browser_mission"]["mission_run_id"] == (
        "browser-mission-health"
    )
    assert payload["capabilities"]["latest_browser_mission"]["model_metrics"][
        "total_tokens"
    ] == 321
    assert payload["endpoint"] == "http://127.0.0.1:18791/mcp"


def test_build_http_app_serves_health_endpoint() -> None:
    from starlette.testclient import TestClient

    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
    )
    state_store.set_daemon_status("ready")
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        response = client.get(config.health_path)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["daemon_status"] == "ready"
    assert body["transport"]["status"] == "ok"
    assert body["capabilities"]["status"] == "pending"


def test_build_http_app_serves_capabilities_endpoint() -> None:
    from starlette.testclient import TestClient

    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
    )
    state_store.set_daemon_status("ready")
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        response = client.get(f"{config.health_path}/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["checks"] == []


def test_build_http_app_serves_read_only_user_action_page() -> None:
    from starlette.testclient import TestClient

    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
    )
    state_store.run_repository.save_user_action_request(
        {
            "request_id": "action-http-1",
            "dedupe_key": "picker:http-1",
            "request_type": "google_picker_selection",
            "provider": "google_photos",
            "status": "pending",
            "title": "Google Photos 선택이 필요합니다",
            "message": "최근 사진을 확인해 주세요.",
            "action_url": "http://127.0.0.1:18791/actions/action-http-1",
            "expires_at": "2026-09-04T00:00:00+00:00",
            "created_at": "2026-09-03T00:00:00+00:00",
        }
    )
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        response = client.get("/actions/action-http-1")
        missing = client.get("/actions/missing")

    assert response.status_code == 200
    assert "Google Photos 선택이 필요합니다" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert missing.status_code == 404


def test_build_http_app_daily_curate_trigger_is_read_only_and_bounded(monkeypatch) -> None:
    from starlette.testclient import TestClient
    import photos_mcp.interfaces.mcp.server as server_module

    calls = []

    async def fake_workflow(**kwargs):
        calls.append(kwargs)
        return {"run_id": "daily-http-1", "status": "pending", "terminal": False}

    monkeypatch.setattr(server_module, "facade_photos_workflow", fake_workflow)
    config = load_config()
    state_store = PhotosMcpStateStore(endpoint=config.endpoint, health_endpoint=config.health_endpoint)
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        response = client.post(
            "/automation/daily-curate",
            json={
                "source": "apple",
                "limit": 9999,
                "action_base_url": "https://photos-mac.tail123.ts.net/photos-actions",
            },
        )
        invalid = client.post("/automation/daily-curate", json={"source": "gcs"})
        unsafe = client.post(
            "/automation/daily-curate",
            json={"source": "google", "action_base_url": "https://public.example.com/actions"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert calls[0]["action"] == "daily_curate"
    assert calls[0]["options"]["limit"] == 500
    assert calls[0]["options"]["mode"] == "review_only"
    assert calls[0]["options"]["action_base_url"] == "https://photos-mac.tail123.ts.net/photos-actions"
    assert "target_album_name" not in calls[0]["options"]
    assert invalid.status_code == 400
    assert unsafe.status_code == 400


def test_build_http_app_reconciles_recommendations_on_loopback(monkeypatch) -> None:
    from starlette.testclient import TestClient
    import photos_mcp.interfaces.mcp.server as server_module

    calls = []

    async def fake_reconcile(**kwargs):
        calls.append(kwargs)
        return {
            "status": "completed",
            "completed_run_count": 1,
            "new_file_count": 2,
        }

    monkeypatch.setattr(server_module, "reconcile_pending_recommendations", fake_reconcile)
    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
    )
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        response = client.post("/automation/reconcile-recommendations", json={})

    assert response.status_code == 200
    assert response.json()["new_file_count"] == 2
    assert calls == [{"repository": state_store.run_repository}]


def test_owner_can_refresh_story_and_cross_site_request_is_blocked(monkeypatch) -> None:
    from starlette.testclient import TestClient
    import photos_mcp.interfaces.mcp.server as server_module

    calls = []

    async def fake_refresh(repository, **kwargs):
        calls.append((repository, kwargs))
        return {"status": "ready"}

    monkeypatch.setattr(server_module, "refresh_recommendation_story", fake_refresh)
    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
    )
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        blocked = client.post(
            "/photos/story/refresh",
            headers={"Origin": "https://attacker.example"},
        )
        refreshed = client.post(
            "/photos/story/refresh",
            follow_redirects=False,
        )

    assert blocked.status_code == 403
    assert refreshed.status_code == 303
    assert refreshed.headers["location"] == "/photos"
    assert calls == [(state_store.run_repository, {"force": True})]


def test_owner_story_creates_30_day_share_derivatives_and_blocks_cross_site_post(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datetime import datetime
    from PIL import Image
    from starlette.testclient import TestClient

    recommendation_root = tmp_path / "recommendations"
    source = recommendation_root / "2026" / "2026-09-06" / "pick.jpg"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (2200, 1400), "#526c68").save(source)
    monkeypatch.setenv("PHOTOS_MCP_RECOMMENDATION_ROOT", str(recommendation_root))
    monkeypatch.setenv("PHOTOS_MCP_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("PHOTOS_MCP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv(
        "PHOTOS_MCP_SHARE_SESSION_SECRET",
        "owner-route-session-secret-with-at-least-32-bytes",
    )
    config = load_config()
    state_store = PhotosMcpStateStore(
        endpoint=config.endpoint,
        health_endpoint=config.health_endpoint,
        repository_path=tmp_path / "jobs.db",
    )
    state_store.run_repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": "local-owner-route-asset",
            "content_hash": "b" * 64,
            "relative_path": "2026/2026-09-06/pick.jpg",
            "mime_type": "image/jpeg",
            "byte_size": source.stat().st_size,
            "capture_date_local": "2026-09-06",
        }
    )
    app = build_http_app(config=config, state_store=state_store)

    with TestClient(app) as client:
        owner = client.get("/photos")
        blocked = client.post(
            "/photos/share",
            data={"duration_days": "30", "download_enabled": "1"},
            headers={"Origin": "https://attacker.example"},
        )
        created = client.post(
            "/photos/share",
            data={"duration_days": "30", "download_enabled": "1"},
        )

    assert owner.status_code == 200
    assert "공유 만들기" in owner.text
    assert blocked.status_code == 403
    assert created.status_code == 201
    assert "잠금 코드" in created.text
    packages = state_store.run_repository.list_shared_story_packages()
    assert len(packages) == 1
    assert packages[0]["download_enabled"] is True
    created_at = datetime.fromisoformat(packages[0]["created_at"])
    expires_at = datetime.fromisoformat(packages[0]["expires_at"])
    assert (expires_at - created_at).days == 30
    derivatives = list((tmp_path / "cache" / "shared-story-assets").rglob("*.jpg"))
    assert {path.name.split("-")[0] for path in derivatives} == {"thumb", "preview", "download"}


def test_load_vendor_server_uses_package_namespace_for_photo_ranker(monkeypatch) -> None:
    server_root = str(VENDOR_ROOT / "photo-ranker")
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if entry != server_root],
    )
    for module_name in [
        "sources",
        "models",
        "photos_mcp_vendor_photo_ranker",
        "photos_mcp_vendor_photo_ranker.server",
    ]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    module = load_vendor_server("photo-ranker")

    assert module.__name__ == "photos_mcp_vendor_photo_ranker.server"
    assert module.Pipeline.__module__ == "photos_mcp_vendor_photo_ranker.pipeline"
    assert "sources" not in sys.modules
    assert "models" not in sys.modules
    assert server_root not in sys.path


def test_load_vendor_server_uses_package_namespace_for_photo_source(monkeypatch, tmp_path: Path) -> None:
    source_root = str(VENDOR_ROOT / "photo-source")
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if entry != source_root],
    )
    for module_name in [
        "sources",
        "models",
        "photos_mcp_vendor_photo_source",
        "photos_mcp_vendor_photo_source.server",
    ]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    module = load_vendor_server("photo-source")
    local_source = module._get_local_source(str(tmp_path))

    assert module.__name__ == "photos_mcp_vendor_photo_source.server"
    assert local_source.__class__.__module__ == "photos_mcp_vendor_photo_source.sources.local_folder"
    assert "sources" not in sys.modules
    assert "models" not in sys.modules
    assert source_root not in sys.path


def test_load_vendor_server_reuses_same_module_instance() -> None:
    first = load_vendor_server("photo-ranker")
    second = load_vendor_server("photo-ranker")

    assert second is first


def test_prepare_vendor_runtime_keeps_vendors_out_of_top_level_namespace(monkeypatch) -> None:
    photo_ranker_root = str(VENDOR_ROOT / "photo-ranker")
    photo_source_root = str(VENDOR_ROOT / "photo-source")
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if entry not in {photo_ranker_root, photo_source_root}],
    )
    for module_name in [
        "sources",
        "sources.apple_photos",
        "models",
        "photos_mcp_vendor_photo_source",
        "photos_mcp_vendor_photo_source.server",
        "photos_mcp_vendor_photo_ranker",
        "photos_mcp_vendor_photo_ranker.server",
    ]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    prepare_vendor_runtime("photo-ranker")
    ranker_package = importlib.import_module("photos_mcp_vendor_photo_ranker")

    prepare_vendor_runtime("photo-source")
    source_package = importlib.import_module("photos_mcp_vendor_photo_source")

    assert ranker_package.__path__ == [str(VENDOR_ROOT / "photo-ranker")]
    assert source_package.__path__ == [str(VENDOR_ROOT / "photo-source")]
    assert "sources" not in sys.modules
    assert "models" not in sys.modules
    assert photo_ranker_root not in sys.path
    assert photo_source_root not in sys.path


def test_resolve_vendor_root_falls_back_to_bundled_resource_layout(tmp_path: Path) -> None:
    package_root = tmp_path / "Contents" / "Resources" / "lib" / "python3.12" / "photos_mcp"
    bundled_vendor_root = tmp_path / "Contents" / "Resources" / "lib" / "photos_mcp" / "vendor"
    bundled_vendor_root.mkdir(parents=True)

    resolved = resolve_vendor_root(package_root)

    assert resolved == bundled_vendor_root
