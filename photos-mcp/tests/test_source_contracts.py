from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from photos_mcp.application.source_registry import (
    SourceAdapterBinding,
    SourceRegistry,
    descriptor_from_legacy_source,
)
from photos_mcp.domain.models.source import (
    AccessGrant,
    AccessGrantType,
    PhotoAssetRef,
    PhotoContentState,
    PhotoProvider,
    SourceCapabilities,
    SourceCapability,
)
from photos_mcp.domain.policies.source_policy import (
    SourcePolicy,
    SourcePolicyViolation,
)
from photos_mcp.photo_source_port import (
    legacy_source_capabilities,
    legacy_source_descriptor,
)


def test_legacy_sources_map_to_typed_descriptors() -> None:
    local = legacy_source_descriptor("local", path_or_bucket="/tmp/photos")
    google = descriptor_from_legacy_source("google", account_id="person@example.com")

    assert local.provider is PhotoProvider.LOCAL_FILES
    assert local.locator == "/tmp/photos"
    assert google.provider is PhotoProvider.GOOGLE_PHOTOS
    assert google.account_id == "person@example.com"


def test_google_photos_is_picker_only_and_blocks_face_processing() -> None:
    capabilities = legacy_source_capabilities("google")
    policy = SourcePolicy.for_provider(PhotoProvider.GOOGLE_PHOTOS)

    assert capabilities.interactive_picker is True
    assert capabilities.catalog_browse is False
    assert capabilities.face_quality is False
    assert capabilities.face_clustering is False
    with pytest.raises(SourcePolicyViolation):
        policy.validate_analysis(face_quality=False, face_clustering=True)


def test_expired_grant_and_asset_content_are_not_ready() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    grant = AccessGrant(
        grant_id="grant-1",
        source_id="google_photos:default",
        grant_type=AccessGrantType.PICKER_SESSION,
        expires_at=now - timedelta(seconds=1),
    )
    asset = PhotoAssetRef(
        source_id="google_photos:default",
        provider_asset_id="item-1",
        content_state=PhotoContentState.MATERIALIZED,
        content_expires_at=now - timedelta(seconds=1),
    )

    assert grant.is_active(now=now) is False
    assert asset.is_content_ready(now=now) is False


def test_registry_resolves_adapters_by_capability() -> None:
    registry = SourceRegistry()
    descriptor = descriptor_from_legacy_source("apple")
    catalog = object()
    content = object()
    registry.register(
        SourceAdapterBinding(
            descriptor=descriptor,
            capabilities=SourceCapabilities.for_provider(PhotoProvider.APPLE_PHOTOS),
            catalog=catalog,
            content=content,
            destination=object(),
        )
    )

    binding = registry.get(descriptor.source_id)

    assert binding.adapter_for(SourceCapability.CATALOG_BROWSE) is catalog
    assert binding.adapter_for(SourceCapability.ORIGINAL_CONTENT_ACCESS) is content
    with pytest.raises(LookupError):
        binding.adapter_for(SourceCapability.INTERACTIVE_PICKER)

