"""Application service for direct, read-only photo classification from the app UI."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

from photos_mcp.application.selection_service import handle_select
from photos_mcp.infrastructure.vendor_adapter.photo_source import PhotoSourcePort, VendorPhotoSourcePort
from photos_mcp.infrastructure.sources.local_files.raw_image import RAW_IMAGE_EXTENSIONS
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore


DEFAULT_SCOPE_SCAN_LIMIT = 4000
MAX_CLASSIFICATION_LIMIT = 1000
ALLOWED_MODES = {"classify", "select_best"}
ALLOWED_SELECTION_PROFILES = {"general", "person", "landscape"}
ALLOWED_SOURCES = {"apple", "local"}
LOCAL_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
} | set(RAW_IMAGE_EXTENSIONS)


class ClassificationValidationError(ValueError):
    """A user-correctable direct classification input error."""


def common_local_source_path(selected_photo_ids: tuple[str, ...] | list[str]) -> str:
    """Return the narrowest directory containing an explicit local selection."""

    parents = [str(Path(path).expanduser().resolve().parent) for path in selected_photo_ids if str(path).strip()]
    if not parents:
        raise ClassificationValidationError("분류할 로컬 사진을 선택해 주세요.")
    try:
        common = Path(os.path.commonpath(parents)).resolve()
    except ValueError as exc:
        raise ClassificationValidationError("선택한 사진의 공통 로컬 경로를 계산할 수 없습니다.") from exc
    if not common.is_dir():
        raise ClassificationValidationError("선택한 사진의 공통 로컬 폴더를 찾을 수 없습니다.")
    return str(common)


@dataclass(frozen=True)
class ClassificationCommand:
    source: str = "apple"
    source_path: str = ""
    album: str = ""
    date_from: str = ""
    date_to: str = ""
    mode: str = "classify"
    selection_profile: str = "general"
    exclude_screenshots: bool = True
    limit: int = 50
    selected_photo_ids: tuple[str, ...] = ()

    def validate(self) -> "ClassificationCommand":
        if self.source not in ALLOWED_SOURCES:
            raise ClassificationValidationError("지원하지 않는 사진 소스입니다.")
        if self.mode not in ALLOWED_MODES:
            raise ClassificationValidationError("지원하지 않는 작업 방식입니다.")
        if self.selection_profile not in ALLOWED_SELECTION_PROFILES:
            raise ClassificationValidationError("지원하지 않는 분류 기준입니다.")
        if not 1 <= self.limit <= MAX_CLASSIFICATION_LIMIT:
            raise ClassificationValidationError(
                f"최대 분석 수는 1장부터 {MAX_CLASSIFICATION_LIMIT}장 사이여야 합니다."
            )
        try:
            start = date.fromisoformat(self.date_from) if self.date_from else None
            end = date.fromisoformat(self.date_to) if self.date_to else None
        except ValueError as exc:
            raise ClassificationValidationError("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.") from exc
        if (start is None) != (end is None):
            raise ClassificationValidationError("기간을 지정할 때는 시작일과 종료일을 모두 입력해 주세요.")
        if start is not None and end is not None and start > end:
            raise ClassificationValidationError("시작일은 종료일보다 늦을 수 없습니다.")
        if self.source == "local":
            self._validate_local_scope()
            if self.album or self.date_from or self.date_to:
                raise ClassificationValidationError("로컬 폴더 분류에서는 앨범과 기간을 함께 사용할 수 없습니다.")
        return self

    def _validate_local_scope(self) -> None:
        if not self.source_path.strip():
            raise ClassificationValidationError("로컬 사진 폴더를 선택해 주세요.")
        root = Path(self.source_path).expanduser().resolve()
        if not root.is_dir():
            raise ClassificationValidationError("선택한 로컬 사진 폴더를 찾을 수 없습니다.")
        selected = tuple(str(item).strip() for item in self.selected_photo_ids if str(item).strip())
        if len(selected) != len(set(selected)):
            raise ClassificationValidationError("같은 사진이 중복 선택되었습니다.")
        if len(selected) > self.limit:
            raise ClassificationValidationError("선택한 사진 수가 최대 분석 수보다 많습니다.")
        for photo_id in selected:
            path = Path(photo_id).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() not in LOCAL_IMAGE_EXTENSIONS:
                raise ClassificationValidationError("선택한 항목에 지원하지 않는 이미지가 포함되어 있습니다.")
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ClassificationValidationError("선택한 사진은 지정한 폴더 안에 있어야 합니다.") from exc

    @property
    def action(self) -> str:
        return "classify_range" if self.mode == "classify" else "select_best"

    def select_options(self) -> dict[str, Any]:
        self.validate()
        options: dict[str, Any] = {
            "source": self.source,
            "source_path": self.source_path,
            "album": self.album,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "limit": self.limit,
            "selection_profile": self.selection_profile,
        }
        if self.selected_photo_ids:
            options["selected_photo_ids"] = list(self.selected_photo_ids)
        if self.action == "select_best":
            options["exclude_screenshots"] = self.exclude_screenshots
            options["background"] = True
        return options


@dataclass(frozen=True)
class ClassificationScopePreview:
    status: str
    candidate_count: int
    analyze_ready_count: int
    download_required_count: int
    requested_limit: int
    run_count: int
    scan_limit: int
    count_is_lower_bound: bool = False
    requires_confirmation: bool = False
    message: str = ""

    @property
    def can_run(self) -> bool:
        return self.status in {"ready", "warning"} and self.candidate_count > 0

    def as_payload(self) -> dict[str, Any]:
        return {**asdict(self), "can_run": self.can_run}


SelectHandler = Callable[..., Awaitable[dict[str, Any]]]


class DirectClassificationService:
    """Shared app service used by the native UI without routing through HTTP MCP."""

    def __init__(
        self,
        *,
        state_store: PhotosMcpStateStore | None,
        source_port: PhotoSourcePort | None = None,
        select_handler: SelectHandler = handle_select,
        scope_scan_limit: int = DEFAULT_SCOPE_SCAN_LIMIT,
    ) -> None:
        self._state_store = state_store
        self._source_port = source_port or VendorPhotoSourcePort()
        self._select_handler = select_handler
        self._scope_scan_limit = max(1, int(scope_scan_limit))

    async def list_albums(self, *, limit: int = 200) -> dict[str, Any]:
        try:
            albums = await asyncio.wait_for(
                self._source_port.list_albums("apple", limit=max(1, limit)),
                timeout=30.0,
            )
        except TimeoutError:
            return {
                "status": "warning",
                "error_code": "album_list_timeout",
                "message": "앨범 목록을 불러오는 데 시간이 오래 걸리고 있습니다.",
                "albums": [],
            }
        except Exception as exc:
            return {
                "status": "error",
                "error_code": "album_list_failed",
                "message": f"앨범 목록을 불러오지 못했습니다: {exc}",
                "albums": [],
            }

        normalized = sorted(
            [
                {
                    "id": str(item.get("id") or item.get("uuid") or ""),
                    "name": str(item.get("name") or item.get("title") or ""),
                    "photo_count": max(0, int(item.get("photo_count") or item.get("count") or 0)),
                    "folders": [str(folder) for folder in list(item.get("folders") or []) if folder],
                }
                for item in albums
                if isinstance(item, dict) and str(item.get("name") or item.get("title") or "").strip()
            ],
            key=lambda item: (item["name"].casefold(), item["id"]),
        )
        return {"status": "ready", "count": len(normalized), "albums": normalized}

    async def preview(self, command: ClassificationCommand) -> ClassificationScopePreview:
        command.validate()
        if command.source == "local" and command.selected_photo_ids:
            selected_count = len(command.selected_photo_ids)
            return ClassificationScopePreview(
                status="ready",
                candidate_count=selected_count,
                analyze_ready_count=selected_count,
                download_required_count=0,
                requested_limit=command.limit,
                run_count=selected_count,
                scan_limit=selected_count,
                message="선택한 로컬 사진만 읽기 전용으로 분석합니다.",
            )
        scan_limit = min(max(command.limit * 4, 100), self._scope_scan_limit)
        filters: dict[str, Any] = {
            "album": command.album,
            "date_from": command.date_from,
            "date_to": command.date_to,
            "limit": scan_limit,
        }
        if command.source == "local":
            filters["path_or_bucket"] = command.source_path
        try:
            items = await asyncio.wait_for(
                self._source_port.list_photos(
                    command.source,
                    **filters,
                ),
                timeout=30.0,
            )
        except TimeoutError:
            return ClassificationScopePreview(
                status="error",
                candidate_count=0,
                analyze_ready_count=0,
                download_required_count=0,
                requested_limit=command.limit,
                run_count=0,
                scan_limit=scan_limit,
                message="선택한 범위의 사진 수를 제한 시간 안에 확인하지 못했습니다.",
            )

        photo_items = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("media_type") or "photo").lower() == "photo"
        ]
        candidate_count = len(photo_items)
        ready_count = sum(1 for item in photo_items if bool(item.get("path")))
        download_count = max(0, candidate_count - ready_count)
        count_is_lower_bound = candidate_count >= scan_limit
        broad_scope = (
            not command.album and not command.date_from and not command.date_to
            if command.source == "apple"
            else True
        )
        requires_confirmation = broad_scope or count_is_lower_bound or candidate_count > command.limit
        if candidate_count == 0:
            status = "empty"
            message = "선택한 범위에 분류할 사진이 없습니다."
        elif download_count:
            status = "warning"
            message = "일부 사진은 분석 전에 iCloud 원본 다운로드가 필요할 수 있습니다."
        else:
            status = "ready"
            message = "선택한 범위를 분석할 수 있습니다."
        return ClassificationScopePreview(
            status=status,
            candidate_count=candidate_count,
            analyze_ready_count=ready_count,
            download_required_count=download_count,
            requested_limit=command.limit,
            run_count=min(candidate_count, command.limit),
            scan_limit=scan_limit,
            count_is_lower_bound=count_is_lower_bound,
            requires_confirmation=requires_confirmation,
            message=message,
        )

    async def execute(self, command: ClassificationCommand) -> dict[str, Any]:
        command.validate()
        return await self._select_handler(
            state_store=self._state_store,
            action=command.action,
            options=command.select_options(),
        )
