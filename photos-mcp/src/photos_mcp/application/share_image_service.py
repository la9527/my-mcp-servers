"""Generate metadata-free web derivatives for private and shared galleries."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any, Literal

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - JPEG/PNG installs remain usable
    pass

from photos_mcp.application.recommendation_storage import recommendation_root
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.runtime.paths import photos_mcp_cache_root


DerivativeKind = Literal["thumb", "preview", "download"]
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_POLICY_VERSION = "share-jpeg-v1"


class ShareImageError(RuntimeError):
    pass


class ShareImageService:
    def __init__(
        self,
        repository: RunRepository,
        *,
        source_root: str | Path | None = None,
        cache_root: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.source_root = Path(source_root) if source_root is not None else recommendation_root()
        default_cache = photos_mcp_cache_root() / "shared-story-assets"
        self.cache_root = Path(cache_root) if cache_root is not None else default_cache

    def derivative(
        self,
        *,
        share_id: str,
        public_asset_id: str,
        local_asset_id: str,
        kind: DerivativeKind,
    ) -> Path:
        if not _SAFE_ID.fullmatch(share_id) or not _SAFE_ID.fullmatch(public_asset_id):
            raise ShareImageError("Invalid public asset identifier")
        if kind not in {"thumb", "preview", "download"}:
            raise ShareImageError("Unsupported derivative kind")
        asset = self.repository.get_local_recommendation_asset_by_id(local_asset_id)
        if asset is None:
            raise ShareImageError("Recommendation asset is unavailable")
        source = self._resolve_source(str(asset.get("relative_path") or ""))
        fingerprint = str(asset.get("content_hash") or "")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", fingerprint):
            fingerprint = self._sha256(source)
        stored_kind = "preview" if kind == "download" else kind
        derivative_id = hashlib.sha256(
            f"{fingerprint}\0{stored_kind}\0{_POLICY_VERSION}".encode("utf-8")
        ).hexdigest()[:32]
        relative_path = Path("derivatives") / fingerprint / _POLICY_VERSION / f"{stored_kind}.jpg"
        destination = self.cache_root / relative_path
        if not destination.is_file() or destination.stat().st_size <= 0:
            legacy = self.cache_root / share_id / public_asset_id / f"{stored_kind}-{_POLICY_VERSION}.jpg"
            reused = False
            if legacy.is_file() and legacy.stat().st_size > 0:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.parent.chmod(0o700)
                try:
                    os.link(legacy, destination)
                    destination.chmod(0o600)
                    reused = True
                except OSError:
                    destination.unlink(missing_ok=True)
            if not reused:
                self._render(source, destination, kind=stored_kind)
        stored = self.repository.upsert_derivative_asset(
            {
                "derivative_id": derivative_id,
                "source_content_hash": fingerprint,
                "derivative_kind": stored_kind,
                "policy_version": _POLICY_VERSION,
                "relative_path": relative_path.as_posix(),
                "byte_size": destination.stat().st_size,
            }
        )
        self.repository.add_derivative_reference(
            reference_scope="story_surface",
            reference_id=share_id,
            local_asset_id=local_asset_id,
            derivative_id=str(stored.get("derivative_id") or derivative_id),
        )
        return destination

    def purge_share(self, share_id: str) -> int:
        if not _SAFE_ID.fullmatch(share_id):
            return 0
        removed = 0
        for asset in self.repository.remove_derivative_references(
            reference_scope="story_surface",
            reference_id=share_id,
        ):
            relative_path = Path(str(asset.get("relative_path") or ""))
            candidate = (self.cache_root / relative_path).resolve()
            root = self.cache_root.resolve()
            if candidate != root and root in candidate.parents and candidate.is_file():
                candidate.unlink(missing_ok=True)
                removed += 1
                for parent in (candidate.parent, candidate.parent.parent):
                    try:
                        parent.rmdir()
                    except OSError:
                        break

        # Non-destructively clean legacy per-share derivatives created before the ledger.
        directory = self.cache_root / share_id
        if directory.exists():
            files = [path for path in directory.rglob("*") if path.is_file()]
            for path in files:
                path.unlink(missing_ok=True)
            removed += len(files)
            for path in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True):
                path.rmdir()
            directory.rmdir()
        return removed

    def _resolve_source(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise ShareImageError("Invalid recommendation asset path")
        root = self.source_root.expanduser().resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ShareImageError("Recommendation asset is outside the managed root")
        return candidate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _render(self, source: Path, destination: Path, *, kind: DerivativeKind) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.chmod(0o700)
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                if image.mode not in {"RGB", "L"}:
                    background = Image.new("RGB", image.size, "white")
                    if "A" in image.getbands():
                        background.paste(image, mask=image.getchannel("A"))
                    else:
                        background.paste(image.convert("RGB"))
                    image = background
                else:
                    image = image.convert("RGB")
                if kind == "thumb":
                    image = ImageOps.fit(
                        image,
                        (640, 640),
                        method=Image.Resampling.LANCZOS,
                    )
                else:
                    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                temporary = destination.with_suffix(".tmp")
                image.save(
                    temporary,
                    format="JPEG",
                    quality=88,
                    optimize=True,
                    progressive=True,
                    exif=b"",
                )
                temporary.chmod(0o600)
                temporary.replace(destination)
                destination.chmod(0o600)
        except (OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            raise ShareImageError("Unable to build safe image derivative") from exc
