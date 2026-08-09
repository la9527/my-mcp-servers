"""Capability-aware registry for concrete photo provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from photos_mcp.domain.models.source import (
    PhotoProvider,
    SourceCapabilities,
    SourceCapability,
    SourceDescriptor,
)


@dataclass(frozen=True, slots=True)
class SourceAdapterBinding:
    descriptor: SourceDescriptor
    capabilities: SourceCapabilities
    catalog: Any | None = None
    picker: Any | None = None
    content: Any | None = None
    destination: Any | None = None

    def adapter_for(self, capability: SourceCapability) -> Any:
        if not self.capabilities.supports(capability):
            raise LookupError(
                f"{self.descriptor.source_id} does not support {capability.value}"
            )
        if capability in {
            SourceCapability.CATALOG_BROWSE,
            SourceCapability.LIST_ALBUMS,
            SourceCapability.DATE_FILTER,
        }:
            adapter = self.catalog
        elif capability is SourceCapability.INTERACTIVE_PICKER:
            adapter = self.picker
        elif capability in {
            SourceCapability.THUMBNAIL_ACCESS,
            SourceCapability.ORIGINAL_CONTENT_ACCESS,
            SourceCapability.PERSISTENT_ASSET_ACCESS,
        }:
            adapter = self.content
        elif capability is SourceCapability.WRITE_DESTINATION:
            adapter = self.destination
        else:
            return self
        if adapter is None:
            raise LookupError(
                f"{self.descriptor.source_id} has no adapter for {capability.value}"
            )
        return adapter


class SourceRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, SourceAdapterBinding] = {}

    def register(self, binding: SourceAdapterBinding) -> None:
        source_id = binding.descriptor.source_id
        if source_id in self._bindings:
            raise ValueError(f"source already registered: {source_id}")
        self._bindings[source_id] = binding

    def get(self, source_id: str) -> SourceAdapterBinding:
        try:
            return self._bindings[source_id]
        except KeyError as exc:
            raise LookupError(f"unknown source: {source_id}") from exc

    def list_bindings(self) -> tuple[SourceAdapterBinding, ...]:
        return tuple(self._bindings.values())


_LEGACY_PROVIDERS = {
    "apple": PhotoProvider.APPLE_PHOTOS,
    "apple_photos": PhotoProvider.APPLE_PHOTOS,
    "local": PhotoProvider.LOCAL_FILES,
    "local_files": PhotoProvider.LOCAL_FILES,
    "google": PhotoProvider.GOOGLE_PHOTOS,
    "google_photos": PhotoProvider.GOOGLE_PHOTOS,
    "gcs": PhotoProvider.GCS,
}


def descriptor_from_legacy_source(
    source: str,
    *,
    locator: str = "",
    account_id: str = "",
) -> SourceDescriptor:
    normalized = source.strip().lower()
    try:
        provider = _LEGACY_PROVIDERS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported photo source: {source}") from exc
    identity = account_id.strip() or "default"
    source_id = f"{provider.value}:{identity}"
    if provider in {PhotoProvider.LOCAL_FILES, PhotoProvider.GCS} and locator:
        source_id = f"{source_id}:{locator}"
    return SourceDescriptor(
        source_id=source_id,
        provider=provider,
        account_id=account_id,
        locator=locator,
    )

