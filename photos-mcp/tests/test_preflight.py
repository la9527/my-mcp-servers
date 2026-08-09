from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import photos_mcp.application.preflight_service as preflight
from photos_mcp.application.preflight_service import (
    CHECK_ERROR,
    CHECK_OK,
    CHECK_WARNING,
    aggregate_check_status,
    check_photos_automation_access,
    check_photos_library_readability,
    check_photos_library_metadata_access,
    check_photos_permission_access,
    check_photos_thumbnail_access,
    run_preflight_check,
    run_startup_checks,
)


def test_check_photos_library_readability_reports_success(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit == 1
            return [SimpleNamespace(photo_id="photo-1")]

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_library_readability()

    assert result.status == CHECK_OK
    assert "photo-1" in result.detail


def test_check_photos_library_readability_reports_failure(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            raise RuntimeError("photos db unavailable")

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_library_readability()

    assert result.status == CHECK_ERROR
    assert result.detail == "photos db unavailable"


def test_check_photos_library_metadata_access_uses_read_only_database(
    monkeypatch,
    tmp_path,
) -> None:
    library_path = tmp_path / "Photos Library.photoslibrary"
    database_path = library_path / "database" / "Photos.sqlite"
    database_path.parent.mkdir(parents=True)
    connection = preflight.sqlite3.connect(database_path)
    connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    connection.close()
    monkeypatch.setattr(preflight, "_resolve_photos_library_path", lambda: library_path)

    result = check_photos_library_metadata_access()

    assert result.status == CHECK_OK
    assert "read-only" in result.detail


def test_resolve_photos_library_path_prefers_explicit_override(
    monkeypatch,
    tmp_path,
) -> None:
    library_path = tmp_path / "External.photoslibrary"
    library_path.mkdir()
    monkeypatch.setenv("PHOTOS_MCP_PHOTOS_LIBRARY_PATH", str(library_path))

    assert preflight._resolve_photos_library_path() == library_path.resolve()


def test_check_photos_automation_access_downgrades_permission_error(monkeypatch) -> None:
    class FakeWriter:
        def list_albums(self):
            raise RuntimeError("run_script failed: Not authorized to send Apple events to Photos. (-1743)")

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_automation_access()

    assert result.status == CHECK_WARNING
    assert "automation" in result.hint.lower()


def test_check_photos_automation_access_reports_success(monkeypatch) -> None:
    class FakeWriter:
        def list_albums(self):
            return [{"name": "album-1"}, {"name": "album-2"}]

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_automation_access()

    assert result.status == CHECK_OK
    assert "2 albums" in result.detail


def test_check_photos_automation_access_prefers_lightweight_probe(monkeypatch) -> None:
    class FakeWriter:
        def probe_automation_access(self):
            return {"album_count": 2, "sample_album": "album-1"}

        def list_albums(self):
            raise AssertionError("preflight should not enumerate full album counts")

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_automation_access()

    assert result.status == CHECK_OK
    assert "2 albums" in result.detail
    assert "album-1" in result.detail


def test_check_photos_permission_access_requests_photokit_authorization(monkeypatch) -> None:
    calls: list[bool] = []

    class FakeSource:
        def probe_photokit_permission(self, *, request_if_needed: bool = False):
            calls.append(request_if_needed)
            return {
                "status": "authorized",
                "status_code": 3,
                "requested": True,
            }

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_permission_access()

    assert result.status == CHECK_OK
    assert calls == [True]
    assert "authorized" in result.detail.lower()


def test_run_startup_checks_loads_library_before_photokit_but_displays_permission_first(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_run_check_with_timeout(check_fn, *, timeout_secs, timeout_result):
        calls.append(timeout_result.key)
        return check_fn()

    monkeypatch.setattr("photos_mcp.application.preflight_service._run_check_with_timeout", fake_run_check_with_timeout)
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_permission_access",
        lambda: SimpleNamespace(key="photos_permission", status=CHECK_OK),
    )
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_library_readability",
        lambda: SimpleNamespace(key="photos_read", status=CHECK_OK),
    )
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_automation_access",
        lambda: SimpleNamespace(key="photos_automation", status=CHECK_OK),
    )
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_thumbnail_access",
        lambda: SimpleNamespace(key="photos_thumbnail", status=CHECK_OK),
    )

    results = run_startup_checks(include_expensive=True)

    assert calls == [
        "photos_read",
        "photos_permission",
        "photos_automation",
        "photos_thumbnail",
    ]
    assert [result.key for result in results] == [
        "photos_permission",
        "photos_read",
        "photos_automation",
        "photos_thumbnail",
    ]


