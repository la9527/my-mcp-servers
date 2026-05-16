"""Data models for photo-source MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Photo:
    id: str
    filename: str
    date_taken: str  # ISO 8601
    source: str  # "apple_photos" | "gcs" | "local"
    path: str
    width: int
    height: int
    albums: list[str] = field(default_factory=list)
    persons: list[str] = field(default_factory=list)
    gps: dict | None = None  # {"lat": float, "lon": float}

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "filename": self.filename,
            "date_taken": self.date_taken,
            "source": self.source,
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "albums": self.albums,
            "persons": self.persons,
        }
        if self.gps:
            d["gps"] = self.gps
        return d


@dataclass
class PhotoMetadata:
    photo_id: str
    filename: str
    date_taken: str
    camera_make: str = ""
    camera_model: str = ""
    focal_length: float = 0.0
    exposure_time: str = ""
    iso: int = 0
    gps: dict | None = None
    albums: list[str] = field(default_factory=list)
    persons: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "photo_id": self.photo_id,
            "filename": self.filename,
            "date_taken": self.date_taken,
            "camera_make": self.camera_make,
            "camera_model": self.camera_model,
            "focal_length": self.focal_length,
            "exposure_time": self.exposure_time,
            "iso": self.iso,
            "gps": self.gps,
            "albums": self.albums,
            "persons": self.persons,
            "keywords": self.keywords,
        }


@dataclass
class ExportResult:
    exported: list[str]
    failed: list[str]
    dest_dir: str

    def to_dict(self) -> dict:
        return {
            "exported": self.exported,
            "failed": self.failed,
            "dest_dir": self.dest_dir,
            "total_exported": len(self.exported),
            "total_failed": len(self.failed),
        }
