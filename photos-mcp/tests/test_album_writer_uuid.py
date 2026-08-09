from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from photos_mcp.infrastructure.vendor_adapter.loader import prepare_vendor_runtime


def _album_writer_module():
    prepare_vendor_runtime("photo-ranker")
    return importlib.import_module("photos_mcp_vendor_photo_ranker.album_writer")


class FakeAlbum:
    def __init__(self, uuid: str, name: str, path: str | None = None) -> None:
        self.uuid = uuid
        self.name = name
        self._path = path
        self._photos = [SimpleNamespace(uuid="photo-existing")]
        self.added = []

    def path_str(self) -> str:
        if self._path is None:
            raise RuntimeError("path unavailable")
        return self._path

    def photos(self):
        return list(self._photos)

    def add(self, photos) -> None:
        self.added.extend(photos)


class FakeLibrary:
    def __init__(self, albums: list[FakeAlbum]) -> None:
        self._albums = albums
        self.album_calls = []
        self.created = []

    def albums(self):
        return list(self._albums)

    def album(self, *names, uuid=None, top_level=False):
        self.album_calls.append({"names": names, "uuid": uuid, "top_level": top_level})
        if uuid is not None:
            return next((album for album in self._albums if album.uuid == uuid), None)
        name = names[0]
        return next((album for album in self._albums if album.name == name), None)

    def create_album(self, name, folder=None):
        album = FakeAlbum(f"created-{len(self.created) + 1}", name, name)
        self.created.append((name, folder))
        self._albums.append(album)
        return album


def _writer_with_library(albums: list[FakeAlbum]):
    module = _album_writer_module()
    writer = module.AlbumWriter()
    writer._lib = FakeLibrary(albums)
    return writer


def _install_fake_photoscript(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "photoscript",
        SimpleNamespace(Photo=lambda uuid: SimpleNamespace(uuid=uuid)),
    )


def test_list_albums_includes_uuid_path_and_folder_when_available() -> None:
    writer = _writer_with_library(
        [
            FakeAlbum("album-1", "여행", "가족/2026/여행"),
            SimpleNamespace(
                uuid="album-2",
                name="경로 없는 앨범",
                photos=lambda: [],
            ),
        ]
    )

    albums = writer.list_albums()

    assert albums[0] == {
        "name": "여행",
        "uuid": "album-1",
        "count": 1,
        "path": "가족/2026/여행",
        "folder": "가족/2026",
    }
    assert albums[1] == {"name": "경로 없는 앨범", "uuid": "album-2", "count": 0}


def test_list_albums_uses_read_only_database_without_apple_events(monkeypatch) -> None:
    module = _album_writer_module()
    album = SimpleNamespace(
        title="가족",
        uuid="album-db-1",
        folder_names=["사진", "2026"],
        photos=[object(), object()],
    )
    monkeypatch.setattr(
        module,
        "get_apple_photos_db",
        lambda: SimpleNamespace(album_info=[album]),
    )
    writer = module.AlbumWriter()

    assert writer.list_albums() == [{
        "name": "가족",
        "uuid": "album-db-1",
        "count": 2,
        "folder": "사진/2026",
        "path": "사진/2026/가족",
    }]


def test_list_album_photo_ids_resolves_only_requested_uuid() -> None:
    wrong = FakeAlbum("album-wrong", "중복 이름", "첫 폴더/중복 이름")
    target = FakeAlbum("album-target", "중복 이름", "둘째 폴더/중복 이름")
    target._photos = [SimpleNamespace(uuid="photo-1"), SimpleNamespace(uuid="photo-2")]
    writer = _writer_with_library([wrong, target])

    result = writer.list_album_photo_ids("중복 이름", album_id="album-target")

    assert result["album_id"] == "album-target"
    assert result["photo_ids"] == ["photo-1", "photo-2"]
    assert writer._lib.album_calls == [
        {"names": (), "uuid": "album-target", "top_level": False}
    ]


def test_resolve_album_returns_uuid_location_and_validates_optional_name() -> None:
    target = FakeAlbum("album-target", "대상", "가족/대상")
    writer = _writer_with_library([target])

    result = writer.resolve_album("album-target", "대상")

    assert result == {
        "album": "대상",
        "album_id": "album-target",
        "uuid": "album-target",
        "exists": True,
        "path": "가족/대상",
        "folder": "가족",
    }
    assert writer._lib.album_calls == [
        {"names": (), "uuid": "album-target", "top_level": False}
    ]


