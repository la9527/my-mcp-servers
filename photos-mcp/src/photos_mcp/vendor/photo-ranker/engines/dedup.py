"""Perceptual hash based duplicate detection engine."""

from __future__ import annotations

import base64
import io
import logging
import uuid

from models import DuplicateGroup

logger = logging.getLogger(__name__)


class DedupEngine:
    """Finds duplicate/similar photos using perceptual hashing."""

    def __init__(self, threshold: int = 8) -> None:
        self._threshold = threshold

    def compute_hash(self, image_b64: str) -> str:
        """Compute average perceptual hash for an image."""
        import imagehash
        from PIL import Image

        img_bytes = base64.b64decode(image_b64)
        image = Image.open(io.BytesIO(img_bytes))
        h = imagehash.average_hash(image)
        return str(h)

    def compute_phash(self, image_b64: str) -> str:
        """Compute perceptual hash (DCT-based). More resistant to false positives."""
        import imagehash
        from PIL import Image

        img_bytes = base64.b64decode(image_b64)
        image = Image.open(io.BytesIO(img_bytes))
        h = imagehash.phash(image)
        return str(h)

    def compute_default_hash(self, image_b64: str) -> str:
        """Compute the default hash used for dedup (phash).

        phash was chosen over ahash based on benchmark results:
        - phash_t8: 100% recall, 0 false positive pairs
        - ahash_t8: 100% recall, 54 false positive pairs
        """
        return self.compute_phash(image_b64)

    def hash_distance(self, hash1: str, hash2: str) -> int:
        """Compute Hamming distance between two hex hash strings."""
        import imagehash

        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        return h1 - h2

    def find_duplicates(
        self,
        photo_hashes: dict[str, str],
        threshold: int | None = None,
    ) -> list[DuplicateGroup]:
        """Group photos by perceptual similarity.

        Args:
            photo_hashes: Mapping of photo_id -> hash string.
            threshold: Max Hamming distance to consider a duplicate.

        Returns:
            List of DuplicateGroup, each containing similar photo IDs.
        """
        import imagehash

        thr = threshold if threshold is not None else self._threshold
        ids = list(photo_hashes.keys())
        hashes = {pid: imagehash.hex_to_hash(h) for pid, h in photo_hashes.items()}

        visited: set[str] = set()
        groups: list[DuplicateGroup] = []

        for i, pid_a in enumerate(ids):
            if pid_a in visited:
                continue
            group_ids = [pid_a]
            visited.add(pid_a)

            for pid_b in ids[i + 1 :]:
                if pid_b in visited:
                    continue
                dist = hashes[pid_a] - hashes[pid_b]
                if dist <= thr:
                    group_ids.append(pid_b)
                    visited.add(pid_b)

            if len(group_ids) > 1:
                groups.append(
                    DuplicateGroup(
                        group_id=uuid.uuid4().hex[:8],
                        photo_ids=group_ids,
                        representative_id=group_ids[0],
                    )
                )

        return groups
