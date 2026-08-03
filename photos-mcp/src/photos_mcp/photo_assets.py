"""Common source asset shape shared by browse and analyze operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PhotoAsset:
    asset_id: str
    source: str
    local_path_available: bool
    readiness: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source: str) -> "PhotoAsset":
        asset_id = str(payload.get("photo_id") or payload.get("id") or "")
        raw_local = payload.get("local_path_available")
        # Apple needs its original downloaded. Other supported sources can provide
        # thumbnail bytes directly and therefore do not share that iCloud constraint.
        local_available = bool(raw_local) if raw_local is not None else bool(payload.get("path"))
        ready = local_available if source == "apple" else bool(asset_id)
        return cls(
            asset_id=asset_id,
            source=source,
            local_path_available=local_available,
            readiness="ready" if ready else "cloud_only",
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "local_path_available": self.local_path_available,
            "readiness": self.readiness,
            "analyze_recommended": self.readiness == "ready",
        }
