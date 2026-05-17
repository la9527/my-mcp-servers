from __future__ import annotations

from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import json

from photos_mcp.config import load_config
from photos_mcp.main import run_cli
from photos_mcp.legacy_loader import LEGACY_ROOT, load_legacy_server, prepare_legacy_runtime
from photos_mcp.server import build_health_payload, build_http_app, build_server
from photos_mcp.state import PhotosMcpStateStore
from photos_mcp.single_instance import AlreadyRunningError


def test_health_mode_returns_expected_payload(capsys) -> None:
    exit_code = run_cli(["--health"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"status": "ok"' in captured.out
    assert '"app_name": "PhotosMcp"' in captured.out
    assert '"endpoint": "http://127.0.0.1:18791/mcp"' in captured.out


def test_run_cli_returns_locked_error_when_instance_is_already_running(monkeypatch, capsys) -> None:
    @contextmanager
    def raise_locked(_config):
        raise AlreadyRunningError("PhotosMcp is already running.")
        yield

    def fail_run_menu_app(*_args, **_kwargs):
        raise AssertionError("run_menu_app should not run when lock acquisition fails")

    monkeypatch.setattr("photos_mcp.main.acquire_single_instance_lock", raise_locked)
    monkeypatch.setattr("photos_mcp.main.run_menu_app", fail_run_menu_app)

    exit_code = run_cli([])
    captured = capsys.readouterr()

    assert exit_code == 75
    assert "already running" in captured.err.lower()


def test_single_instance_lock_rejects_second_process(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env["NANOBOT_PHOTOS_MCP_RUNTIME_ROOT"] = str(tmp_path)

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys
                from photos_mcp.config import load_config
                from photos_mcp.single_instance import acquire_single_instance_lock

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
                    from photos_mcp.config import load_config
                    from photos_mcp.single_instance import AlreadyRunningError, acquire_single_instance_lock

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


def test_build_server_registers_health_tool() -> None:
    mcp = build_server()

    assert "health_status" in mcp._tool_manager._tools


def test_build_server_registers_legacy_photo_tools() -> None:
    mcp = build_server()

    assert "list_photos" in mcp._tool_manager._tools
    assert "get_metadata" in mcp._tool_manager._tools
    assert "score_quality" in mcp._tool_manager._tools
    assert "classify_and_organize" in mcp._tool_manager._tools


def test_build_health_payload_reflects_state_store_status() -> None:
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:18791/mcp",
        health_endpoint="http://127.0.0.1:18791/health",
    )
    state_store.set_daemon_status("ready")

    payload = build_health_payload(load_config(), state_store)

    assert payload["status"] == "ok"
    assert payload["daemon_status"] == "ready"
    assert payload["preflight_status"] == "pending"
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


def test_load_legacy_server_keeps_photo_ranker_runtime_imports_available(monkeypatch) -> None:
    server_root = str(LEGACY_ROOT / "photo-ranker")
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if entry != server_root],
    )
    monkeypatch.delitem(sys.modules, "sources", raising=False)
    monkeypatch.delitem(sys.modules, "models", raising=False)
    monkeypatch.delitem(sys.modules, "photos_mcp_legacy_photo_ranker", raising=False)

    load_legacy_server("photo-ranker")
    sources_module = importlib.import_module("sources")
    models_module = importlib.import_module("models")

    assert Path(sources_module.__file__).resolve().parent == LEGACY_ROOT / "photo-ranker"
    assert Path(models_module.__file__).resolve() == LEGACY_ROOT / "photo-ranker" / "models.py"


def test_prepare_legacy_runtime_switches_sources_namespace_between_servers(monkeypatch) -> None:
    photo_ranker_root = str(LEGACY_ROOT / "photo-ranker")
    photo_source_root = str(LEGACY_ROOT / "photo-source")
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if entry not in {photo_ranker_root, photo_source_root}],
    )
    for module_name in ["sources", "sources.apple_photos", "models"]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    prepare_legacy_runtime("photo-ranker")
    ranker_sources = importlib.import_module("sources")
    ranker_models = importlib.import_module("models")

    prepare_legacy_runtime("photo-source")
    source_package = importlib.import_module("sources")
    apple_module = importlib.import_module("sources.apple_photos")
    source_models = importlib.import_module("models")

    assert Path(ranker_sources.__file__).resolve() == LEGACY_ROOT / "photo-ranker" / "sources.py"
    assert Path(ranker_models.__file__).resolve() == LEGACY_ROOT / "photo-ranker" / "models.py"
    assert Path(source_package.__file__).resolve() == LEGACY_ROOT / "photo-source" / "sources" / "__init__.py"
    assert Path(apple_module.__file__).resolve() == LEGACY_ROOT / "photo-source" / "sources" / "apple_photos.py"
    assert Path(source_models.__file__).resolve() == LEGACY_ROOT / "photo-source" / "models.py"