def test_run_preflight_check_dispatches_only_requested_check(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_check_with_timeout(check_fn, *, timeout_secs, timeout_result):
        calls.append(timeout_result.key)
        return check_fn()

    monkeypatch.setattr("photos_mcp.application.preflight_service._run_check_with_timeout", fake_run_check_with_timeout)
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_thumbnail_access",
        lambda: preflight.PreflightCheckResult(
            "photos_thumbnail",
            "Thumbnail",
            CHECK_OK,
            "available",
        ),
    )

    result = run_preflight_check("photos_thumbnail")

    assert result.status == CHECK_OK
    assert calls == ["photos_thumbnail"]


def test_startup_defers_uninterruptible_automation_and_thumbnail_checks(monkeypatch) -> None:
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_permission_access",
        lambda: preflight.PreflightCheckResult("photos_permission", "Permission", CHECK_OK, "ok"),
    )
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_library_readability",
        lambda: preflight.PreflightCheckResult("photos_read", "Read", CHECK_OK, "ok"),
    )
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_automation_access",
        lambda: (_ for _ in ()).throw(AssertionError("automation probe must be deferred")),
    )
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.check_photos_thumbnail_access",
        lambda: (_ for _ in ()).throw(AssertionError("thumbnail probe must be deferred")),
    )

    results = run_startup_checks()

    assert [result.key for result in results] == [
        "photos_permission",
        "photos_read",
        "photos_automation",
        "photos_thumbnail",
    ]
    assert results[2].status == CHECK_WARNING
    assert "deferred" in results[2].summary
    assert "deferred" in results[3].summary


def test_timed_out_preflight_worker_is_not_duplicated() -> None:
    started = Event()
    release = Event()
    calls: list[str] = []
    timeout_result = preflight.PreflightCheckResult(
        key="test_no_duplicate_worker",
        title="Test",
        status=CHECK_WARNING,
        summary="timed out",
    )

    def blocking_check():
        calls.append("started")
        started.set()
        release.wait(timeout=1.0)
        return timeout_result

    first = preflight._run_check_with_timeout(
        blocking_check,
        timeout_secs=0.01,
        timeout_result=timeout_result,
    )
    assert started.is_set()
    second = preflight._run_check_with_timeout(
        blocking_check,
        timeout_secs=0.01,
        timeout_result=timeout_result,
    )

    assert first is timeout_result
    assert second is timeout_result
    assert calls == ["started"]
    release.set()
    active_thread = preflight._ACTIVE_CHECK_THREADS.get(timeout_result.key)
    if active_thread is not None:
        active_thread.join(timeout=1.0)


