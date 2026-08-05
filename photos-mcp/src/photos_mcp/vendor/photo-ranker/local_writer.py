"""Local filesystem write-back for classified photos."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_SAFE_COMPONENT_RE = re.compile(r"[^\w.-]+", re.UNICODE)
_SAFE_RECEIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_GENERIC_SCENE_TERMS = (
    "해변",
    "바다",
    "공원",
    "꽃",
    "산",
    "숲",
    "실내",
    "야외",
    "음식",
    "생일",
    "결혼",
    "여행",
    "가족",
    "일몰",
    "야경",
    "beach",
    "sea",
    "park",
    "flower",
    "mountain",
    "forest",
    "indoor",
    "outdoor",
    "food",
    "birthday",
    "wedding",
    "travel",
    "family",
    "sunset",
    "night",
)
_SAFE_EVENT_TYPES = {
    "birthday",
    "celebration",
    "daily",
    "event",
    "family",
    "food",
    "graduation",
    "landscape",
    "meal",
    "other",
    "outdoor",
    "pet",
    "portrait",
    "sports",
    "travel",
    "wedding",
}

_NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "pm": "https://photos-mcp.local/ns/1.0/",
}
for _prefix, _uri in _NS.items():
    ET.register_namespace(_prefix, _uri)


class LocalDirectoryWriter:
    """Copy or hard-link classified local photos into grouped directories."""

    def organize_by_classification(
        self,
        results: list[dict],
        output_dir: str,
        min_score: float = 0.0,
        group_by_date: bool = False,
        mode: str = "copy",
    ) -> dict:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)

        copied = 0
        skipped = 0
        failed: list[str] = []
        created_dirs: set[str] = set()

        for result in results:
            if result.get("total_score", 0.0) < min_score:
                skipped += 1
                continue

            source = Path(result.get("photo_id", ""))
            if not source.is_file():
                failed.append(str(source))
                continue

            target_dir = self._target_dir(root, result, group_by_date)
            target_dir.mkdir(parents=True, exist_ok=True)
            created_dirs.add(str(target_dir))

            try:
                self._write_file(source, target_dir / source.name, mode)
                copied += 1
            except Exception:
                failed.append(str(source))

        return {
            "output_dir": str(root),
            "created_dirs": sorted(created_dirs),
            "copied": copied,
            "failed": failed,
            "skipped": skipped,
            "mode": mode,
        }

    def export_selected_originals(
        self,
        results: list[dict],
        output_dir: str,
        *,
        min_score: float = 0.0,
        mode: str = "copy",
        receipt_id: str = "",
        exported_at: datetime | str | None = None,
        exiftool_executable: str | None = None,
    ) -> dict[str, Any]:
        """Copy selected originals with deterministic names and XMP metadata.

        This is intentionally separate from :meth:`organize_by_classification`.
        The legacy writer can still hard-link files, while metadata export must
        always operate on a copy so an original inode can never be modified.
        """
        if mode != "copy":
            raise ValueError("Metadata export supports copy mode only.")

        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        export_time = self._normalize_export_time(exported_at)
        safe_receipt = self._receipt_id(receipt_id, results)
        summary = {
            "selected": 0,
            "exported": 0,
            "existing": 0,
            "duplicates": 0,
            "skipped": 0,
            "failed": 0,
            "conflicts": 0,
            "sidecars_written": 0,
            "metadata_embedded": 0,
            "metadata_embedding_failed": 0,
        }
        failure_counts: dict[str, int] = {}
        manifest_items: list[dict[str, Any]] = []
        destination_paths: list[str] = []
        successful_item_indexes: list[int] = []
        seen_destinations: set[str] = set()

        for item_index, result in enumerate(results):
            if result.get("selected") is False:
                summary["skipped"] += 1
                continue
            summary["selected"] += 1
            if self._score(result.get("total_score")) < min_score:
                summary["skipped"] += 1
                continue

            source = self._source_for_result(result)
            if source is None:
                self._record_failure(summary, failure_counts, "source_unavailable")
                continue

            copied_new = False
            sidecar_existed = False
            try:
                source_digest = self._file_digest(source)
                metadata = self._metadata_for_result(
                    result,
                    source,
                    source_digest,
                    safe_receipt,
                    export_time,
                )
                relative_path = self._relative_destination(result, source, metadata)
                destination = root / relative_path
                sidecar = destination.with_name(f"{destination.name}.xmp")
                destination.parent.mkdir(parents=True, exist_ok=True)
                safe_relative = relative_path.as_posix()
                if safe_relative in seen_destinations:
                    summary["duplicates"] += 1
                    continue

                state = self._copy_idempotently(
                    source,
                    destination,
                    sidecar,
                    source_digest,
                )
                if state == "conflict":
                    summary["conflicts"] += 1
                    self._record_failure(summary, failure_counts, "destination_conflict")
                    continue
                copied_new = state == "exported"
                sidecar_existed = sidecar.exists()

                embedded = False
                embedding_failed = False
                if exiftool_executable and source.suffix.lower() in {
                    ".jpg",
                    ".jpeg",
                    ".heic",
                    ".heif",
                }:
                    embedded = self._embed_standard_xmp(
                        destination,
                        metadata,
                        exiftool_executable,
                    )
                    if not embedded:
                        embedding_failed = True

                metadata["export_digest"] = self._file_digest(destination)
                self._write_xmp_sidecar(sidecar, metadata)
                summary[state] += 1
                summary["sidecars_written"] += 1
                if embedded:
                    summary["metadata_embedded"] += 1
                if embedding_failed:
                    summary["metadata_embedding_failed"] += 1
                seen_destinations.add(safe_relative)
                destination_paths.append(safe_relative)
                successful_item_indexes.append(item_index)
                manifest_items.append(
                    {
                        "relative_path": safe_relative,
                        "sidecar_path": sidecar.relative_to(root).as_posix(),
                        "status": metadata["status_key"],
                        "event_type": metadata["event"],
                        "capture_date": metadata["capture_date"],
                        "date_source": metadata["date_source"],
                        "scores": metadata["scores"],
                        "content_digest": source_digest,
                        "copy_state": state,
                        "metadata_embedded": embedded,
                        "metadata_embedding_failed": embedding_failed,
                    }
                )
            except Exception:
                if copied_new:
                    destination.unlink(missing_ok=True)
                    if not sidecar_existed:
                        sidecar.unlink(missing_ok=True)
                self._record_failure(summary, failure_counts, "write_failed")

        manifest_items.sort(key=lambda item: item["relative_path"])
        destination_paths.sort()
        manifest = {
            "schema": "photos-mcp-original-export/v1",
            "receipt_id": safe_receipt,
            "exported_at": export_time,
            "summary": summary,
            "failure_counts": failure_counts,
            "items": manifest_items,
        }
        manifest_name = f"photos-mcp-export-{safe_receipt}.json"
        self._write_json_atomic(root / manifest_name, manifest)
        return {
            **summary,
            "failure_counts": failure_counts,
            "receipt_id": safe_receipt,
            "manifest_path": manifest_name,
            "destination_paths": destination_paths,
            "successful_item_indexes": successful_item_indexes,
            "mode": "copy",
        }

    def export_originals(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Compatibility alias for callers using the shorter export name."""
        return self.export_selected_originals(*args, **kwargs)

    @staticmethod
    def _target_dir(root: Path, result: dict, group_by_date: bool) -> Path:
        event_type = result.get("event_type") or "other"
        if group_by_date and result.get("capture_date"):
            return root / event_type / result["capture_date"][:7]
        return root / event_type

    @staticmethod
    def _write_file(source: Path, destination: Path, mode: str) -> None:
        if mode == "copy":
            shutil.copy2(source, destination)
            return
        if mode == "hardlink":
            if destination.exists():
                destination.unlink()
            destination.hardlink_to(source)
            return
        raise ValueError(f"Unsupported mode: {mode!r}. Use 'copy' or 'hardlink'.")

    @staticmethod
    def _score(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _score_token(cls, value: Any) -> str:
        return f"{max(0, min(999, int(round(cls._score(value))))):03d}"

    @staticmethod
    def _safe_component(value: Any, fallback: str, max_length: int = 40) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
        normalized = "".join(ch for ch in normalized if not unicodedata.category(ch).startswith("C"))
        normalized = _SAFE_COMPONENT_RE.sub("-", normalized).strip("-._")
        if normalized in {"", ".", ".."}:
            normalized = fallback
        return normalized[:max_length].rstrip("-._") or fallback

    @classmethod
    def _safe_event(cls, value: Any) -> str:
        event = cls._safe_component(value, "other").casefold()
        return event if event in _SAFE_EVENT_TYPES else "other"

    @classmethod
    def _short_scene(cls, value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        matches = [term for term in _GENERIC_SCENE_TERMS if term.casefold() in normalized]
        if not matches:
            return "장면"
        return cls._safe_component("-".join(dict.fromkeys(matches[:2])), "장면", 24)

    @staticmethod
    def _status(result: dict) -> tuple[str, str]:
        raw_status = str(
            result.get("selection_state")
            or result.get("status")
            or result.get("result_category")
            or ""
        ).casefold()
        recommended = bool(result.get("recommended_in_cluster")) or raw_status in {
            "recommended",
            "recommend",
            "추천",
        }
        if recommended:
            return "추천", "recommended"
        return "검토-필요", "review-needed"

    @classmethod
    def _capture_datetime(cls, result: dict, source: Path) -> tuple[datetime, str]:
        raw_value = result.get("capture_date") or result.get("captured_at")
        if raw_value:
            text = str(raw_value).strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
                return parsed, "capture_date"
            except ValueError:
                pass
        return datetime.fromtimestamp(source.stat().st_mtime, tz=UTC), "file_modified_time"

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _source_for_result(result: dict) -> Path | None:
        for key in ("source_photo_path", "photo_id"):
            value = result.get(key)
            if not value:
                continue
            candidate = Path(str(value))
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @classmethod
    def _receipt_id(cls, receipt_id: str, results: list[dict]) -> str:
        if receipt_id and _SAFE_RECEIPT_RE.fullmatch(receipt_id):
            return receipt_id
        seed = json.dumps(
            [
                {
                    "event": str(item.get("event_type") or ""),
                    "date": str(item.get("capture_date") or ""),
                    "score": cls._score(item.get("total_score")),
                }
                for item in results
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        if receipt_id:
            seed += hashlib.sha256(receipt_id.encode("utf-8")).hexdigest()
        return f"pm-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _normalize_export_time(value: datetime | str | None) -> str:
        if value is None:
            timestamp = datetime.now(UTC)
        elif isinstance(value, datetime):
            timestamp = value
        else:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @classmethod
    def _metadata_for_result(
        cls,
        result: dict,
        source: Path,
        source_digest: str,
        receipt_id: str,
        export_time: str,
    ) -> dict[str, Any]:
        status_label, status_key = cls._status(result)
        event = cls._safe_event(result.get("event_type"))
        scene = cls._short_scene(result.get("scene_description"))
        captured_at, date_source = cls._capture_datetime(result, source)
        scores = {
            "total": cls._score(result.get("total_score")),
            "technical": cls._score(result.get("technical_score")),
            "meaningful": cls._score(result.get("meaningful_score")),
        }
        return {
            "status_label": status_label,
            "status_key": status_key,
            "event": event,
            "scene": scene,
            "description": scene.replace("-", " "),
            "capture_date": captured_at.isoformat(),
            "capture_stamp": captured_at.strftime("%Y%m%d-%H%M%S"),
            "capture_month": captured_at.strftime("%Y-%m"),
            "date_source": date_source,
            "scores": scores,
            "source_digest": source_digest,
            "receipt_id": receipt_id,
            "exported_at": export_time,
        }

    @classmethod
    def _relative_destination(
        cls,
        result: dict,
        source: Path,
        metadata: dict[str, Any],
    ) -> Path:
        del result
        extension = source.suffix.lower()
        if not extension or not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".bin"
        scores = metadata["scores"]
        filename = (
            f"{metadata['capture_stamp']}_{metadata['event']}_{metadata['scene']}_"
            f"{metadata['status_label']}_Q{cls._score_token(scores['total'])}_"
            f"T{cls._score_token(scores['technical'])}_"
            f"M{cls._score_token(scores['meaningful'])}_"
            f"{metadata['source_digest'][:8]}{extension}"
        )
        return (
            Path(metadata["status_label"])
            / metadata["event"]
            / metadata["capture_month"]
            / filename
        )

    @classmethod
    def _copy_idempotently(
        cls,
        source: Path,
        destination: Path,
        sidecar: Path,
        source_digest: str,
    ) -> str:
        if destination.exists():
            sidecar_source_digest, sidecar_export_digest = cls._sidecar_digests(sidecar)
            if (
                sidecar_source_digest == source_digest
                and sidecar_export_digest
                and cls._file_digest(destination) == sidecar_export_digest
            ):
                return "existing"
            if not sidecar.exists() and cls._file_digest(destination) == source_digest:
                return "existing"
            return "conflict"

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=".photos-mcp-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            shutil.copy2(source, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return "exported"

    @staticmethod
    def _record_failure(
        summary: dict[str, int],
        failure_counts: dict[str, int],
        reason: str,
    ) -> None:
        summary["failed"] += 1
        failure_counts[reason] = failure_counts.get(reason, 0) + 1

    @classmethod
    def _write_xmp_sidecar(cls, sidecar: Path, metadata: dict[str, Any]) -> None:
        xmpmeta = ET.Element(f"{{{_NS['x']}}}xmpmeta")
        rdf = ET.SubElement(xmpmeta, f"{{{_NS['rdf']}}}RDF")
        description = ET.SubElement(
            rdf,
            f"{{{_NS['rdf']}}}Description",
            {f"{{{_NS['rdf']}}}about": ""},
        )

        subject = ET.SubElement(description, f"{{{_NS['dc']}}}subject")
        bag = ET.SubElement(subject, f"{{{_NS['rdf']}}}Bag")
        for value in ("PhotosMcp", metadata["status_label"], metadata["event"]):
            ET.SubElement(bag, f"{{{_NS['rdf']}}}li").text = value

        dc_description = ET.SubElement(description, f"{{{_NS['dc']}}}description")
        alt = ET.SubElement(dc_description, f"{{{_NS['rdf']}}}Alt")
        description_value = ET.SubElement(
            alt,
            f"{{{_NS['rdf']}}}li",
            {"{http://www.w3.org/XML/1998/namespace}lang": "x-default"},
        )
        description_value.text = metadata["description"]
        ET.SubElement(description, f"{{{_NS['xmp']}}}Label").text = metadata["status_label"]

        custom_values = {
            "TotalScore": metadata["scores"]["total"],
            "TechnicalScore": metadata["scores"]["technical"],
            "MeaningfulScore": metadata["scores"]["meaningful"],
            "SelectionState": metadata["status_key"],
            "CaptureDate": metadata["capture_date"],
            "DateSource": metadata["date_source"],
            "ExportedAt": metadata["exported_at"],
            "ReceiptId": metadata["receipt_id"],
            "SourceDigest": metadata["source_digest"],
            "ExportDigest": metadata["export_digest"],
        }
        for key, value in custom_values.items():
            ET.SubElement(description, f"{{{_NS['pm']}}}{key}").text = str(value)

        tree = ET.ElementTree(xmpmeta)
        cls._write_bytes_atomic(
            sidecar,
            ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True),
        )

    @staticmethod
    def _sidecar_digests(sidecar: Path) -> tuple[str, str]:
        if not sidecar.is_file():
            return "", ""
        try:
            root = ET.parse(sidecar).getroot()
            source_node = root.find(f".//{{{_NS['pm']}}}SourceDigest")
            export_node = root.find(f".//{{{_NS['pm']}}}ExportDigest")
            source_digest = (
                source_node.text.strip()
                if source_node is not None and source_node.text
                else ""
            )
            export_digest = (
                export_node.text.strip()
                if export_node is not None and export_node.text
                else ""
            )
            return source_digest, export_digest
        except (ET.ParseError, OSError):
            return "", ""

    @staticmethod
    def _embed_standard_xmp(
        destination: Path,
        metadata: dict[str, Any],
        executable: str,
    ) -> bool:
        command = [
            executable,
            "-overwrite_original",
            "-XMP-dc:Subject=PhotosMcp",
            f"-XMP-dc:Subject+={metadata['status_label']}",
            f"-XMP-dc:Subject+={metadata['event']}",
            f"-XMP-xmp:Label={metadata['status_label']}",
            f"-XMP-dc:Description={metadata['description']}",
            str(destination),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        LocalDirectoryWriter._write_bytes_atomic(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        )

    @staticmethod
    def _write_bytes_atomic(path: Path, content: bytes) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=".photos-mcp-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
