"""Local-file provider data transfer models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalPhoto:
    path: str
    name: str
    modified_at: float
    size_bytes: int
    pixel_width: int = 0
    pixel_height: int = 0