def test_resolve_album_treats_photoscript_value_error_as_missing() -> None:
    writer = _writer_with_library([])

    def invalid_uuid(*_names, uuid=None, top_level=False):
        raise ValueError(f"Invalid album id: {uuid}")

    writer._lib.album = invalid_uuid

    assert writer.resolve_album("missing-album") == {
        "album": "",
        "album_id": "missing-album",
        "uuid": "missing-album",
        "exists": False,
    }


def test_resolve_album_accepts_photoscript_uuid_suffix() -> None:
    target = FakeAlbum("album-target", "대상")
    writer = _writer_with_library([target])

    def resolve_suffixed(*_names, uuid=None, top_level=False):
        assert uuid == "album-target/L0/040"
        return target

    writer._lib.album = resolve_suffixed

    result = writer.resolve_album("album-target/L0/040")

    assert result["album_id"] == "album-target"
    assert result["exists"] is True


def test_resolve_album_rejects_library_returning_different_uuid() -> None:
    writer = _writer_with_library([])
    wrong = FakeAlbum("album-wrong", "대상")
    writer._lib.album = lambda *_names, uuid=None, top_level=False: wrong

    with pytest.raises(ValueError, match="resolved a different album UUID"):
        writer.resolve_album("album-target")


def test_add_photos_by_uuid_never_creates_or_falls_back_by_name(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    wrong = FakeAlbum("album-wrong", "중복 이름")
    target = FakeAlbum("album-target", "중복 이름")
    writer = _writer_with_library([wrong, target])

    result = writer.add_photos_to_album(
        ["photo-1", "photo-2"],
        "중복 이름",
        album_id="album-target",
    )

    assert [photo.uuid for photo in target.added] == ["photo-1", "photo-2"]
    assert wrong.added == []
    assert writer._lib.created == []
    assert result["album_id"] == "album-target"
    assert result["uuid"] == "album-target"
    assert result["created_album"] is False
    assert all(call["names"] == () for call in writer._lib.album_calls)


def test_add_photos_accepts_uuid_without_optional_name(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    target = FakeAlbum("album-target", "실제 이름")
    writer = _writer_with_library([target])

    result = writer.add_photos_to_album(["photo-1"], album_id="album-target")

    assert result["album"] == "실제 이름"
    assert result["album_id"] == "album-target"
    assert [photo.uuid for photo in target.added] == ["photo-1"]


def test_add_photos_by_unknown_uuid_does_not_create_or_use_name(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    writer = _writer_with_library([FakeAlbum("album-other", "대상")])

    with pytest.raises(ValueError, match="UUID not found"):
        writer.add_photos_to_album(["photo-1"], "대상", album_id="missing-album")

    assert writer._lib.created == []
    assert writer._lib.album_calls == [
        {"names": (), "uuid": "missing-album", "top_level": False}
    ]


def test_add_photos_by_uuid_rejects_optional_name_mismatch_before_write(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    target = FakeAlbum("album-target", "실제 이름")
    writer = _writer_with_library([target])

    with pytest.raises(ValueError, match="name does not match"):
        writer.add_photos_to_album(
            ["photo-1"],
            "잘못된 이름",
            album_id="album-target",
        )

    assert target.added == []
    assert writer._lib.created == []


def test_name_based_add_preserves_creation_and_returns_created_uuid(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    writer = _writer_with_library([])

    result = writer.add_photos_to_album(["photo-1"], "새 앨범")

    assert writer._lib.created == [("새 앨범", None)]
    assert result["album"] == "새 앨범"
    assert result["album_id"] == "created-1"
    assert result["uuid"] == "created-1"
    assert result["created_album"] is True


def test_create_album_reuses_only_same_name_in_requested_folder(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    first = FakeAlbum("album-first", "여행", "가족/여행")
    second = FakeAlbum("album-second", "여행", "업무/여행")
    writer = _writer_with_library([first, second])
    writer._ensure_folder = lambda _folder: SimpleNamespace(name="업무")

    result = writer.create_album("여행", "업무")

    assert result["uuid"] == "album-second"
    assert result["created"] is False
    assert writer._lib.created == []


def test_create_album_does_not_reuse_same_name_from_another_folder(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    existing = FakeAlbum("album-first", "여행", "가족/여행")
    writer = _writer_with_library([existing])
    writer._ensure_folder = lambda _folder: SimpleNamespace(name="업무")

    result = writer.create_album("여행", "업무")

    assert result["uuid"] == "created-1"
    assert result["created"] is True
    assert len(writer._lib.created) == 1


def test_create_album_rejects_duplicate_top_level_names(monkeypatch) -> None:
    _install_fake_photoscript(monkeypatch)
    writer = _writer_with_library([
        FakeAlbum("album-first", "여행", "여행"),
        FakeAlbum("album-second", "여행", "여행"),
    ])

    with pytest.raises(ValueError, match="Multiple Apple Photos albums"):
        writer.create_album("여행")

    assert writer._lib.created == []


@pytest.mark.parametrize(
    ("method_name", "args", "expected_operation"),
    [
        (
            "list_album_photo_ids",
            ("대상", "폴더", "album-target"),
            "list_album_photo_ids",
        ),
        (
            "add_photos_to_album",
            (["photo-1"], "대상", "폴더", "album-target"),
            "add_photos_to_album",
        ),
        (
            "resolve_album",
            ("album-target", "대상"),
            "resolve_album",
        ),
    ],
)
def test_terminal_mode_payload_carries_album_id(
    monkeypatch,
    method_name: str,
    args: tuple,
    expected_operation: str,
) -> None:
    writer = _album_writer_module().AlbumWriter()
    calls = []
    monkeypatch.setattr(writer, "_should_use_terminal_helper", lambda: True)

    def fake_helper(operation, payload):
        calls.append((operation, payload))
        return {"exists": True} if operation.startswith("list_") else {"added": 1}

    monkeypatch.setattr(writer, "_run_terminal_helper", fake_helper)

    getattr(writer, method_name)(*args)

    assert calls[0][0] == expected_operation
    assert calls[0][1]["album_id"] == "album-target"


def test_terminal_runner_forwards_album_id_to_writer(monkeypatch, tmp_path: Path) -> None:
    script_path = (
        Path(__file__).parents[1]
        / "src/photos_mcp/vendor/photo-ranker/scripts/apple_photos_terminal_runner.py"
    )
    captured = {}
    fake_writer = SimpleNamespace()

    def add_photos_to_album(photo_uuids, album_name, folder="", album_id=""):
        captured["call"] = (photo_uuids, album_name, folder, album_id)
        return {"album_id": album_id, "added": len(photo_uuids)}

    fake_writer.add_photos_to_album = add_photos_to_album
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps(
            {
                "operation": "add_photos_to_album",
                "payload": {
                    "photo_uuids": ["photo-1"],
                    "album_name": "대상",
                    "folder": "폴더",
                    "album_id": "album-target",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setitem(
        sys.modules,
        "_script_bootstrap",
        SimpleNamespace(prepare_photo_ranker_runtime=lambda _path: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "apple_terminal_helper",
        SimpleNamespace(
            write_terminal_response=lambda _path, _request, result: captured.update(
                result=result
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "photos_mcp_vendor_photo_ranker.album_writer",
        SimpleNamespace(AlbumWriter=lambda: fake_writer),
    )

    spec = importlib.util.spec_from_file_location("test_apple_photos_terminal_runner", script_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda: argparse.Namespace(request=str(request_path), response=str(response_path)),
    )

    assert runner.main() == 0
    assert captured["call"] == (["photo-1"], "대상", "폴더", "album-target")
    assert captured["result"] == {"album_id": "album-target", "added": 1}


def test_terminal_runner_forwards_resolve_album_id_and_name(
    monkeypatch,
    tmp_path: Path,
) -> None:
    script_path = (
        Path(__file__).parents[1]
        / "src/photos_mcp/vendor/photo-ranker/scripts/apple_photos_terminal_runner.py"
    )
    captured = {}
    fake_writer = SimpleNamespace()

    def resolve_album(album_id, album_name=""):
        captured["call"] = (album_id, album_name)
        return {"album_id": album_id, "album": album_name, "exists": True}

    fake_writer.resolve_album = resolve_album
    request_path = tmp_path / "resolve-request.json"
    response_path = tmp_path / "resolve-response.json"
    request_path.write_text(
        json.dumps(
            {
                "operation": "resolve_album",
                "payload": {"album_id": "album-target", "album_name": "대상"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setitem(
        sys.modules,
        "_script_bootstrap",
        SimpleNamespace(prepare_photo_ranker_runtime=lambda _path: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "apple_terminal_helper",
        SimpleNamespace(
            write_terminal_response=lambda _path, _request, result: captured.update(
                result=result
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "photos_mcp_vendor_photo_ranker.album_writer",
        SimpleNamespace(AlbumWriter=lambda: fake_writer),
    )

    spec = importlib.util.spec_from_file_location(
        "test_apple_photos_terminal_resolve_runner",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda: argparse.Namespace(request=str(request_path), response=str(response_path)),
    )

    assert runner.main() == 0
    assert captured["call"] == ("album-target", "대상")
    assert captured["result"] == {
        "album_id": "album-target",
        "album": "대상",
        "exists": True,
    }
