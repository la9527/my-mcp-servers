from __future__ import annotations

from types import SimpleNamespace
import sys
from threading import Event, Thread

from photos_mcp import apple_photos_runtime


def _reset_runtime(monkeypatch) -> None:
    monkeypatch.setattr(apple_photos_runtime, "_DATABASE", None)
    monkeypatch.setattr(apple_photos_runtime, "_DATABASE_LOADING", False)
    monkeypatch.setattr(apple_photos_runtime, "_DATABASE_ERROR", None)
    apple_photos_runtime._DATABASE_READY.clear()


def test_shared_runtime_uses_explicit_library_and_skips_search_index(monkeypatch, tmp_path) -> None:
    library = tmp_path / "Photos Library.photoslibrary"
    library.mkdir()
    captured: list[dict] = []

    class FakePhotosDB:
        def __init__(self, **kwargs) -> None:
            captured.append(kwargs)

    _reset_runtime(monkeypatch)
    monkeypatch.setenv("PHOTOS_MCP_PHOTOS_LIBRARY_PATH", str(library))
    monkeypatch.setitem(sys.modules, "osxphotos", SimpleNamespace(PhotosDB=FakePhotosDB))

    first = apple_photos_runtime.get_apple_photos_db()
    second = apple_photos_runtime.get_apple_photos_db()

    assert first is second
    assert captured == [
        {
            "_skip_searchinfo": True,
            "dbfile": str(library.resolve()),
            "library_path": str(library.resolve()),
        }
    ]


def test_shared_runtime_deduplicates_concurrent_cold_starts(monkeypatch) -> None:
    started = Event()
    release = Event()
    calls: list[int] = []
    results: list[object] = []

    class FakePhotosDB:
        def __init__(self, **_kwargs) -> None:
            calls.append(1)
            started.set()
            assert release.wait(timeout=1)

    _reset_runtime(monkeypatch)
    monkeypatch.setitem(sys.modules, "osxphotos", SimpleNamespace(PhotosDB=FakePhotosDB))

    first = Thread(target=lambda: results.append(apple_photos_runtime.get_apple_photos_db()))
    second = Thread(target=lambda: results.append(apple_photos_runtime.get_apple_photos_db()))
    first.start()
    assert started.wait(timeout=1)
    second.start()
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == [1]
    assert len(results) == 2
    assert results[0] is results[1]
