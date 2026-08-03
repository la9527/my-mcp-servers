"""Single adapter boundary for all photo-source vendor operations."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol


VendorCaller = Callable[..., Awaitable[Any]]


class PhotoSourcePort(Protocol):
    """Read-only source operations shared by browse, prefetch, and analyze."""

    async def list_photos(self, source: str, **filters: Any) -> list[dict[str, Any]]: ...

    async def list_albums(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]: ...

    async def search_photos(self, query: str, *, source: str, path_or_bucket: str, limit: int) -> list[dict[str, Any]]: ...

    async def prefetch_photos(self, source: str, **filters: Any) -> dict[str, Any]: ...

    async def get_metadata(self, source: str, photo_id: str, *, path_or_bucket: str) -> dict[str, Any] | None: ...

    async def get_thumbnail(
        self,
        source: str,
        photo_id: str,
        *,
        path_or_bucket: str,
        max_size: int,
    ) -> str | None: ...

    async def probe_local_availability(
        self,
        source: str,
        photo_id: str,
        *,
        path_or_bucket: str,
    ) -> dict[str, Any]: ...

    def latest_fetch_detail(self, source: str, photo_id: str) -> dict[str, Any] | None: ...


class VendorPhotoSourcePort:
    """Adapter for the bundled photo-source vendor server.

    Vendor imports stay inside this adapter so facade services do not need to
    know about Apple Photos implementation helpers or MCP method names.
    """

    def __init__(self, caller: VendorCaller | None = None) -> None:
        self._caller = caller

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        # Resolve lazily so test and host integrations can replace the caller.
        if self._caller is None:
            from photos_mcp.facade.common import call_vendor

            caller = call_vendor
        else:
            caller = self._caller

        return await caller("photo-source", method, *args, **kwargs)

    async def list_photos(self, source: str, **filters: Any) -> list[dict[str, Any]]:
        result = await self._call("list_photos", source, **filters)
        return [dict(item) for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    async def list_albums(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]:
        result = await self._call("list_albums", source, limit=limit)
        return [dict(item) for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    async def search_photos(
        self,
        query: str,
        *,
        source: str,
        path_or_bucket: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        result = await self._call(
            "search_photos",
            query,
            source=source,
            path_or_bucket=path_or_bucket,
            limit=limit,
        )
        return [dict(item) for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    async def prefetch_photos(self, source: str, **filters: Any) -> dict[str, Any]:
        result = await self._call("prefetch_photos", source, **filters)
        return dict(result) if isinstance(result, dict) else {}

    async def get_metadata(
        self,
        source: str,
        photo_id: str,
        *,
        path_or_bucket: str,
    ) -> dict[str, Any] | None:
        result = await self._call(
            "get_metadata",
            source,
            photo_id,
            path_or_bucket=path_or_bucket,
        )
        return dict(result) if isinstance(result, dict) else None

    async def get_thumbnail(
        self,
        source: str,
        photo_id: str,
        *,
        path_or_bucket: str,
        max_size: int,
    ) -> str | None:
        result = await self._call(
            "get_thumbnail",
            source,
            photo_id,
            path_or_bucket=path_or_bucket,
            max_size=max_size,
        )
        return str(result) if result else None

    async def probe_local_availability(
        self,
        source: str,
        photo_id: str,
        *,
        path_or_bucket: str,
    ) -> dict[str, Any]:
        probe: dict[str, Any] = {
            "photo_id": photo_id,
            "source": source,
            "local_path_available": None,
            "local_path": "",
        }
        if source != "apple":
            return probe
        try:
            from photos_mcp.facade.common import load_vendor_server

            module = load_vendor_server("photo-source")
            apple_source = module._get_apple_source()
            vendor_probe = apple_source.probe_local_availability(photo_id)
            if isinstance(vendor_probe, dict):
                probe.update({
                    "local_path_available": vendor_probe.get("local_path_available"),
                    "local_path": str(vendor_probe.get("local_path") or ""),
                })
        except Exception:
            pass
        if probe["local_path_available"] is not None:
            return probe

        try:
            items = await self.list_photos(source, path_or_bucket=path_or_bucket, limit=100)
        except Exception:
            return probe
        for item in items:
            candidate_id = str(item.get("photo_id") or item.get("id") or "")
            if candidate_id == photo_id:
                local_path = str(item.get("path") or "")
                probe["local_path_available"] = bool(local_path)
                probe["local_path"] = local_path
                return probe
        return probe

    def latest_fetch_detail(self, source: str, photo_id: str) -> dict[str, Any] | None:
        if source != "apple":
            return None
        try:
            from photos_mcp.facade.common import load_vendor_server

            apple_source = load_vendor_server("photo-source")._get_apple_source()
            detail = getattr(apple_source, "_last_fetch_details", {}).get(photo_id)
            return dict(detail) if isinstance(detail, dict) else None
        except Exception:
            return None
