"""Local filesystem write-back for classified photos."""

from __future__ import annotations

import shutil
from pathlib import Path


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