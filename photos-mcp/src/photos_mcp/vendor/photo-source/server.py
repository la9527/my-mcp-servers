"""photo-source MCP server — Apple Photos, GCS, local folder access."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "photo-source",
    instructions=(
        "사진 소스 접근 서버. Apple Photos, Google Cloud Storage, "
        "로컬 폴더에서 사진을 검색, 메타데이터 조회, "
        "썸네일 생성할 수 있습니다."
    ),
)

# ── lazy source instances ──────────────────────────────
_local_source = None
_apple_source = None
_gcs_source = None


def _get_local_source(root_dir: str):
    from .sources.local_folder import LocalFolderSource

    global _local_source
    if _local_source is None or _local_source._root != root_dir:
        _local_source = LocalFolderSource(root_dir)
    return _local_source


def _get_apple_source():
    from .sources.apple_photos import ApplePhotosSource

    global _apple_source
    if _apple_source is None:
        _apple_source = ApplePhotosSource()
    return _apple_source


def _parse_gcs_location(path_or_bucket: str) -> tuple[str, str]:
    """Normalize the documented gs://bucket/prefix location form."""
    location = path_or_bucket.strip()
    if location.startswith("gs://"):
        location = location[5:]
    bucket, separator, prefix = location.partition("/")
    if not bucket:
        raise ValueError("GCS path_or_bucket must include a bucket name")
    return bucket, prefix if separator else ""


def _get_gcs_source(bucket: str, prefix: str = ""):
    from .sources.gcs import GCSSource

    global _gcs_source
    if (
        _gcs_source is None
        or _gcs_source._bucket_name != bucket
        or _gcs_source._prefix != prefix
    ):
        _gcs_source = GCSSource(bucket, prefix)
    return _gcs_source


def _google_picker_required() -> None:
    raise ValueError(
        "Google Photos 전체 보관함 조회는 지원하지 않습니다. "
        "Google Photos Picker 선택 세션을 사용하세요."
    )


# ── MCP Tools ──────────────────────────────────────────


@mcp.tool()
def list_photos(
    source: str,
    path_or_bucket: str = "",
    date_from: str = "",
    date_to: str = "",
    album: str = "",
    person: str = "",
    limit: int = 100,
) -> list[dict]:
    """사진 목록을 반환합니다.

    Args:
        source: 소스 종류 — "local", "apple", "gcs"
        path_or_bucket: local이면 디렉터리 경로, gcs이면 bucket 이름
        date_from: 시작 날짜 (ISO 형식, 선택)
        date_to: 종료 날짜 (ISO 형식, 선택)
        album: 앨범 이름 필터 (Apple Photos 전용, 선택)
        person: 인물 이름 필터 (Apple Photos 전용, 선택)
        limit: 최대 반환 수
    """
    src = _resolve_source(source, path_or_bucket)
    kwargs: dict = {
        "date_from": date_from or None,
        "date_to": date_to or None,
        "limit": limit,
    }

    if source == "apple":
        if album:
            kwargs["album"] = album
        if person:
            kwargs["person"] = person

    photos = src.list_photos(**kwargs)
    return [p.to_dict() for p in photos]


@mcp.tool()
def list_albums(source: str = "apple", limit: int = 200) -> list[dict]:
    """사진 소스의 앨범 목록을 읽기 전용으로 반환합니다."""
    if source == "apple":
        return _get_apple_source().list_albums(limit=limit)
    if source == "google":
        _google_picker_required()
    return []


@mcp.tool()
def get_metadata(
    source: str,
    photo_id: str,
    path_or_bucket: str = "",
) -> dict | None:
    """사진의 상세 메타데이터를 반환합니다.

    Args:
        source: 소스 종류 — "local", "apple", "gcs"
        photo_id: 사진 ID (local=경로, apple=UUID, gcs=blob 이름)
        path_or_bucket: local이면 디렉터리, gcs이면 bucket 이름
    """
    src = _resolve_source(source, path_or_bucket)
    meta = src.get_metadata(photo_id)
    return meta.to_dict() if meta else None


@mcp.tool()
def get_thumbnail(
    source: str,
    photo_id: str,
    path_or_bucket: str = "",
    max_size: int = 512,
) -> str | None:
    """사진의 리사이즈된 썸네일을 base64로 반환합니다.

    Args:
        source: 소스 종류 — "local", "apple", "gcs"
        photo_id: 사진 ID
        path_or_bucket: local이면 디렉터리, gcs이면 bucket 이름
        max_size: 썸네일 최대 크기 (픽셀)
    """
    src = _resolve_source(source, path_or_bucket)
    return src.get_thumbnail(photo_id, max_size)


