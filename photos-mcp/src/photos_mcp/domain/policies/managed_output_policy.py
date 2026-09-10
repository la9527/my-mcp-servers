"""Prevent PhotosMcp-managed output copies from becoming analysis inputs."""

from __future__ import annotations

from typing import Any


def managed_google_output_asset_keys(
    repository: Any,
    *,
    source_id: str,
) -> set[str]:
    """Return Picker stable keys created by PhotosMcp Google album writes."""

    list_receipts = getattr(repository, "list_recommendation_destination_receipts", None)
    if not callable(list_receipts):
        return set()
    keys: set[str] = set()
    for receipt in list_receipts():
        if str(receipt.get("destination_type") or "") != "google_album":
            continue
        if str(receipt.get("state") or "") != "completed":
            continue
        media_item_id = str(receipt.get("provider_media_item_id") or "")
        if media_item_id:
            keys.add(f"{source_id}:{media_item_id}")
    return keys
