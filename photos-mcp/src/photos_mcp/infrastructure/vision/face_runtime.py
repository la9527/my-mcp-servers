"""Shared OpenCV face runtime contract for app and vendor adapters."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

from photos_mcp.infrastructure.runtime.paths import photos_mcp_home

MODEL_FAMILY = "opencv-yunet-sface"
MODEL_VERSION = "yunet-2023mar+sface-2021dec"
DETECTOR_MODEL = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "face_recognition_sface_2021dec.onnx"
MODEL_MINIMUM_BYTES = {
    DETECTOR_MODEL: 200_000,
    RECOGNIZER_MODEL: 36_000_000,
}
DETECTOR_MAX_LONG_EDGE = 1600
DETECTION_PREPROCESSOR_VERSION = "bounded-long-edge-1600-v1"


def bounded_detector_image(image: Any, cv2_module: Any) -> tuple[Any, float, float]:
    """Return a bounded detector proxy and its source scale factors."""

    height, width = image.shape[:2]
    scale = min(1.0, float(DETECTOR_MAX_LONG_EDGE) / float(max(width, height)))
    if scale >= 1.0:
        return image, 1.0, 1.0
    detector_width = max(1, round(width * scale))
    detector_height = max(1, round(height * scale))
    proxy = cv2_module.resize(
        image,
        (detector_width, detector_height),
        interpolation=cv2_module.INTER_AREA,
    )
    return proxy, detector_width / float(width), detector_height / float(height)


def face_to_source_coordinates(face: Any, scale_x: float, scale_y: float) -> Any:
    """Map a YuNet row (box plus five landmarks) back to source pixels."""

    import numpy as np

    if scale_x <= 0.0 or scale_y <= 0.0:
        raise ValueError("invalid detector scale")
    mapped = np.asarray(face, dtype="float32").copy()
    for index in (0, 2, 4, 6, 8, 10, 12):
        if index < mapped.size:
            mapped[index] /= scale_x
    for index in (1, 3, 5, 7, 9, 11, 13):
        if index < mapped.size:
            mapped[index] /= scale_y
    return mapped


def model_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def face_model_roots() -> tuple[Path, ...]:
    configured = os.environ.get("PHOTOS_MCP_PERSON_MODEL_ROOT", "").strip()
    if configured:
        return (Path(configured).expanduser(),)
    roots = [photos_mcp_home() / "cache" / "models" / "person-shadow"]
    executable = Path(sys.executable).resolve()
    if ".app" in str(executable):
        roots.insert(0, executable.parents[1] / "Resources" / "person-models")
    return tuple(roots)


def resolve_face_models() -> dict[str, Path]:
    """Resolve a complete, size-validated YuNet/SFace model pair."""

    for root in face_model_roots():
        paths = {name: root / name for name in MODEL_MINIMUM_BYTES}
        if all(
            path.is_file() and path.stat().st_size >= MODEL_MINIMUM_BYTES[name]
            for name, path in paths.items()
        ):
            return paths
    return {}
