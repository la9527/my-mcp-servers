"""Approval-gated uploads to app-created Google Photos albums."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from photos_mcp.domain.models.source import MaterializedPhotoContent, SourceDescriptor
from photos_mcp.infrastructure.sources.google_photos.http import (
    GoogleHttpTransport,
    GooglePhotosApiError,
)
from photos_mcp.infrastructure.sources.google_photos.upload_repository import (
    GoogleUploadReceipt,
    GoogleUploadReceiptRepository,
)


Uploader = Callable[[tuple[MaterializedPhotoContent, ...], dict[str, Any]], Awaitable[dict[str, Any]]]
AccessTokenProvider = Callable[[], Awaitable[str]]
LIBRARY_API_ROOT = "https://photoslibrary.googleapis.com/v1"
APPEND_ONLY_SCOPE = "https://www.googleapis.com/auth/photoslibrary.appendonly"


def _content_fingerprint(contents: tuple[MaterializedPhotoContent, ...]) -> str:
    digest = hashlib.sha256()
    for content in contents:
        digest.update(content.asset.stable_key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(content.local_path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _header(headers: Any, name: str) -> str:
    target = name.lower()
    return next(
        (str(value) for key, value in dict(headers or {}).items() if str(key).lower() == target),
        "",
    )


class GooglePhotosLibraryClient:
    def __init__(
        self,
        *,
        access_token: AccessTokenProvider,
        transport: GoogleHttpTransport,
        receipts: GoogleUploadReceiptRepository,
        api_root: str = LIBRARY_API_ROOT,
        preferred_chunk_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._access_token = access_token
        self._transport = transport
        self._receipts = receipts
        self._api_root = api_root.rstrip("/")
        self._preferred_chunk_bytes = max(256 * 1024, int(preferred_chunk_bytes))

    async def upload_album(
        self,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        album_name: str,
        job_id: str,
    ) -> dict[str, Any]:
        album = await self._json_request(
            "POST",
            f"{self._api_root}/albums",
            {"album": {"title": album_name[:500]}},
        )
        album_id = str(album.get("id") or "")
        if not album_id:
            raise GooglePhotosApiError(
                status=502,
                code="missing_album_id",
                message="Google Photos did not return an album id",
            )

        completed: list[tuple[MaterializedPhotoContent, str]] = []
        failed: list[str] = []
        for content in contents:
            try:
                token = await self._upload_content(content, job_id=job_id)
            except (GooglePhotosApiError, ConnectionError, OSError):
                failed.append(content.asset.stable_key)
                continue
            completed.append((content, token))

        created_count = 0
        media_item_ids: list[str] = []
        for start in range(0, len(completed), 50):
            batch = completed[start : start + 50]
            response = await self._json_request(
                "POST",
                f"{self._api_root}/mediaItems:batchCreate",
                {
                    "albumId": album_id,
                    "newMediaItems": [
                        {
                            "simpleMediaItem": {
                                "uploadToken": token,
                                "fileName": Path(content.asset.filename).name[:255],
                            }
                        }
                        for content, token in batch
                    ],
                },
            )
            results = response.get("newMediaItemResults") or []
            if len(results) < len(batch):
                failed.extend(
                    content.asset.stable_key
                    for content, _token in batch[len(results) :]
                )
            for (content, _token), result in zip(batch, results, strict=False):
                status = result.get("status") if isinstance(result, dict) else {}
                media_item = result.get("mediaItem") if isinstance(result, dict) else {}
                media_id = str(media_item.get("id") or "") if isinstance(media_item, dict) else ""
                if isinstance(status, dict) and int(status.get("code") or 0) != 0:
                    failed.append(content.asset.stable_key)
                    continue
                if media_id:
                    media_item_ids.append(media_id)
                created_count += 1

        return {
            "job_id": job_id,
            "album_id": album_id,
            "album_product_url": str(album.get("productUrl") or ""),
            "requested_count": len(contents),
            "uploaded_count": len(completed),
            "created_count": created_count,
            "failed_count": len(set(failed)),
            "media_item_ids": media_item_ids,
            "state": "completed" if not failed else "partial_failure",
        }

    async def _upload_content(
        self,
        content: MaterializedPhotoContent,
        *,
        job_id: str,
    ) -> str:
        content_key = content.asset.stable_key
        existing = self._receipts.get(job_id, content_key)
        if existing and existing.state == "uploaded" and existing.upload_token:
            return existing.upload_token
        receipt = existing or await self._start_upload(content, job_id=job_id)
        offset = await self._query_offset(receipt) if receipt.offset else 0
        size = content.local_path.stat().st_size
        granularity = max(1, receipt.chunk_granularity or 256 * 1024)
        chunk_size = max(granularity, self._preferred_chunk_bytes // granularity * granularity)

        with content.local_path.open("rb") as handle:
            handle.seek(offset)
            while offset < size:
                payload = handle.read(min(chunk_size, size - offset))
                final = offset + len(payload) >= size
                response = await self._transport.request(
                    "POST",
                    receipt.upload_url,
                    headers={
                        "Authorization": f"Bearer {await self._access_token()}",
                        "Content-Length": str(len(payload)),
                        "X-Goog-Upload-Command": "upload, finalize" if final else "upload",
                        "X-Goog-Upload-Offset": str(offset),
                    },
                    body=payload,
                    max_response_bytes=1024 * 1024,
                )
                if response.status >= 400:
                    self._receipts.save(
                        replace(receipt, offset=offset, state="paused", error_code="upload_failed")
                    )
                    raise GooglePhotosApiError(
                        status=response.status,
                        code="resumable_upload_failed",
                        message="Google Photos resumable upload failed",
                    )
                offset += len(payload)
                token = response.body.decode("utf-8").strip() if final else ""
                receipt = self._receipts.save(
                    replace(
                        receipt,
                        offset=offset,
                        upload_token=token,
                        state="uploaded" if final else "uploading",
                        error_code="",
                    )
                )
        if not receipt.upload_token:
            raise GooglePhotosApiError(
                status=502,
                code="missing_upload_token",
                message="Google Photos upload did not return an upload token",
            )
        return receipt.upload_token

    async def _start_upload(
        self,
        content: MaterializedPhotoContent,
        *,
        job_id: str,
    ) -> GoogleUploadReceipt:
        size = content.local_path.stat().st_size
        response = await self._transport.request(
            "POST",
            f"{self._api_root}/uploads",
            headers={
                "Authorization": f"Bearer {await self._access_token()}",
                "Content-Length": "0",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Content-Type": content.mime_type,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Raw-Size": str(size),
            },
            body=b"",
        )
        upload_url = _header(response.headers, "X-Goog-Upload-URL")
        if response.status >= 400 or not upload_url:
            raise GooglePhotosApiError(
                status=response.status,
                code="resumable_upload_start_failed",
                message="Google Photos resumable upload could not be started",
            )
        receipt = GoogleUploadReceipt(
            job_id=job_id,
            content_key=content.asset.stable_key,
            upload_url=upload_url,
            chunk_granularity=int(
                _header(response.headers, "X-Goog-Upload-Chunk-Granularity") or 262144
            ),
            state="uploading",
        )
        return self._receipts.save(receipt)

    async def _query_offset(self, receipt: GoogleUploadReceipt) -> int:
        response = await self._transport.request(
            "POST",
            receipt.upload_url,
            headers={
                "Authorization": f"Bearer {await self._access_token()}",
                "Content-Length": "0",
                "X-Goog-Upload-Command": "query",
            },
            body=b"",
        )
        if response.status >= 400:
            return receipt.offset
        return int(_header(response.headers, "X-Goog-Upload-Size-Received") or receipt.offset)

    async def _json_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._transport.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {await self._access_token()}",
                "Content-Type": "application/json",
            },
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        try:
            result = json.loads(response.body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GooglePhotosApiError(
                status=502,
                code="invalid_google_response",
                message="Google Photos Library returned invalid JSON",
            ) from exc
        if response.status >= 400:
            error = result.get("error") if isinstance(result, dict) else {}
            raise GooglePhotosApiError(
                status=response.status,
                code=str(error.get("status") or "library_request_failed"),
                message=str(error.get("message") or "Google Photos Library request failed"),
            )
        return result if isinstance(result, dict) else {}


class GoogleAppCreatedLibraryDestination:
    def __init__(
        self,
        uploader: Uploader | None = None,
        *,
        client: GooglePhotosLibraryClient | None = None,
    ) -> None:
        self._uploader = uploader
        self._client = client

    async def plan_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        total_bytes = sum(content.local_path.stat().st_size for content in contents)
        return {
            "plan_id": uuid4().hex,
            "destination": destination.source_id,
            "scope": "app_created_content_only",
            "item_count": len(contents),
            "total_bytes": total_bytes,
            "storage_warning_required": total_bytes > 25 * 1024 * 1024,
            "album_name": str(options.get("album_name") or ""),
            "content_fingerprint": _content_fingerprint(contents),
            "approved": False,
        }

    async def execute_write(
        self,
        destination: SourceDescriptor,
        contents: tuple[MaterializedPhotoContent, ...],
        *,
        approved_plan: dict[str, Any],
    ) -> dict[str, Any]:
        if approved_plan.get("approved") is not True:
            raise PermissionError("Google Photos write plan requires explicit approval")
        if approved_plan.get("scope") != "app_created_content_only":
            raise PermissionError("Google Photos writes are limited to app-created content")
        if approved_plan.get("destination") != destination.source_id:
            raise PermissionError("Google Photos write destination changed after approval")
        if int(approved_plan.get("item_count") or -1) != len(contents):
            raise PermissionError("Google Photos write plan item count changed")
        if approved_plan.get("content_fingerprint") != _content_fingerprint(contents):
            raise PermissionError("Google Photos write plan content changed")
        if self._client is not None:
            return await self._client.upload_album(
                contents,
                album_name=str(approved_plan.get("album_name") or "Photos MCP"),
                job_id=str(approved_plan.get("plan_id") or uuid4().hex),
            )
        if self._uploader is None:
            raise RuntimeError("Google Photos Library destination is not configured")
        return await self._uploader(contents, approved_plan)
