"""Build private recommendation stories and revocable public share packages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from photos_mcp.application.story_generation import (
    StoryIdentityRepository,
    ensure_recommendation_story,
    normalize_story_person_evidence,
)
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


def _identity_revocation_tag(secret: bytes, person_ref: str) -> str:
    """Build an internal-only, unlinkable share revocation index key."""

    return _b64encode(
        hmac.new(
            secret,
            f"person-share-revocation-v1\0{person_ref}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )


def build_recommendation_story(
    repository: RunRepository,
    *,
    now: datetime | None = None,
    identity_repository: StoryIdentityRepository | None = None,
) -> dict[str, Any]:
    """Return an idempotent evidence-backed StoryManifest."""
    return ensure_recommendation_story(
        repository,
        now=now,
        identity_repository=identity_repository,
    )


def _public_people_fields(
    person_refs: set[str],
    people_by_ref: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if any(person_ref not in people_by_ref for person_ref in person_refs):
        raise ValueError("unknown_story_person_ref")
    names = [people_by_ref[person_ref]["display_name"] for person_ref in sorted(person_refs)]
    if not names:
        return {}
    return {
        "confirmed_people": [{"display_name": name} for name in names],
        "people_caption": f"함께한 사람: {', '.join(names)}",
    }


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
        include_person_names: bool = False,
        identity_repository: StoryIdentityRepository | None = None,
    ) -> tuple[dict[str, Any], str]:
        days = max(1, min(int(duration_days), MAX_SHARE_DAYS))
        code = passcode.strip() or f"{secrets.randbelow(1_000_000):06d}"
        if len(code) < 6 or len(code) > 32:
            raise ValueError("Passcode must contain 6 to 32 characters")
        now = self._now_fn().astimezone(UTC)
        salt = secrets.token_bytes(16)
        share_id = secrets.token_urlsafe(18)
        story_photos = [
            photo
            for photo in story.get("photos") or []
            if isinstance(photo, dict) and photo.get("asset_id")
        ]
        local_asset_ids = [str(photo["asset_id"]) for photo in story_photos]
        if include_person_names and identity_repository is None:
            raise ValueError("identity_repository_required")
        family_identity = normalize_story_person_evidence(
            (
                identity_repository.build_story_person_evidence(
                    local_asset_ids,
                    audience="family_share",
                )
                if include_person_names and identity_repository is not None
                else {"person_refs": [], "assets": []}
            ),
            local_asset_ids=local_asset_ids,
        )
        public_photos = []
        public_id_by_ref: dict[str, str] = {}
        public_id_by_asset: dict[str, str] = {}
        for index, photo in enumerate(story_photos, start=1):
            public_asset_id = secrets.token_urlsafe(12)
            photo_ref = str(photo.get("photo_ref") or "")
            public_id_by_asset[str(photo["asset_id"])] = public_asset_id
            if photo_ref:
                public_id_by_ref[photo_ref] = public_asset_id
            family_refs = set(family_identity["refs_by_asset"][str(photo["asset_id"])])
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
                    "latitude": photo.get("latitude"),
                    "longitude": photo.get("longitude"),
                    "google_place_id": str(photo.get("google_place_id") or "")[:255],
                    "poi_type": str(photo.get("poi_type") or "")[:80],
                    "resolution_status": str(
                        photo.get("resolution_status") or "coordinate_only"
                    )[:40],
                    **_public_people_fields(
                        family_refs,
                        family_identity["people_by_ref"],
                    ),
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
            public_location_groups = []
            for label, group_ids in location_groups.items():
                mapped = next(
                    (
                        photo
                        for photo in public_photos
                        if photo.get("public_asset_id") in group_ids
                        and photo.get("latitude") is not None
                        and photo.get("longitude") is not None
                    ),
                    None,
                )
                group = {
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
                if mapped is not None:
                    group["map"] = {
                        "latitude": round(float(mapped["latitude"]), 7),
                        "longitude": round(float(mapped["longitude"]), 7),
                        "google_place_id": str(mapped.get("google_place_id") or "")[:255],
                    }
                public_location_groups.append(group)
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
                    "location_groups": public_location_groups,
                    **_public_people_fields(
                        {
                            person_ref
                            for photo in public_photos
                            if photo.get("public_asset_id") in ids
                            for person_ref in family_identity["refs_by_asset"].get(
                                str(photo.get("local_asset_id") or ""),
                                [],
                            )
                        },
                        family_identity["people_by_ref"],
                    ),
                }
            )
        overview_groups: dict[str, list[dict[str, Any]]] = {}
        for photo in public_photos:
            label = str(photo.get("location") or "").strip() or "위치 미상"
            overview_groups.setdefault(label, []).append(photo)
        family_photo_counts: dict[str, int] = {}
        for photo in public_photos:
            for person_ref in set(
                family_identity["refs_by_asset"].get(
                    str(photo.get("local_asset_id") or ""),
                    [],
                )
            ):
                family_photo_counts[person_ref] = family_photo_counts.get(person_ref, 0) + 1
        package = {
            "share_id": share_id,
            "story_id": str(story.get("story_id") or ""),
            "story_revision": max(1, int(story.get("revision") or 1)),
            "status": "active",
            "session_version": 1,
            "privacy_profile": "family_detailed",
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
            "people_overview": [
                {
                    "display_name": family_identity["people_by_ref"][person_ref]["display_name"],
                    "photo_count": family_photo_counts[person_ref],
                }
                for person_ref in sorted(family_photo_counts)
            ],
            "person_names_included": bool(include_person_names),
            # Private package-only index. Public projections intentionally omit
            # both these keyed tags and repository person identifiers.
            "identity_revocation_tags": sorted(
                _identity_revocation_tag(self._secret, person_ref)
                for person_ref in family_photo_counts
            ),
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

    def revoke_for_person(self, person_identity_id: str) -> tuple[str, ...]:
        """Revoke active name-bearing shares associated with one private id."""

        tag = _identity_revocation_tag(self._secret, person_identity_id)
        revoked: list[str] = []
        for package in self.repository.list_shared_story_packages(limit=500):
            if str(package.get("status") or "") != "active":
                continue
            tags = {
                str(value)
                for value in package.get("identity_revocation_tags") or []
                if str(value)
            }
            if tag not in tags:
                continue
            share_id = str(package.get("share_id") or "")
            if share_id and self.revoke(share_id):
                revoked.append(share_id)
        return tuple(revoked)

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
                "people_overview",
                "person_names_included",
            )
        }
        if include_story:
            safe["photos"] = [
                {key: photo.get(key) for key in (
                    "public_asset_id", "photo_ref", "sequence", "capture_date", "title",
                    "summary", "alt", "location",
                    "location_status", "latitude", "longitude",
                    "google_place_id", "poi_type", "resolution_status",
                    "confirmed_people", "people_caption",
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
                        "confirmed_people",
                        "people_caption",
                    )
                }
                for chapter in package.get("chapters") or []
                if isinstance(chapter, dict)
            ]
            for photo in safe["photos"]:
                if not photo.get("confirmed_people"):
                    photo.pop("confirmed_people", None)
                    photo.pop("people_caption", None)
            for chapter in safe["chapters"]:
                if not chapter.get("confirmed_people"):
                    chapter.pop("confirmed_people", None)
                    chapter.pop("people_caption", None)
        return safe
