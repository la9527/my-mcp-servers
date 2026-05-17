from __future__ import annotations

from types import SimpleNamespace

from photos_mcp.preflight import (
    CHECK_ERROR,
    CHECK_OK,
    CHECK_WARNING,
    aggregate_check_status,
    check_photos_automation_access,
    check_photos_library_readability,
)


def test_check_photos_library_readability_reports_success(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            assert limit == 1
            return [SimpleNamespace(photo_id="photo-1")]

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.preflight.load_legacy_server", lambda name: fake_module)

    result = check_photos_library_readability()

    assert result.status == CHECK_OK
    assert "photo-1" in result.detail


def test_check_photos_library_readability_reports_failure(monkeypatch) -> None:
    class FakeSource:
        def list_photos(self, limit: int = 1):
            raise RuntimeError("photos db unavailable")

    fake_module = SimpleNamespace(_get_apple_source=lambda: FakeSource())
    monkeypatch.setattr("photos_mcp.preflight.load_legacy_server", lambda name: fake_module)

    result = check_photos_library_readability()

    assert result.status == CHECK_ERROR
    assert result.detail == "photos db unavailable"


def test_check_photos_automation_access_downgrades_permission_error(monkeypatch) -> None:
    class FakeWriter:
        def list_albums(self):
            raise RuntimeError("run_script failed: Not authorized to send Apple events to Photos. (-1743)")

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.preflight.load_legacy_server", lambda name: fake_module)

    result = check_photos_automation_access()

    assert result.status == CHECK_WARNING
    assert "automation" in result.hint.lower()


def test_check_photos_automation_access_reports_success(monkeypatch) -> None:
    class FakeWriter:
        def list_albums(self):
            return [{"name": "album-1"}, {"name": "album-2"}]

    fake_module = SimpleNamespace(AlbumWriter=lambda: FakeWriter())
    monkeypatch.setattr("photos_mcp.preflight.load_legacy_server", lambda name: fake_module)

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
    monkeypatch.setattr("photos_mcp.preflight.load_legacy_server", lambda name: fake_module)

    result = check_photos_automation_access()

    assert result.status == CHECK_OK
    assert "2 albums" in result.detail
    assert "album-1" in result.detail


def test_aggregate_check_status_prioritizes_error_then_warning() -> None:
    ok = SimpleNamespace(status=CHECK_OK)
    warning = SimpleNamespace(status=CHECK_WARNING)
    error = SimpleNamespace(status=CHECK_ERROR)

    assert aggregate_check_status([ok]) == CHECK_OK
    assert aggregate_check_status([ok, warning]) == CHECK_WARNING
    assert aggregate_check_status([warning, error]) == CHECK_ERROR
    assert aggregate_check_status([]) == "pending"