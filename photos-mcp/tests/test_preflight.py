from __future__ import annotations

from types import SimpleNamespace

from photos_mcp.preflight import (
    CHECK_ERROR,
    CHECK_OK,
    CHECK_WARNING,
    aggregate_check_status,
    check_photos_automation_access,
    check_photos_library_readability,
    check_photos_permission_access,
    check_photos_thumbnail_access,
    run_startup_checks,
)


def test_check_photos_library_readability_reports_success(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit == 1
            return [SimpleNamespace(photo_id="photo-1")]

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

    result = check_photos_library_readability()

    assert result.status == CHECK_OK
    assert "photo-1" in result.detail


def test_check_photos_library_readability_reports_failure(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            raise RuntimeError("photos db unavailable")

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

    result = check_photos_library_readability()

    assert result.status == CHECK_ERROR
    assert result.detail == "photos db unavailable"


def test_check_photos_automation_access_downgrades_permission_error(monkeypatch) -> None:
    class FakeWriter:
        def list_albums(self):
            raise RuntimeError("run_script failed: Not authorized to send Apple events to Photos. (-1743)")

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

    result = check_photos_automation_access()

    assert result.status == CHECK_WARNING
    assert "automation" in result.hint.lower()


def test_check_photos_automation_access_reports_success(monkeypatch) -> None:
    class FakeWriter:
        def list_albums(self):
            return [{"name": "album-1"}, {"name": "album-2"}]

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

    result = check_photos_permission_access()

    assert result.status == CHECK_OK
    assert calls == [True]
    assert "authorized" in result.detail.lower()


def test_run_startup_checks_includes_photos_permission_first(monkeypatch) -> None:
    def fake_run_check_with_timeout(check_fn, *, timeout_secs, timeout_result):
        return check_fn()

    monkeypatch.setattr("photos_mcp.preflight._run_check_with_timeout", fake_run_check_with_timeout)
    monkeypatch.setattr(
        "photos_mcp.preflight.check_photos_permission_access",
        lambda: SimpleNamespace(key="photos_permission", status=CHECK_OK),
    )
    monkeypatch.setattr(
        "photos_mcp.preflight.check_photos_library_readability",
        lambda: SimpleNamespace(key="photos_read", status=CHECK_OK),
    )
    monkeypatch.setattr(
        "photos_mcp.preflight.check_photos_automation_access",
        lambda: SimpleNamespace(key="photos_automation", status=CHECK_OK),
    )
    monkeypatch.setattr(
        "photos_mcp.preflight.check_photos_thumbnail_access",
        lambda: SimpleNamespace(key="photos_thumbnail", status=CHECK_OK),
    )

    results = run_startup_checks()

    assert [result.key for result in results] == [
        "photos_permission",
        "photos_read",
        "photos_automation",
        "photos_thumbnail",
    ]


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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_vendor_server", lambda name: fake_module)

    result = check_photos_thumbnail_access()

    assert result.status == CHECK_WARNING
    assert "permission_denied_seen=true" in result.detail
    assert "local_path_missing_seen=true" in result.detail
    assert "candidates_tried=1" in result.detail


def test_aggregate_check_status_prioritizes_error_then_warning() -> None:
    ok = SimpleNamespace(status=CHECK_OK)
    warning = SimpleNamespace(status=CHECK_WARNING)
    error = SimpleNamespace(status=CHECK_ERROR)

    assert aggregate_check_status([ok]) == CHECK_OK
    assert aggregate_check_status([ok, warning]) == CHECK_WARNING
    assert aggregate_check_status([warning, error]) == CHECK_ERROR
    assert aggregate_check_status([]) == "pending"