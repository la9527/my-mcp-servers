"""Build private recommendation stories and revocable public share packages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from photos_mcp.application.story_generation import ensure_recommendation_story
from photos_mcp.infrastructure.persistence.run_repository import RunRepository


DEFAULT_SHARE_DAYS = 30
MAX_SHARE_DAYS = 30
SESSION_HOURS = 12
_PBKDF2_ITERATIONS = 240_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _passcode_hash(passcode: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        passcode.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return _b64encode(digest)


def build_recommendation_story(
    repository: RunRepository,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an idempotent evidence-backed StoryManifest."""
    return ensure_recommendation_story(repository, now=now)


class StoryShareService:
    def __init__(
        self,
        repository: RunRepository,
        *,
        session_secret: bytes,
        now_fn=_utcnow,
    ) -> None:
        if len(session_secret) < 32:
            raise ValueError("Share session secret must be at least 32 bytes")
        self.repository = repository
        self._secret = session_secret
        self._now_fn = now_fn

    def create(
        self,
        story: dict[str, Any],
        *,
        duration_days: int = DEFAULT_SHARE_DAYS,
        download_enabled: bool = True,
        passcode: str = "",
    ) -> tuple[dict[str, Any], str]:
        days = max(1, min(int(duration_days), MAX_SHARE_DAYS))
        code = passcode.strip() or f"{secrets.randbelow(1_000_000):06d}"
        if len(code) < 6 or len(code) > 32:
            raise ValueError("Passcode must contain 6 to 32 characters")
        now = self._now_fn().astimezone(UTC)
        salt = secrets.token_bytes(16)
        share_id = secrets.token_urlsafe(18)
        public_photos = []
        public_id_by_ref: dict[str, str] = {}
        public_id_by_asset: dict[str, str] = {}
        for index, photo in enumerate(story.get("photos") or [], start=1):
            if not isinstance(photo, dict) or not photo.get("asset_id"):
                continue
            public_asset_id = secrets.token_urlsafe(12)
            photo_ref = str(photo.get("photo_ref") or "")
            public_id_by_asset[str(photo["asset_id"])] = public_asset_id
            if photo_ref:
                public_id_by_ref[photo_ref] = public_asset_id
            public_photos.append(
                {
                    "public_asset_id": public_asset_id,
                    "local_asset_id": str(photo["asset_id"]),
                    "photo_ref": photo_ref,
                    "sequence": index,
                    "capture_date": str(photo.get("capture_date") or ""),
                    "title": str(photo.get("title") or "사진")[:100],
                    "summary": str(photo.get("summary") or "")[:300],
                    "alt": str(photo.get("alt") or "공유 사진")[:160],
                    "location": str(photo.get("share_location") or "")[:80],
                    "location_status": str(photo.get("location_status") or "unknown")[:40],
                }
            )
        public_chapters = []
        for chapter in story.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            ids = [
                public_id_by_ref[ref]
                for ref in (str(value) for value in chapter.get("photo_refs") or [])
                if ref in public_id_by_ref
            ]
            if not ids:
                ids = [
                    public_id_by_asset[asset_id]
                    for asset_id in (str(value) for value in chapter.get("asset_ids") or [])
                    if asset_id in public_id_by_asset
                ]
            if not ids:
                continue
            location_groups: dict[str, list[str]] = {}
            for photo in public_photos:
                public_id = str(photo.get("public_asset_id") or "")
                if public_id not in ids:
                    continue
                label = str(photo.get("location") or "").strip() or "위치 미상"
                location_groups.setdefault(label, []).append(public_id)
            public_chapters.append(
                {
                    "chapter_id": str(chapter.get("chapter_id") or "")[:40],
                    "date": str(chapter.get("date") or "")[:24],
                    "title": str(chapter.get("title") or "사진 모음")[:100],
                    "summary": str(chapter.get("summary") or "")[:500],
                    "public_asset_ids": ids,
                    "locations": sorted(
                        {
                            str(photo.get("location") or "")[:80]
                            for photo in public_photos
                            if photo.get("public_asset_id") in ids
                            and str(photo.get("location") or "")
                        }
                    ),
                    "location_groups": [
                        {
                            "label": label,
                            "status": (
                                "unknown"
                                if label == "위치 미상"
                                else "contextual_estimate"
                                if any(
                                    photo.get("location_status") == "contextual_estimate"
                                    for photo in public_photos
                                    if photo.get("public_asset_id") in group_ids
                                )
                                else "confirmed_gps"
                            ),
                            "public_asset_ids": group_ids,
                        }
                        for label, group_ids in location_groups.items()
                    ],
                }
            )
        overview_groups: dict[str, list[dict[str, Any]]] = {}
        for photo in public_photos:
            label = str(photo.get("location") or "").strip() or "위치 미상"
            overview_groups.setdefault(label, []).append(photo)
        package = {
            "share_id": share_id,
            "story_id": str(story.get("story_id") or ""),
            "story_revision": max(1, int(story.get("revision") or 1)),
            "status": "active",
            "session_version": 1,
            "privacy_profile": "share_safe",
            "title": str(story.get("title") or "사진 이야기")[:160],
            "subtitle": str(story.get("subtitle") or "")[:300],
            "closing": str(story.get("closing") or "")[:300],
            "theme": (
                str(story.get("theme"))
                if str(story.get("theme"))
                in {"day_in_life", "weekend_journal", "seasonal_digest", "mixed_archive"}
                else "mixed_archive"
            ),
            "date_from": str(story.get("date_from") or ""),
            "date_to": str(story.get("date_to") or ""),
            "photos": public_photos,
            "chapters": public_chapters,
            "location_overview": [
                {
                    "label": label,
                    "count": len(items),
                    "status": (
                        "unknown"
                        if label == "위치 미상"
                        else "contextual_estimate"
                        if any(item.get("location_status") == "contextual_estimate" for item in items)
                        else "confirmed_gps"
                    ),
                }
                for label, items in overview_groups.items()
            ],
            "download_enabled": bool(download_enabled),
            "derivative_policy": "share-jpeg-2048-q88-v1",
            "passcode_salt": _b64encode(salt),
            "passcode_hash": _passcode_hash(code, salt),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=days)).isoformat(),
        }
        stored = self.repository.upsert_shared_story_package(package)
        return self.public_metadata(stored, include_story=True), code

    def get_active(self, share_id: str) -> tuple[dict[str, Any] | None, str]:
        package = self.repository.get_shared_story_package(share_id)
        if package is None:
            return None, "missing"
        if str(package.get("status") or "") != "active":
            return None, "revoked"
        try:
            expired = _parse_time(str(package.get("expires_at") or "")) <= self._now_fn().astimezone(UTC)
        except ValueError:
            expired = True
        if expired:
            self.repository.upsert_shared_story_package(
                {
                    **package,
                    "status": "expired",
                    "session_version": int(package.get("session_version") or 1) + 1,
                    "expired_at": self._now_fn().astimezone(UTC).isoformat(),
                }
            )
            return None, "expired"
        return package, "active"

    def expire_due(self) -> list[str]:
        expired: list[str] = []
        for package in self.repository.list_shared_story_packages(limit=500):
            share_id = str(package.get("share_id") or "")
            if not share_id or str(package.get("status") or "") != "active":
                continue
            _active, state = self.get_active(share_id)
            if state == "expired":
                expired.append(share_id)
        return expired

    def verify_passcode(self, share_id: str, passcode: str) -> bool:
        package, state = self.get_active(share_id)
        if state != "active" or package is None:
            return False
        try:
            salt = _b64decode(str(package["passcode_salt"]))
            expected = str(package["passcode_hash"])
        except (KeyError, ValueError):
            return False
        return hmac.compare_digest(expected, _passcode_hash(passcode, salt))

    def issue_session(self, share_id: str) -> str:
        package, state = self.get_active(share_id)
        if state != "active" or package is None:
            raise ValueError("Share is not active")
        expires = min(
            _parse_time(str(package["expires_at"])),
            self._now_fn().astimezone(UTC) + timedelta(hours=SESSION_HOURS),
        )
        payload = {
            "s": share_id,
            "v": int(package.get("session_version") or 1),
            "e": int(expires.timestamp()),
        }
        encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify_session(self, share_id: str, token: str) -> bool:
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return False
            payload = json.loads(_b64decode(encoded))
            package, state = self.get_active(share_id)
            return bool(
                state == "active"
                and package is not None
                and payload.get("s") == share_id
                and int(payload.get("v") or 0) == int(package.get("session_version") or 1)
                and int(payload.get("e") or 0) > int(self._now_fn().timestamp())
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            return False

    def revoke(self, share_id: str) -> bool:
        package = self.repository.get_shared_story_package(share_id)
        if package is None:
            return False
        updated = {
            **package,
            "status": "revoked",
            "session_version": int(package.get("session_version") or 1) + 1,
            "revoked_at": self._now_fn().astimezone(UTC).isoformat(),
        }
        self.repository.upsert_shared_story_package(updated)
        return True

    @staticmethod
    def find_photo(package: dict[str, Any], public_asset_id: str) -> dict[str, Any] | None:
        return next(
            (
                photo
                for photo in package.get("photos") or []
                if isinstance(photo, dict)
                and hmac.compare_digest(str(photo.get("public_asset_id") or ""), public_asset_id)
            ),
            None,
        )

    @staticmethod
    def public_metadata(package: dict[str, Any], *, include_story: bool) -> dict[str, Any]:
        safe = {
            key: package.get(key)
            for key in (
                "share_id",
                "story_id",
                "story_revision",
                "status",
                "title",
                "subtitle",
                "closing",
                "theme",
                "date_from",
                "date_to",
                "download_enabled",
                "created_at",
                "expires_at",
                "location_overview",
            )
        }
        if include_story:
            safe["photos"] = [
                {key: photo.get(key) for key in (
                    "public_asset_id", "photo_ref", "sequence", "capture_date", "title",
                    "summary", "alt", "location",
                    "location_status",
                )}
                for photo in package.get("photos") or []
                if isinstance(photo, dict)
            ]
            safe["chapters"] = [
                {
                    key: chapter.get(key)
                    for key in (
                        "chapter_id",
                        "date",
                        "title",
                        "summary",
                        "locations",
                        "public_asset_ids",
                        "location_groups",
                    )
                }
                for chapter in package.get("chapters") or []
                if isinstance(chapter, dict)
            ]
        return safe
