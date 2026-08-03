import pytest

from photos_mcp.facade.run_service import _selected_photo_probe
from photos_mcp.photo_assets import PhotoAsset
from photos_mcp.state import PhotosMcpStateStore


def test_apple_asset_readiness_has_a_persistable_payload() -> None:
    asset = PhotoAsset.from_payload(
        {"id": "apple-id", "local_path_available": True},
        source="apple",
    )

    assert asset.as_payload()["readiness"] == "ready"
    assert asset.as_payload()["asset_id"] == "apple-id"


def test_gcs_asset_is_analysis_ready_without_a_local_original() -> None:
    asset = PhotoAsset.from_payload({"id": "photos/image.jpg"}, source="gcs")

    assert asset.readiness == "ready"
    assert asset.local_path_available is False


def test_asset_readiness_is_isolated_to_the_owning_state_store() -> None:
    first = PhotosMcpStateStore(endpoint="http://one/mcp", health_endpoint="http://one/health")
    second = PhotosMcpStateStore(endpoint="http://two/mcp", health_endpoint="http://two/health")

    first.remember_photo_assets([
        {"source": "apple", "asset_id": "photo-1", "local_path_available": True, "readiness": "ready"}
    ])

    assert first.get_photo_asset("apple", "photo-1")["readiness"] == "ready"
    assert second.get_photo_asset("apple", "photo-1") is None


def test_expired_asset_readiness_is_not_reused_after_restart() -> None:
    first = PhotosMcpStateStore(endpoint="http://one/mcp", health_endpoint="http://one/health")
    first.remember_photo_assets([
        {"source": "apple", "asset_id": "photo-expired", "local_path_available": True, "readiness": "ready"}
    ])
    restarted = PhotosMcpStateStore(
        endpoint="http://one/mcp",
        health_endpoint="http://one/health",
        run_repository=first.run_repository,
        photo_asset_readiness_ttl_seconds=0.0,
    )

    assert restarted.get_photo_asset("apple", "photo-expired") is None


@pytest.mark.asyncio
async def test_analyze_probe_reuses_readiness_from_its_own_state_store(monkeypatch) -> None:
    store = PhotosMcpStateStore(endpoint="http://one/mcp", health_endpoint="http://one/health")
    store.remember_photo_assets([
        {"source": "apple", "asset_id": "photo-1", "local_path_available": True, "readiness": "ready"}
    ])

    def fail_vendor_load(_name: str):
        raise AssertionError("cached readiness should not load the Apple source")

    monkeypatch.setattr("photos_mcp.facade.common.load_vendor_server", fail_vendor_load)

    probe = await _selected_photo_probe("apple", "photo-1", "", store)

    assert probe["local_path_available"] is True
