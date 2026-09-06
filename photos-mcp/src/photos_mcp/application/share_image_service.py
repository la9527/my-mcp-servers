"""Generate metadata-free web derivatives for private and shared galleries."""

from __future__ import annotations

import hashlib
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
        destination = self.cache_root / share_id / public_asset_id / f"{kind}-{_POLICY_VERSION}.jpg"
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        asset = self.repository.get_local_recommendation_asset_by_id(local_asset_id)
        if asset is None:
            raise ShareImageError("Recommendation asset is unavailable")
        source = self._resolve_source(str(asset.get("relative_path") or ""))
        fingerprint = str(asset.get("content_hash") or "")
        if not fingerprint:
            fingerprint = self._sha256(source)
        self._render(source, destination, kind=kind)
        return destination

    def purge_share(self, share_id: str) -> int:
        if not _SAFE_ID.fullmatch(share_id):
            return 0
        directory = self.cache_root / share_id
        if not directory.exists():
            return 0
        files = [path for path in directory.rglob("*") if path.is_file()]
        for path in files:
            path.unlink(missing_ok=True)
        for path in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True):
            path.rmdir()
        directory.rmdir()
        return len(files)

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
