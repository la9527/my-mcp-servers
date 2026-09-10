"""Privacy-preserving Android-original to Google Picker matching diagnostics.

The production location bridge must never guess a location from a filename or a
single perceptual hash.  This module computes several independent fingerprints
and keeps exact coordinates out of its result objects.  It is intentionally a
diagnostic boundary first: only grade A is eligible for automatic projection,
while grade B remains shadow-only until labelled samples prove it safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import Any, Iterable

import imagehash
from PIL import Image, ImageOps

try:  # pragma: no cover - registration availability depends on the runtime build.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp"})
MAX_DECODED_PIXELS = 120_000_000
NORMALIZED_LONG_EDGE = 2048
PHASH_SIZE = 16


@dataclass(frozen=True)
class AssetFingerprint:
    """Private matching evidence for one image.

    ``path`` and ``filename`` are used only inside the local process.  Public
    diagnostic payloads are produced by :func:`summarize_matching` and contain
    neither field nor exact GPS coordinates.
    """

    role: str
    path: Path
    safe_id: str
    filename: str
    mime_type: str
    file_sha256: str
    jpeg_codestream_sha256: str
    normalized_pixel_sha256: str
    perceptual_hash: str
    perceptual_hash_bits: int
    width: int
    height: int
    capture_time: datetime | None
    camera_make: str
    camera_model: str
    gps_present: bool
    metadata_source: str
    error: str = ""


@dataclass(frozen=True)
class MatchEvidence:
    file_sha256: bool
    jpeg_codestream_sha256: bool
    normalized_pixel_sha256: bool
    perceptual_distance: float | None
    dimensions_equal: bool
    dimensions_compatible: bool
    capture_delta_seconds: float | None
    filename_equal: bool
    camera_equal: bool

    @property
    def strong(self) -> bool:
        return bool(
            self.file_sha256
            or self.jpeg_codestream_sha256
            or self.normalized_pixel_sha256
        )


@dataclass(frozen=True)
class MatchDecision:
    android_safe_id: str
    picker_safe_id: str
    grade: str
    state: str
    reason: str
    evidence: MatchEvidence | None


def discover_images(root: str | Path) -> tuple[Path, ...]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"image directory does not exist: {base}")
    return tuple(
        path
        for path in sorted(base.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not path.name.startswith("._")
    )


def _jpeg_without_metadata_sha256(data: bytes, *, is_jpeg: bool) -> str:
    """Hash a JPEG after removing leading APPn and COM segments.

    Google or another exporter can remove EXIF without touching the encoded
    scan.  Hashing the remaining JPEG structure recovers that exact identity.
    Any malformed marker layout returns an empty digest rather than guessing.
    """

    if not is_jpeg:
        return ""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return ""
    kept = bytearray(data[:2])
    cursor = 2
    try:
        while cursor < len(data):
            marker_start = cursor
            if data[cursor] != 0xFF:
                return ""
            while cursor < len(data) and data[cursor] == 0xFF:
                cursor += 1
            if cursor >= len(data):
                return ""
            marker = data[cursor]
            cursor += 1
            if marker == 0xDA:  # Start of Scan: the rest is encoded image data.
                kept.extend(data[marker_start:])
                break
            if marker == 0xD9:  # Empty image, but still a valid marker boundary.
                kept.extend(data[marker_start:cursor])
                break
            if marker in {0x01, *range(0xD0, 0xD8)}:
                kept.extend(data[marker_start:cursor])
                continue
            if cursor + 2 > len(data):
                return ""
            segment_length = int.from_bytes(data[cursor : cursor + 2], "big")
            if segment_length < 2 or cursor + segment_length > len(data):
                return ""
            segment_end = cursor + segment_length
            is_metadata = 0xE0 <= marker <= 0xEF or marker == 0xFE
            if not is_metadata:
                kept.extend(data[marker_start:segment_end])
            cursor = segment_end
    except (IndexError, ValueError):
        return ""
    return hashlib.sha256(kept).hexdigest() if len(kept) > 2 else ""


def _parse_datetime(value: Any, offset: Any = "") -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    if len(normalized) >= 10 and normalized[4] == ":" and normalized[7] == ":":
        normalized = f"{normalized[:4]}-{normalized[5:7]}-{normalized[8:]}"
    offset_text = str(offset or "").strip()
    if offset_text and len(normalized) == 19:
        normalized += offset_text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_asset_id(role: str, root: Path, path: Path) -> str:
    relative = str(path.resolve().relative_to(root.resolve()))
    return hashlib.sha256(f"{role}\0{relative}".encode("utf-8")).hexdigest()[:20]


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _picker_sidecar(path: Path) -> dict[str, Any]:
    sidecar = path.with_name(f"{path.name}.photos-mcp.json")
    if not sidecar.is_file():
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fingerprint_image_bytes(
    payload: bytes,
    *,
    role: str,
    safe_id: str,
    filename: str,
    mime_type: str = "",
    provider_metadata: dict[str, Any] | None = None,
    metadata_source: str = "memory_stream",
    private_path: Path | None = None,
) -> AssetFingerprint:
    """Fingerprint an in-memory image without persisting its original bytes."""

    metadata = provider_metadata or {}
    width = _safe_int(metadata.get("width"))
    height = _safe_int(metadata.get("height"))
    capture_time = _parse_datetime(metadata.get("create_time"))
    camera_make = str(metadata.get("camera_make") or "").strip()
    camera_model = str(metadata.get("camera_model") or "").strip()
    gps_present = False
    pixel_digest = ""
    phash = ""
    phash_bits = PHASH_SIZE * PHASH_SIZE
    file_digest = hashlib.sha256(payload).hexdigest() if payload else ""
    is_jpeg = bool(
        mime_type.casefold() == "image/jpeg"
        or Path(filename).suffix.casefold() in {".jpg", ".jpeg"}
        or payload[:2] == b"\xff\xd8"
    )
    jpeg_digest = _jpeg_without_metadata_sha256(payload, is_jpeg=is_jpeg)
    error = "" if payload else "empty_payload"
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS
    try:
        if error:
            raise OSError("image bytes are unavailable")
        with Image.open(BytesIO(payload)) as opened:
            exif = opened.getexif()
            raw_width, raw_height = opened.size
            width = width or int(raw_width)
            height = height or int(raw_height)
            mime_type = mime_type or str(Image.MIME.get(opened.format or "") or "")
            try:
                exif_details = exif.get_ifd(34665)
            except (AttributeError, KeyError, TypeError, ValueError):
                exif_details = {}
            capture_time = capture_time or _parse_datetime(
                exif_details.get(36867) or exif.get(36867) or exif.get(306),
                exif_details.get(36881)
                or exif_details.get(36880)
                or exif.get(36881)
                or exif.get(36880),
            )
            camera_make = camera_make or str(exif.get(271) or "").strip()
            camera_model = camera_model or str(exif.get(272) or "").strip()
            try:
                gps = exif.get_ifd(34853)
            except (AttributeError, KeyError, TypeError, ValueError):
                gps = {}
            gps_present = bool(gps and gps.get(2) and gps.get(4))

            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            normalized.thumbnail(
                (NORMALIZED_LONG_EDGE, NORMALIZED_LONG_EDGE),
                Image.Resampling.LANCZOS,
            )
            pixel_hasher = hashlib.sha256()
            pixel_hasher.update(f"{normalized.width}x{normalized.height}:RGB\0".encode("ascii"))
            pixel_hasher.update(normalized.tobytes())
            pixel_digest = pixel_hasher.hexdigest()
            perceptual = imagehash.phash(normalized, hash_size=PHASH_SIZE)
            phash = str(perceptual)
            phash_bits = len(perceptual.hash.flatten())
    except Exception as exc:  # A corrupt item must be reported, never abort the whole sample.
        error = error or type(exc).__name__
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
    return AssetFingerprint(
        role=role,
        path=private_path or Path(f".private/{role}/{safe_id}"),
        safe_id=safe_id,
        filename=filename,
        mime_type=mime_type,
        file_sha256=file_digest,
        jpeg_codestream_sha256=jpeg_digest,
        normalized_pixel_sha256=pixel_digest,
        perceptual_hash=phash,
        perceptual_hash_bits=phash_bits,
        width=width,
        height=height,
        capture_time=capture_time,
        camera_make=camera_make,
        camera_model=camera_model,
        gps_present=gps_present,
        metadata_source=metadata_source,
        error=error,
    )


def fingerprint_image(path: str | Path, *, role: str, root: str | Path) -> AssetFingerprint:
    image_path = Path(path).expanduser().resolve()
    base = Path(root).expanduser().resolve()
    safe_id = _safe_asset_id(role, base, image_path)
    sidecar = _picker_sidecar(image_path) if role == "picker" else {}
    sidecar_file = sidecar.get("file") if isinstance(sidecar.get("file"), dict) else {}
    picker_metadata = (
        sidecar.get("picker_metadata")
        if isinstance(sidecar.get("picker_metadata"), dict)
        else {}
    )
    filename = str(sidecar_file.get("filename") or image_path.name)
    mime_type = str(sidecar_file.get("mime_type") or "")
    try:
        payload = image_path.read_bytes()
    except OSError:
        payload = b""
    return fingerprint_image_bytes(
        payload,
        role=role,
        safe_id=safe_id,
        filename=filename,
        mime_type=mime_type,
        provider_metadata=picker_metadata,
        metadata_source="picker_sidecar" if sidecar else "embedded_or_file",
        private_path=image_path,
    )


def _normalized_name(value: str) -> str:
    return Path(value).name.casefold().strip()


def compare_fingerprints(android: AssetFingerprint, picker: AssetFingerprint) -> MatchEvidence:
    distance: float | None = None
    if android.perceptual_hash and picker.perceptual_hash:
        bits = max(1, android.perceptual_hash_bits, picker.perceptual_hash_bits)
        distance = float(
            (int(android.perceptual_hash, 16) ^ int(picker.perceptual_hash, 16)).bit_count()
        ) / float(bits)
    delta: float | None = None
    if android.capture_time is not None and picker.capture_time is not None:
        delta = abs((android.capture_time - picker.capture_time).total_seconds())
    dimensions_equal = bool(
        android.width
        and android.height
        and (android.width, android.height) == (picker.width, picker.height)
    )
    dimensions_compatible = bool(
        dimensions_equal
        or (
            android.width
            and android.height
            and (android.width, android.height) == (picker.height, picker.width)
        )
    )
    return MatchEvidence(
        file_sha256=bool(
            android.file_sha256
            and android.file_sha256 == picker.file_sha256
        ),
        jpeg_codestream_sha256=bool(
            android.jpeg_codestream_sha256
            and android.jpeg_codestream_sha256 == picker.jpeg_codestream_sha256
        ),
        normalized_pixel_sha256=bool(
            android.normalized_pixel_sha256
            and android.normalized_pixel_sha256 == picker.normalized_pixel_sha256
        ),
        perceptual_distance=distance,
        dimensions_equal=dimensions_equal,
        dimensions_compatible=dimensions_compatible,
        capture_delta_seconds=delta,
        filename_equal=bool(
            android.filename
            and picker.filename
            and _normalized_name(android.filename) == _normalized_name(picker.filename)
        ),
        camera_equal=bool(
            android.camera_model
            and picker.camera_model
            and android.camera_model.casefold() == picker.camera_model.casefold()
            and (
                not android.camera_make
                or not picker.camera_make
                or android.camera_make.casefold() == picker.camera_make.casefold()
            )
        ),
    )


def _shadow_candidate(evidence: MatchEvidence) -> bool:
    distance = evidence.perceptual_distance
    if distance is None or distance > 0.08:
        return False
    close_time = evidence.capture_delta_seconds is not None and evidence.capture_delta_seconds <= 5
    compatible_time = (
        evidence.capture_delta_seconds is not None
        and evidence.capture_delta_seconds <= 120
    )
    return bool(
        evidence.dimensions_compatible
        and (
            (close_time and (evidence.filename_equal or evidence.camera_equal))
            or (
                distance <= 0.03
                and compatible_time
                and evidence.filename_equal
            )
        )
    )


def match_fingerprints(
    android_assets: Iterable[AssetFingerprint],
    picker_assets: Iterable[AssetFingerprint],
) -> tuple[MatchDecision, ...]:
    android = tuple(android_assets)
    picker = tuple(picker_assets)
    decisions: list[MatchDecision] = []
    claimed_picker_ids: set[str] = set()

    def index_assets(assets: tuple[AssetFingerprint, ...], field: str):
        index: dict[str, list[AssetFingerprint]] = {}
        for asset in assets:
            if asset.error:
                continue
            value = str(getattr(asset, field) or "")
            if value:
                index.setdefault(value, []).append(asset)
        return index

    strong_fields = (
        "file_sha256",
        "jpeg_codestream_sha256",
        "normalized_pixel_sha256",
    )
    picker_indexes = {field: index_assets(picker, field) for field in strong_fields}
    android_indexes = {field: index_assets(android, field) for field in strong_fields}
    picker_by_dimensions: dict[tuple[int, int], list[AssetFingerprint]] = {}
    for asset in picker:
        if not asset.error and asset.width and asset.height:
            keys = {(asset.width, asset.height), (asset.height, asset.width)}
            for key in keys:
                picker_by_dimensions.setdefault(key, []).append(asset)

    def indexed_candidates(
        asset: AssetFingerprint,
        indexes: dict[str, dict[str, list[AssetFingerprint]]],
    ) -> list[AssetFingerprint]:
        candidates: dict[str, AssetFingerprint] = {}
        for field in strong_fields:
            value = str(getattr(asset, field) or "")
            if not value:
                continue
            for candidate in indexes[field].get(value, ()):
                candidates[candidate.safe_id] = candidate
        return list(candidates.values())

    for left in android:
        if left.error:
            decisions.append(
                MatchDecision(left.safe_id, "", "D", "unreadable", left.error, None)
            )
            continue
        strong = [
            (right, compare_fingerprints(left, right))
            for right in indexed_candidates(left, picker_indexes)
        ]
        if len(strong) == 1:
            right, evidence = strong[0]
            reverse_count = len(indexed_candidates(right, android_indexes))
            if reverse_count == 1 and right.safe_id not in claimed_picker_ids:
                claimed_picker_ids.add(right.safe_id)
                decisions.append(
                    MatchDecision(
                        left.safe_id,
                        right.safe_id,
                        "A",
                        "auto_link_eligible",
                        "unique_strong_identity",
                        evidence,
                    )
                )
                continue
        if strong:
            decisions.append(
                MatchDecision(
                    left.safe_id,
                    "",
                    "C",
                    "needs_review",
                    "duplicate_or_ambiguous_strong_identity",
                    None,
                )
            )
            continue

        shadow: list[tuple[AssetFingerprint, MatchEvidence]] = []
        for right in picker_by_dimensions.get((left.width, left.height), ()):
            if right.safe_id in claimed_picker_ids:
                continue
            evidence = compare_fingerprints(left, right)
            if _shadow_candidate(evidence):
                shadow.append((right, evidence))
        shadow.sort(
            key=lambda item: (
                item[1].perceptual_distance
                if item[1].perceptual_distance is not None
                else 1.0,
                item[1].capture_delta_seconds
                if item[1].capture_delta_seconds is not None
                else float("inf"),
                item[0].safe_id,
            )
        )
        if len(shadow) == 1:
            right, evidence = shadow[0]
            decisions.append(
                MatchDecision(
                    left.safe_id,
                    right.safe_id,
                    "B",
                    "shadow_only",
                    "unique_multi_evidence_candidate",
                    evidence,
                )
            )
        elif shadow:
            decisions.append(
                MatchDecision(
                    left.safe_id,
                    "",
                    "C",
                    "needs_review",
                    "multiple_visual_candidates",
                    None,
                )
            )
        else:
            decisions.append(
                MatchDecision(left.safe_id, "", "D", "unmatched", "no_safe_candidate", None)
            )
    return tuple(decisions)


def _public_evidence(evidence: MatchEvidence | None) -> dict[str, Any]:
    if evidence is None:
        return {}
    return {
        "file_sha256": evidence.file_sha256,
        "jpeg_codestream_sha256": evidence.jpeg_codestream_sha256,
        "normalized_pixel_sha256": evidence.normalized_pixel_sha256,
        "perceptual_distance": (
            round(evidence.perceptual_distance, 6)
            if evidence.perceptual_distance is not None
            else None
        ),
        "dimensions_equal": evidence.dimensions_equal,
        "dimensions_compatible": evidence.dimensions_compatible,
        "capture_delta_seconds": (
            round(evidence.capture_delta_seconds, 3)
            if evidence.capture_delta_seconds is not None
            else None
        ),
        "filename_equal": evidence.filename_equal,
        "camera_equal": evidence.camera_equal,
    }


def summarize_matching(
    android_assets: Iterable[AssetFingerprint],
    picker_assets: Iterable[AssetFingerprint],
    decisions: Iterable[MatchDecision],
    *,
    ground_truth: dict[str, str] | None = None,
) -> dict[str, Any]:
    android = tuple(android_assets)
    picker = tuple(picker_assets)
    evaluated = tuple(decisions)
    grades = {grade: sum(item.grade == grade for item in evaluated) for grade in "ABCD"}
    auto = tuple(item for item in evaluated if item.grade == "A")
    truth = ground_truth or {}
    labelled = 0
    false_positive_count = 0
    true_positive_count = 0
    readable_android = sum(not item.error for item in android)
    if ground_truth is not None:
        for item in auto:
            if item.android_safe_id not in truth:
                continue
            labelled += 1
            if truth[item.android_safe_id] == item.picker_safe_id:
                true_positive_count += 1
            else:
                false_positive_count += 1
    truth_coverage_complete = bool(
        ground_truth is not None
        and len(truth) == readable_android
        and {item.safe_id for item in android if not item.error} == set(truth)
    )
    ordinary_match_rate = len(auto) / readable_android if readable_android else 0.0
    gate_status = "needs_ground_truth"
    if ground_truth is not None:
        gate_status = (
            "passed"
            if ordinary_match_rate >= 0.95
            and false_positive_count == 0
            and len(truth) >= 20
            and truth_coverage_complete
            else "failed"
        )
    return {
        "schema_version": 1,
        "privacy": {
            "contains_paths": False,
            "contains_filenames": False,
            "contains_coordinates": False,
            "safe_ids_are_path_derived_hashes": True,
        },
        "sample": {
            "android_count": len(android),
            "android_readable_count": readable_android,
            "android_with_gps_count": sum(item.gps_present for item in android),
            "picker_count": len(picker),
            "picker_readable_count": sum(not item.error for item in picker),
        },
        "matches": {
            "grade_counts": grades,
            "auto_link_eligible_count": len(auto),
            "auto_link_rate": round(ordinary_match_rate, 4),
            "picker_unlinked_count": max(
                0,
                len(picker)
                - len(
                    {
                        item.picker_safe_id
                        for item in evaluated
                        if item.grade == "A" and item.picker_safe_id
                    }
                ),
            ),
        },
        "ground_truth": {
            "provided": ground_truth is not None,
            "pair_count": len(truth),
            "coverage_complete": truth_coverage_complete,
            "auto_matches_evaluated": labelled,
            "true_positive_count": true_positive_count,
            "false_positive_count": false_positive_count,
        },
        "quality_gate": {
            "status": gate_status,
            "minimum_pair_count": 20,
            "minimum_auto_link_rate": 0.95,
            "maximum_false_positive_count": 0,
        },
        "decisions": [
            {
                "android_safe_id": item.android_safe_id,
                "picker_safe_id": item.picker_safe_id,
                "grade": item.grade,
                "state": item.state,
                "reason": item.reason,
                "evidence": _public_evidence(item.evidence),
            }
            for item in evaluated
        ],
    }