def test_check_photos_thumbnail_access_reports_success(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit >= 1
            return [SimpleNamespace(photo_id="photo-1", path="/tmp/photo-1.jpeg")]

        def get_thumbnail(self, photo_id: str, max_size: int = 64):
            assert photo_id == "photo-1"
            assert max_size == 64
            return "thumb-b64"

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_OK
    assert "photo-1" in result.detail
    assert "fallback_used=false" in result.detail
    assert "candidates_tried=1" in result.detail
    assert "permission_denied_seen=false" in result.detail
    assert "local_path_missing_seen=false" in result.detail


def test_check_photos_thumbnail_access_reports_warning_when_thumbnail_missing(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit >= 1
            return [SimpleNamespace(photo_id="photo-1", path="")]

        def get_thumbnail(self, photo_id: str, max_size: int = 64):
            assert photo_id == "photo-1"
            assert max_size == 64
            return None

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_WARNING
    assert "returned no bytes" in result.detail
    assert "fallback_used=false" in result.detail
    assert "candidates_tried=1" in result.detail
    assert "permission_denied_seen=false" in result.detail
    assert "local_path_missing_seen=true" in result.detail
    assert "downloaded locally" in result.hint


def test_check_photos_thumbnail_access_uses_fallback_candidate(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit >= 1
            return [
                SimpleNamespace(photo_id="photo-1", path="/tmp/photo-1.jpeg"),
                SimpleNamespace(photo_id="photo-2", path="/tmp/photo-2.jpeg"),
            ]

        def get_thumbnail(self, photo_id: str, max_size: int = 64):
            assert max_size == 64
            if photo_id == "photo-1":
                return None
            if photo_id == "photo-2":
                return "thumb-b64"
            raise AssertionError(f"unexpected photo_id {photo_id}")

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_OK
    assert "photo-2" in result.detail
    assert "fallback_used=true" in result.detail
    assert "candidates_tried=2" in result.detail
    assert "permission_denied_seen=false" in result.detail
    assert "local_path_missing_seen=false" in result.detail


def test_check_photos_thumbnail_access_requests_enough_candidates_for_late_local_match(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit >= 6
            return [
                SimpleNamespace(photo_id=f"photo-{index}", path="")
                for index in range(1, 6)
            ] + [SimpleNamespace(photo_id="photo-6", path="/tmp/photo-6.jpeg")]

        def get_thumbnail(self, photo_id: str, max_size: int = 64):
            assert max_size == 64
            if photo_id == "photo-6":
                return "thumb-b64"
            return None

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_OK
    assert "photo-6" in result.detail
    assert "fallback_used=false" in result.detail
    assert "candidates_tried=1" in result.detail


def test_check_photos_thumbnail_access_reports_permission_denied_context(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit >= 1
            return [SimpleNamespace(photo_id="photo-1", path="")]

        def get_thumbnail(self, photo_id: str, max_size: int = 64):
            assert photo_id == "photo-1"
            assert max_size == 64
            raise RuntimeError(
                "PhotoKit export is not authorized for this process. "
                "Grant Photos access in System Settings > Privacy & Security > Photos. "
                "auth_status = 2"
            )

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_WARNING
    assert "auth_status = 2" in result.detail
    assert "permission_denied_seen=true" in result.detail
    assert "local_path_missing_seen=true" in result.detail
    assert "candidates_tried=1" in result.detail


def test_check_photos_thumbnail_access_reports_silent_photokit_permission_denied(monkeypatch) -> None:
    class FakeSource:
        def __init__(self) -> None:
            self._photokit_disabled = False

        def list_photos(self, limit: int = 1):
            assert limit >= 1
            return [SimpleNamespace(photo_id="photo-1", path="")]

        def get_thumbnail(self, photo_id: str, max_size: int = 64):
            assert photo_id == "photo-1"
            assert max_size == 64
            self._photokit_disabled = True
            return None

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.application.preflight_service.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_WARNING
    assert "permission_denied_seen=true" in result.detail
    assert "local_path_missing_seen=true" in result.detail
    assert "candidates_tried=1" in result.detail


def test_check_photos_thumbnail_access_error_hint_uses_grouped_tool_name(monkeypatch) -> None:
    monkeypatch.setattr(
        "photos_mcp.application.preflight_service.load_vendor_server",
        lambda _name: (_ for _ in ()).throw(RuntimeError("thumbnail unavailable")),
    )

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_ERROR
    assert "photos_select(action=\"analyze_photo\")" in result.hint
    assert "photos_run" not in result.hint


def test_aggregate_check_status_prioritizes_error_then_warning() -> None:
    ok = SimpleNamespace(status=CHECK_OK)
    warning = SimpleNamespace(status=CHECK_WARNING)
    error = SimpleNamespace(status=CHECK_ERROR)

    assert aggregate_check_status([ok]) == CHECK_OK
    assert aggregate_check_status([ok, warning]) == CHECK_WARNING
    assert aggregate_check_status([warning, error]) == CHECK_ERROR
    assert aggregate_check_status([]) == "pending"
