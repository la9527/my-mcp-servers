"""Shared face-level people review application service for Mac and Android."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from photos_mcp.application.person_identity_repository import PersonIdentityRepository
from photos_mcp.application.people_automation_policy import PEOPLE_AUTOMATION_POLICY_VERSION


@dataclass(frozen=True)
class PeopleReviewPage:
    items: tuple[dict[str, Any], ...]
    next_offset: int | None


class PeopleWorkspaceService:
    """One command/query boundary shared by every owner-facing client."""

    def __init__(
        self,
        identity_repository: PersonIdentityRepository,
        *,
        run_repository: Any | None = None,
        private_root: Path | None = None,
    ) -> None:
        self.identity_repository = identity_repository
        self.run_repository = run_repository
        self.private_root = (
            private_root or identity_repository.path.parent / "index-private"
        ).expanduser().resolve()

    def list_asset_reviews(
        self,
        *,
        state: str = "pending",
        offset: int = 0,
        limit: int = 24,
    ) -> PeopleReviewPage:
        bounded_limit = max(1, min(50, int(limit)))
        if state != "pending":
            raise ValueError("exception review queue only supports pending state")
        ids = self.identity_repository.list_actionable_asset_people_review_ids(
            offset=max(0, int(offset)),
            limit=bounded_limit + 1,
        )
        has_more = len(ids) > bounded_limit
        details = (self.asset_review_detail(asset_id) for asset_id in ids[:bounded_limit])
        items = tuple(item for item in details if item.get("actionable_face_count", 0) > 0)
        return PeopleReviewPage(
            items=items,
            next_offset=(max(0, int(offset)) + bounded_limit) if has_more else None,
        )

    def asset_review_detail(self, local_asset_id: str) -> dict[str, Any]:
        detail = self.identity_repository.asset_people_review_detail(local_asset_id)
        actionable_faces: list[dict[str, Any]] = []
        for raw_face in detail.get("faces") or []:
            review_kind = str(raw_face.get("review_kind") or "")
            if (
                review_kind not in {
                    "quick_confirmation",
                    "ambiguous_identity_match",
                    "conflicted_identity_match",
                    "promoted_new_person",
                }
                or str(raw_face.get("quality_tier") or "review_eligible")
                == "quality_suppressed"
                or str(raw_face.get("review_state") or "pending") != "pending"
                or raw_face.get("confirmed_person_identity_id")
            ):
                continue
            face = dict(raw_face)
            candidate_id = str(face.get("candidate_person_identity_id") or "")
            if (
                str(face.get("review_kind") or "") == "promoted_new_person"
                and candidate_id
            ):
                face["cluster_evidence"] = [
                    {
                        "face_observation_id": str(item["face_observation_id"]),
                        "local_asset_id": str(item["local_asset_id"]),
                        "review_crop_ref": str(item["crop_ref"]),
                    }
                    for item in self.identity_repository.candidate_identity_face_artifacts(
                        candidate_id,
                        limit=3,
                    )
                ]
            actionable_faces.append(face)
        asset: dict[str, Any] = {}
        if self.run_repository is not None:
            try:
                asset = self.run_repository.get_local_recommendation_asset_by_id(
                    local_asset_id
                ) or {}
            except (OSError, RuntimeError):
                asset = {}
        return {
            **detail,
            "faces": actionable_faces,
            "actionable_face_count": len(actionable_faces),
            "capture_date_local": str(asset.get("capture_date_local") or "")[:10],
            "source": str(asset.get("source") or asset.get("provider") or ""),
        }

    def provider_alias_review_detail(self, local_asset_id: str) -> dict[str, Any]:
        """Return every usable face crop for an explicit provider-name hint.

        The normal review queue intentionally hides low-quality faces. A user
        who opened an Apple Photos name hint, however, needs to choose between
        all detected people in that exact photo. System-suppressed crops are
        therefore visible here, while owner-resolved, rejected or explicitly
        ignored faces remain hidden.
        """

        try:
            detail = self.identity_repository.asset_people_review_detail(local_asset_id)
        except KeyError:
            detail = {
                "local_asset_id": local_asset_id,
                "review_revision": 0,
                "review_state": "pending",
                "index_state": "pending",
                "detected_face_count": 0,
                "faces": [],
                "aliases": [],
            }
        choices: list[dict[str, Any]] = []
        for raw_face in detail.get("faces") or []:
            if raw_face.get("confirmed_person_identity_id"):
                continue
            review_state = str(raw_face.get("review_state") or "pending")
            review_kind = str(raw_face.get("review_kind") or "")
            review_revision = int(raw_face.get("face_review_revision") or 0)
            system_suppressed = (
                review_kind == "quality_suppressed"
                and review_state == "ignored"
                and review_revision <= 1
            )
            if review_state in {"resolved", "rejected"} or (
                review_state == "ignored" and not system_suppressed
            ):
                continue
            face = dict(raw_face)
            face["source_review_state"] = review_state
            face["review_state"] = "pending" if system_suppressed else review_state
            face["manual_alias_choice"] = True
            choices.append(face)

        asset: dict[str, Any] = {}
        if self.run_repository is not None:
            try:
                asset = self.run_repository.get_local_recommendation_asset_by_id(
                    local_asset_id
                ) or {}
            except (OSError, RuntimeError):
                asset = {}
        numbered_ref = f"numbered-previews/{local_asset_id}.jpg"
        try:
            self.artifact_path(numbered_ref)
        except (ValueError, FileNotFoundError):
            numbered_ref = ""
        return {
            **detail,
            "faces": choices,
            "actionable_face_count": len(choices),
            "capture_date_local": str(asset.get("capture_date_local") or "")[:10],
            "source": str(asset.get("source") or asset.get("provider") or ""),
            "numbered_context_ref": numbered_ref,
        }

    def list_provider_alias_reviews(self, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        """Project pending aliases as de-duplicated face-level photo reviews."""

        asset_ids: list[str] = []
        for alias in self.identity_repository.list_provider_person_aliases(
            alias_state="candidate"
        ):
            if alias.local_asset_id not in asset_ids:
                asset_ids.append(alias.local_asset_id)
            if len(asset_ids) >= max(1, min(50, int(limit))):
                break
        return tuple(self.provider_alias_review_detail(asset_id) for asset_id in asset_ids)

    def identity_choices(self) -> tuple[dict[str, Any], ...]:
        choices: list[dict[str, Any]] = []
        for identity in self.identity_repository.list_identities(
            identity_statuses={"user_confirmed"}
        ):
            if identity.name_status != "user_confirmed" or not identity.display_name.strip():
                continue
            choices.append(
                {
                    "person_identity_id": identity.person_identity_id,
                    "identity_revision": identity.identity_revision,
                    "display_name": identity.display_name,
                    "linked_photo_count": self.identity_repository.person_photo_count(
                        identity.person_identity_id
                    ),
                    "representative_face_ref": str(
                        (
                            self.identity_repository.representative_face_artifact(
                                identity.person_identity_id
                            )
                            or {}
                        ).get("crop_ref")
                        or ""
                    ),
                    "automation_profile": (
                        self.identity_repository.ensure_identity_automation_profile(
                            identity.person_identity_id,
                            policy_version=PEOPLE_AUTOMATION_POLICY_VERSION,
                        )
                        or {
                            "profile_revision": 0,
                            "auto_enabled": True,
                            "suspended": False,
                            "owner_confirmed_anchor_count": 0,
                            "independent_context_count": 0,
                            "maturity": "learning",
                        }
                    ),
                }
            )
        choices.sort(key=lambda value: (str(value["display_name"]), str(value["person_identity_id"])))
        return tuple(choices)

    def dashboard(self) -> dict[str, Any]:
        """Return the same exception-first People home model to every client."""

        choices = self.identity_choices()
        quick = 0
        ambiguous = 0
        promoted = 0
        for kind, count in self.identity_repository.actionable_review_kind_counts().items():
            if kind == "promoted_new_person":
                promoted += count
            elif kind in {"ambiguous_identity_match", "conflicted_identity_match"}:
                ambiguous += count
            else:
                quick += count
        return {
            "exception_count": quick + ambiguous + promoted,
            "quick_confirmation_count": quick,
            "ambiguous_match_count": ambiguous,
            "promoted_new_person_count": promoted,
            "automatic_assignment_count": self.identity_repository.automatic_assignment_count(),
            "quality_suppressed_count": self.identity_repository.quality_suppressed_face_count(),
            "managed_person_count": len(choices),
            "people": choices,
            "policy_version": "exception-only-v1",
        }

    def artifact_path(self, artifact_ref: str) -> Path:
        relative = Path(str(artifact_ref or ""))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid artifact ref")
        resolved = (self.private_root / relative).resolve()
        if self.private_root not in resolved.parents or not resolved.is_file():
            raise FileNotFoundError(artifact_ref)
        return resolved

    def apply_asset_review(
        self,
        *,
        local_asset_id: str,
        expected_review_revision: int,
        assignments: Iterable[Mapping[str, Any]],
        face_decisions: Iterable[Mapping[str, Any]],
        device_fingerprint: str,
        idempotency_key: str,
        request_hash: str,
        actor: str,
    ) -> dict[str, Any]:
        return self.identity_repository.apply_asset_people_review(
            local_asset_id=local_asset_id,
            expected_review_revision=expected_review_revision,
            assignments=assignments,
            face_decisions=face_decisions,
            device_fingerprint=device_fingerprint,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            actor=actor,
        )

    @staticmethod
    def stable_revision(value: Any) -> str:
        return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:20]
