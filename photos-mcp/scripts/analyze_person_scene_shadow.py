#!/usr/bin/env python3
"""Measure anonymous subject groups and face-quality ranking on private reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
import urllib.request

import cv2
from Foundation import NSData
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision
import numpy as np
import Vision

from photos_mcp.application.person_scene_shadow import (
    FaceShadowMeasurement,
    PhotoShadowMeasurement,
    analyze_person_scene_shadow,
    assign_subject_signatures,
    group_by_subject_signature,
)


MODEL_SPECS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        200_000,
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx",
        36_000_000,
    ),
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task",
        3_000_000,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path.home() / ".photos-mcp" / "cache" / "models" / "person-shadow",
    )
    parser.add_argument("--audit-limit", type=int, default=16)
    return parser.parse_args()


def _ensure_models(model_root: Path) -> dict[str, Path]:
    model_root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, (url, minimum_size) in MODEL_SPECS.items():
        path = model_root / name
        if not path.is_file() or path.stat().st_size < minimum_size:
            temporary = path.with_suffix(path.suffix + ".download")
            urllib.request.urlretrieve(url, temporary)
            if temporary.stat().st_size < minimum_size:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(f"모델 다운로드 크기가 올바르지 않습니다: {name}")
            temporary.replace(path)
            path.chmod(0o600)
        paths[name] = path
    return paths


def _load_score_rows(database: Path, job_id: str) -> list[dict[str, Any]]:
    with sqlite3.connect(database.expanduser()) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM photo_results WHERE job_id = ?",
                (job_id,),
            )
        ]


def _completed_items(queue: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in queue.get("items") or []
        if (item.get("labels") or {}).get("review_status") == "completed"
        and (item.get("labels") or {}).get("best_photo_ids")
    ]


def _preview_paths(items: list[dict[str, Any]]) -> dict[str, Path]:
    return {
        str(photo.get("photo_id") or ""): Path(str(photo.get("preview_path") or ""))
        for item in items
        for photo in item.get("photos") or []
        if str(photo.get("photo_id") or "") and str(photo.get("preview_path") or "")
    }


def _iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    left_x1, left_y1, left_x2, left_y2 = left
    right_x1, right_y1, right_x2, right_y2 = right
    x1, y1 = max(left_x1, right_x1), max(left_y1, right_y1)
    x2, y2 = min(left_x2, right_x2), min(left_y2, right_y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection <= 0.0:
        return 0.0
    left_area = max(0.0, left_x2 - left_x1) * max(0.0, left_y2 - left_y1)
    right_area = max(0.0, right_x2 - right_x1) * max(0.0, right_y2 - right_y1)
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _vision_face_quality(path: Path, width: int, height: int) -> list[dict[str, Any]]:
    image_bytes = path.read_bytes()
    request = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(image_bytes, len(image_bytes)),
        {},
    )
    success, error = handler.performRequests_error_([request], None)
    if not success or error is not None:
        return []
    results = []
    for observation in list(request.results() or []):
        box = observation.boundingBox()
        x1 = float(box.origin.x) * width
        y1 = (1.0 - float(box.origin.y) - float(box.size.height)) * height
        x2 = x1 + float(box.size.width) * width
        y2 = y1 + float(box.size.height) * height
        quality = observation.faceCaptureQuality()
        results.append(
            {
                "bbox": (x1, y1, x2, y2),
                "quality": float(quality) if quality is not None else None,
            }
        )
    return results


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _blendshape_signals(result: Any) -> tuple[float | None, float | None, float | None]:
    if not result.face_blendshapes:
        return None, None, None
    scores = {
        category.category_name: float(category.score)
        for category in result.face_blendshapes[0]
    }
    blink = max(scores.get("eyeBlinkLeft", 0.0), scores.get("eyeBlinkRight", 0.0))
    eye_moves = [
        scores.get(name, 0.0)
        for name in (
            "eyeLookDownLeft",
            "eyeLookDownRight",
            "eyeLookInLeft",
            "eyeLookInRight",
            "eyeLookOutLeft",
            "eyeLookOutRight",
            "eyeLookUpLeft",
            "eyeLookUpRight",
        )
    ]
    smile = statistics_mean(
        scores.get("mouthSmileLeft", 0.0),
        scores.get("mouthSmileRight", 0.0),
    )
    return _bounded(1.0 - blink), _bounded(1.0 - max(eye_moves)), _bounded(smile)


def statistics_mean(*values: float) -> float:
    return sum(values) / len(values) if values else 0.0


def _pose_score(result: Any) -> float | None:
    if not result.facial_transformation_matrixes:
        return None
    matrix = np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64)
    if matrix.shape != (4, 4):
        return None
    rotation = matrix[:3, :3]
    pitch = math.degrees(
        math.atan2(-rotation[2, 0], math.sqrt(rotation[0, 0] ** 2 + rotation[1, 0] ** 2))
    )
    yaw = math.degrees(math.atan2(rotation[1, 0], rotation[0, 0]))
    return _bounded(1.0 - max(abs(pitch) / 35.0, abs(yaw) / 40.0))


def _landmarker_signals(
    landmarker: Any,
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> tuple[float | None, float | None, float | None, float | None]:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = bbox
    face_width, face_height = x2 - x1, y2 - y1
    padding = 0.4
    crop_x1 = max(0, int(x1 - face_width * padding))
    crop_y1 = max(0, int(y1 - face_height * padding))
    crop_x2 = min(width, int(x2 + face_width * padding))
    crop_y2 = min(height, int(y2 + face_height * padding))
    crop = image[crop_y1:crop_y2, crop_x1:crop_x2]
    if crop.size == 0:
        return None, None, None, None
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    eye_open, gaze, smile = _blendshape_signals(result)
    return eye_open, gaze, smile, _pose_score(result)


def _sharpness_score(crop: np.ndarray) -> float | None:
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return _bounded(1.0 - math.exp(-variance / 300.0))


def _measure_photo(
    photo_id: str,
    path: Path,
    detector: Any,
    recognizer: Any,
    landmarker: Any,
) -> PhotoShadowMeasurement:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return PhotoShadowMeasurement(photo_id)
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _status, detected = detector.detect(image)
    if detected is None:
        return PhotoShadowMeasurement(photo_id)
    vision_faces = _vision_face_quality(path, width, height)
    measurements: list[FaceShadowMeasurement] = []
    for face in detected:
        x, y, face_width, face_height = (float(value) for value in face[:4])
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2 = min(width, int(x + face_width))
        y2 = min(height, int(y + face_height))
        if x2 <= x1 or y2 <= y1:
            continue
        try:
            aligned = recognizer.alignCrop(image, face)
            feature = np.asarray(recognizer.feature(aligned), dtype=np.float32).reshape(-1)
        except cv2.error:
            continue
        norm = float(np.linalg.norm(feature))
        if norm <= 0.0:
            continue
        embedding = tuple(float(value) for value in feature / norm)
        bbox = (x1, y1, x2, y2)
        quality = None
        if vision_faces:
            match = max(vision_faces, key=lambda item: _iou(bbox, item["bbox"]))
            if _iou(bbox, match["bbox"]) >= 0.15:
                quality = match["quality"]
        eye_open, gaze, smile, pose = _landmarker_signals(landmarker, image, bbox)
        crop = image[y1:y2, x1:x2]
        area_ratio = (face_width * face_height) / max(1.0, float(width * height))
        measurements.append(
            FaceShadowMeasurement(
                embedding=embedding,
                capture_quality=quality,
                eye_open=eye_open,
                camera_gaze=gaze,
                smile=smile,
                sharpness=_sharpness_score(crop),
                pose=pose,
                area=_bounded(math.sqrt(max(0.0, area_ratio)) / 0.25),
                bbox=bbox,
            )
        )
    return PhotoShadowMeasurement(photo_id, tuple(measurements))


def _measurement_to_json(measurement: PhotoShadowMeasurement) -> dict[str, Any]:
    return {
        "photo_id": measurement.photo_id,
        "faces": [
            {
                "embedding": list(face.embedding),
                "capture_quality": face.capture_quality,
                "eye_open": face.eye_open,
                "camera_gaze": face.camera_gaze,
                "smile": face.smile,
                "sharpness": face.sharpness,
                "pose": face.pose,
                "area": face.area,
                "bbox": list(face.bbox) if face.bbox is not None else None,
            }
            for face in measurement.faces
        ],
    }


def _measurement_from_json(payload: dict[str, Any]) -> PhotoShadowMeasurement:
    return PhotoShadowMeasurement(
        photo_id=str(payload["photo_id"]),
        faces=tuple(
            FaceShadowMeasurement(
                embedding=tuple(float(value) for value in face.get("embedding") or []),
                capture_quality=face.get("capture_quality"),
                eye_open=face.get("eye_open"),
                camera_gaze=face.get("camera_gaze"),
                smile=face.get("smile"),
                sharpness=face.get("sharpness"),
                pose=face.get("pose"),
                area=face.get("area"),
                bbox=tuple(int(value) for value in face.get("bbox") or []) or None,
            )
            for face in payload.get("faces") or []
        ),
    )


def _model_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    return {
        name: {
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in sorted(paths.items())
    }


def _private_audit(
    items: list[dict[str, Any]],
    measurements: dict[str, PhotoShadowMeasurement],
    *,
    threshold: float,
    limit: int,
) -> dict[str, Any]:
    audit_items: list[dict[str, Any]] = []
    for item in items:
        photos = {
            str(photo.get("photo_id") or ""): photo
            for photo in item.get("photos") or []
            if str(photo.get("photo_id") or "")
        }
        signatures = assign_subject_signatures(
            [measurements.get(photo_id, PhotoShadowMeasurement(photo_id)) for photo_id in photos],
            similarity_threshold=threshold,
        )
        groups = group_by_subject_signature(signatures)
        if len(groups) < 2:
            continue
        audit_items.append(
            {
                "scene_cluster_id": str(item.get("scene_cluster_id") or ""),
                "human_primary": str((item.get("labels") or {}).get("best_photo_ids", [""])[0]),
                "groups": [
                    {
                        "anonymous_signature": list(signature),
                        "photos": [
                            {
                                "photo_id": photo_id,
                                "preview_path": str(photos[photo_id].get("preview_path") or ""),
                            }
                            for photo_id in photo_ids
                        ],
                    }
                    for signature, photo_ids in groups.items()
                ],
            }
        )
        if limit > 0 and len(audit_items) >= limit:
            break
    return {
        "private": True,
        "purpose": "person-aware scene boundary visual audit",
        "similarity_threshold": threshold,
        "items": audit_items,
    }


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    review_path = args.review.expanduser().resolve()
    queue = json.loads(review_path.read_text(encoding="utf-8"))
    items = _completed_items(queue)
    previews = _preview_paths(items)
    missing = [path for path in previews.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"미리보기 {len(missing)}개를 찾지 못했습니다.")

    private_root = args.private_root.expanduser().resolve()
    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private_root, 0o700)
    cache_path = private_root / "measurements-private.json"
    models = _ensure_models(args.model_root.expanduser().resolve())

    cached: dict[str, PhotoShadowMeasurement] = {}
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version") or 1) >= 2:
            cached = {
                measurement.photo_id: measurement
                for item in payload.get("measurements") or []
                if (measurement := _measurement_from_json(item)).photo_id
            }
    cached_count_before = len(set(cached) & set(previews))

    detector = cv2.FaceDetectorYN_create(
        str(models["face_detection_yunet_2023mar.onnx"]),
        "",
        (320, 320),
        0.55,
        0.3,
        5000,
    )
    recognizer = cv2.FaceRecognizerSF_create(
        str(models["face_recognition_sface_2021dec.onnx"]),
        "",
    )
    options = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(models["face_landmarker.task"])),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.1,
        min_face_presence_confidence=0.1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        for index, (photo_id, path) in enumerate(previews.items(), start=1):
            if photo_id in cached:
                continue
            cached[photo_id] = _measure_photo(photo_id, path, detector, recognizer, landmarker)
            if index % 25 == 0:
                print(f"계측 진행: {index}/{len(previews)}", flush=True)

    cache_payload = {
        "schema_version": 2,
        "private": True,
        "job_id": str(queue.get("job_id") or ""),
        "measurements": [_measurement_to_json(cached[photo_id]) for photo_id in sorted(cached)],
    }
    cache_path.write_text(
        json.dumps(cache_payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cache_path.chmod(0o600)

    score_rows = _load_score_rows(args.database, str(queue.get("job_id") or ""))
    summary = analyze_person_scene_shadow(queue, score_rows, cached.values())
    summary["runtime"] = {
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "opencv_version": cv2.__version__,
        "apple_face_capture_revision": int(
            Vision.VNDetectFaceCaptureQualityRequest.currentRevision()
        ),
        "model_manifest": _model_manifest(models),
        "measurement_cache_reused_count": cached_count_before,
        "measurement_new_count": len(previews) - cached_count_before,
    }
    summary["method"] = {
        "identity": "opencv-yunet-sface",
        "expression": "mediapipe-face-landmarker-blendshapes",
        "face_quality": "apple-vision-face-capture-quality",
        "identity_thresholds": [0.363, 0.45, 0.55],
        "ranking": "0.65 face + 0.25 technical + 0.10 current total within subject group",
        "face_veto": "replace only a nearby current winner with a clearly recovered candidate",
    }

    audit = _private_audit(items, cached, threshold=0.45, limit=args.audit_limit)
    audit_path = private_root / "audit-private.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_path.chmod(0o600)

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