@mcp.tool()
def search_photos(
    query: str,
    source: str = "apple",
    path_or_bucket: str = "",
    limit: int = 50,
) -> list[dict]:
    """키워드로 Apple Photos 사진을 검색합니다.

    Args:
        query: 검색 키워드
        source: 소스 종류 — "apple"
        path_or_bucket: 사용하지 않음
        limit: 최대 결과 수
    """
    if source == "google":
        _google_picker_required()
    elif source == "apple":
        src = _get_apple_source()
        photos = src.search_photos(query, limit)
        return [p.to_dict() for p in photos]
    else:
        return [{"error": f"search_photos는 apple만 지원합니다. (받은 값: {source})"}]


@mcp.tool()
def prefetch_photos(
    source: str,
    path_or_bucket: str = "",
    photo_ids: list[str] | None = None,
    date_from: str = "",
    date_to: str = "",
    album: str = "",
    person: str = "",
    limit: int = 100,
) -> dict:
    """필터로 선택된 사진 원본을 미리 로컬에 확보합니다.

    Args:
        source: 소스 종류 — 현재는 "apple" 중심
        path_or_bucket: local/gcs 호환용 자리, apple 에서는 사용하지 않음
        photo_ids: 특정 사진 ID 목록 (선택)
        date_from: 시작 날짜 (ISO 형식, 선택)
        date_to: 종료 날짜 (ISO 형식, 선택)
        album: 앨범 이름 필터 (Apple Photos 전용, 선택)
        person: 인물 이름 필터 (Apple Photos 전용, 선택)
        limit: 최대 처리 수
    """
    src = _resolve_source(source, path_or_bucket)
    if not hasattr(src, "prefetch_photos"):
        raise ValueError(f"prefetch_photos is not supported for source: {source}")

    kwargs: dict = {
        "photo_ids": photo_ids or None,
        "date_from": date_from or None,
        "date_to": date_to or None,
        "limit": limit,
    }
    if album:
        kwargs["album"] = album
    if person and source == "apple":
        kwargs["person"] = person

    return src.prefetch_photos(**kwargs)


@mcp.tool()
def export_photos(
    source: str,
    photo_ids: list[str],
    output_dir: str,
    path_or_bucket: str = "",
    max_size: int = 0,
) -> dict:
    """사진을 지정 디렉터리에 내보냅니다.

    Args:
        source: 소스 종류 — "local", "apple", "gcs"
        photo_ids: 내보낼 사진 ID 목록
        output_dir: 출력 디렉터리
        path_or_bucket: local이면 디렉터리, gcs이면 bucket 이름
        max_size: 리사이즈 최대 크기 (0이면 원본 유지)
    """
    import base64
    from pathlib import Path

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    exported = []
    failed = []

    src = _resolve_source(source, path_or_bucket)

    for pid in photo_ids:
        try:
            if max_size > 0:
                b64 = src.get_thumbnail(pid, max_size)
                if b64 is None:
                    failed.append(pid)
                    continue
                # Derive filename
                meta = src.get_metadata(pid)
                fname = meta.filename if meta else f"{pid}.jpg"
                dest = out / fname
                dest.write_bytes(base64.b64decode(b64))
            else:
                # For local source, just copy the file
                if source == "local":
                    import shutil

                    src_path = src.resolve_photo_path(pid)
                    if src_path is not None:
                        shutil.copy2(src_path, out / src_path.name)
                    else:
                        failed.append(pid)
                        continue
                else:
                    # For other sources, download via thumbnail at full res
                    b64 = src.get_thumbnail(pid, 99999)
                    if b64 is None:
                        failed.append(pid)
                        continue
                    meta = src.get_metadata(pid)
                    fname = meta.filename if meta else f"{pid}.jpg"
                    dest = out / fname
                    dest.write_bytes(base64.b64decode(b64))
            exported.append(pid)
        except Exception as e:
            logger.error("Export failed for %s: %s", pid, e)
            failed.append(pid)

    return {"exported": exported, "failed": failed}


# ── helpers ────────────────────────────────────────────


def _resolve_source(source: str, path_or_bucket: str):
    if source == "local":
        if not path_or_bucket:
            raise ValueError("path_or_bucket is required for local source")
        return _get_local_source(path_or_bucket)
    elif source == "apple":
        return _get_apple_source()
    elif source == "gcs":
        if not path_or_bucket:
            raise ValueError("path_or_bucket is required for gcs source")
        bucket, prefix = _parse_gcs_location(path_or_bucket)
        return _get_gcs_source(bucket, prefix)
    elif source == "google":
        _google_picker_required()
    else:
        raise ValueError(f"Unknown source: {source}. Use 'local', 'apple', or 'gcs'.")


if __name__ == "__main__":
    mcp.run()
