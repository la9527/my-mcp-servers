"""Data models for photo-ranker MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    BIRTHDAY = "birthday"
    TRAVEL = "travel"
    GRADUATION = "graduation"
    MEAL = "meal"
    DAILY = "daily"
    CELEBRATION = "celebration"
    OUTDOOR = "outdoor"
    PORTRAIT = "portrait"
    OTHER = "other"


@dataclass
class QualityScore:
    photo_id: str | None
    aesthetic_score: float  # 0-50 (mapped from 0-10)
    technical_score: float  # 0-50
    total: float  # 0-100
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "photo_id": self.photo_id,
            "aesthetic_score": round(self.aesthetic_score, 2),
            "technical_score": round(self.technical_score, 2),
            "total": round(self.total, 2),
            "notes": self.notes,
        }


@dataclass
class FaceResult:
    bbox: tuple[int, int, int, int]  # top, right, bottom, left
    embedding: list[float] | None = None
    expression: str = "unknown"
    gender: str = ""  # "male" or "female"
    age: int = 0

    def to_dict(self) -> dict:
        return {
            "bbox": list(self.bbox),
            "embedding_dim": len(self.embedding) if self.embedding else 0,
            "expression": self.expression,
            "gender": self.gender,
            "age": self.age,
        }


@dataclass
class SceneDescription:
    scene: str
    people_count: int
    is_family_photo: bool
    expressions: list[str]
    event_type: EventType
    event_confidence: float
    quality_notes: str
    meaningful_score: int  # 1-10
    raw_json: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scene": self.scene,
            "people_count": self.people_count,
            "is_family_photo": self.is_family_photo,
            "expressions": self.expressions,
            "event_type": self.event_type.value,
            "event_confidence": round(self.event_confidence, 3),
            "quality_notes": self.quality_notes,
            "meaningful_score": self.meaningful_score,
        }


@dataclass
class DuplicateGroup:
    group_id: str
    photo_ids: list[str]
    representative_id: str

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "photo_ids": self.photo_ids,
            "representative_id": self.representative_id,
        }


@dataclass
class RankedPhoto:
    photo_id: str
    total_score: float
    quality_score: float
    family_score: float
    event_score: float
    uniqueness_score: float
    scene_description: str
    event_type: str
    faces_detected: int
    known_persons: list[str] = field(default_factory=list)
    has_gps: bool = False
    meaningful_score: int = 5
    capture_date: str = ""

    def to_dict(self) -> dict:
        d = {
            "photo_id": self.photo_id,
            "total_score": round(self.total_score, 2),
            "quality_score": round(self.quality_score, 2),
            "family_score": round(self.family_score, 2),
            "event_score": round(self.event_score, 2),
            "uniqueness_score": round(self.uniqueness_score, 2),
            "scene_description": self.scene_description,
            "event_type": self.event_type,
            "faces_detected": self.faces_detected,
            "known_persons": self.known_persons,
            "has_gps": self.has_gps,
            "meaningful_score": self.meaningful_score,
        }
        if self.capture_date:
            d["capture_date"] = self.capture_date
        return d
