#!/usr/bin/env python3
"""Run cached two-image VLM comparisons for same-subject review pairs."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

import httpx
from PIL import Image, ImageOps

from photos_mcp.application.person_pairwise_shadow import (
    PairwiseShadowCase,
    PairwiseShadowDecision,
    consensus_pairwise_decision,
    evaluate_pairwise_shadow,
    mirror_decision_to_original,
    parse_pairwise_decision,
)
import photos_mcp.application.person_scene_shadow as person_shadow
from photos_mcp.infrastructure.vision.broker_client import default_runtime_broker_client
from photos_mcp.infrastructure.vision.runtime import resolve_vision_runtime_settings


PAIRWISE_PROMPT_VERSION = "person-pairwise-shadow-v3-face-crop-board"
PAIRWISE_PROMPT = """\
사진 A와 사진 B는 같은 장면에서 주요 인물이 같은 대표 사진 후보입니다.
가족 앨범에 한 장만 남긴다는 기준으로 더 좋은 사진을 고르세요.

각 후보 이미지는 위쪽의 전체 프레임과 아래쪽의 주요 얼굴 확대를 한 장으로 합친 보드입니다. 전체 사진의 구도와 순간을 우선 확인하고, 얼굴 확대는 눈 감김·가림·초점처럼 작은 차이를 확인하는 데만 사용하세요.

판단 순서:
1. 주요 인물 모두의 눈 감김, 얼굴 가림, 흔들림과 초점 실패
2. 주요 인물의 자연스러운 표정과 촬영 순간. 미소 하나만으로 선택하지 말 것
3. 인물 배치, 잘림, 배경 정돈과 전체 구도
4. 두 사진의 차이가 의미 없으면 tie

나이, 관계, 이름과 민감한 속성을 추론하지 마세요.
반드시 JSON 하나만 출력하세요:
{"winner":"A 또는 B 또는 tie","confidence":0.0,"same_primary_subjects":true,"reasons":["짧은 근거"],"defects":{"A":[],"B":[]}}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=32)
    return parser.parse_args()


def _load_measurements(path: Path) -> dict[str, person_shadow.PhotoShadowMeasurement]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    measurements: dict[str, person_shadow.PhotoShadowMeasurement] = {}
    for item in payload.get("measurements") or []:
        faces = tuple(
            person_shadow.FaceShadowMeasurement(
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
            for face in item.get("faces") or []
        )
        photo_id = str(item.get("photo_id") or "")
        if photo_id:
            measurements[photo_id] = person_shadow.PhotoShadowMeasurement(photo_id, faces)
    return measurements


def _load_rows(database: Path, job_id: str) -> dict[str, dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return {
            str(row["photo_id"]): dict(row)
            for row in connection.execute(
                "SELECT * FROM photo_results WHERE job_id = ?",
                (job_id,),
            )
        }


def _case_hash(scene_id: str, human_id: str, competitor_id: str) -> str:
    return hashlib.sha256(f"{scene_id}|{human_id}|{competitor_id}".encode()).hexdigest()


def _prepare_cases(
    queue: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    measurements: dict[str, person_shadow.PhotoShadowMeasurement],
    *,
    max_cases: int,
) -> list[dict[str, Any]]:
    correct: list[dict[str, Any]] = []
    incorrect: list[dict[str, Any]] = []
    for item in queue.get("items") or []:
        labels = item.get("labels") or {}
        best_ids = labels.get("best_photo_ids") or []
        if labels.get("review_status") != "completed" or not best_ids:
            continue
        photos = {
            str(photo.get("photo_id") or ""): photo
            for photo in item.get("photos") or []
            if str(photo.get("photo_id") or "") in rows
        }
        human_id = str(best_ids[0])
        if human_id not in photos:
            continue
        scene_measurements = [
            measurements.get(photo_id, person_shadow.PhotoShadowMeasurement(photo_id))
            for photo_id in photos
        ]
        signatures = person_shadow.assign_subject_signatures(
            scene_measurements,
            similarity_threshold=0.45,
        )
        groups = person_shadow.group_by_subject_signature(signatures)
        human_group = list(groups.get(signatures.get(human_id), ()))
        current = person_shadow._current_order(human_group, rows)
        if len(current) < 2:
            continue
        competitor_id = current[0] if current[0] != human_id else next(
            photo_id for photo_id in current if photo_id != human_id
        )
        scene_id = str(item.get("scene_cluster_id") or "")
        case_id = _case_hash(scene_id, human_id, competitor_id)[:20]
        human_on_a = int(case_id[-1], 16) % 2 == 0
        side_a = human_id if human_on_a else competitor_id
        side_b = competitor_id if human_on_a else human_id
        case = {
            "case_id": case_id,
            "human_side": "A" if human_on_a else "B",
            "current_side": "A" if current[0] == side_a else "B",
            "side_a_photo_id": side_a,
            "side_b_photo_id": side_b,
            "side_a_path": str(photos[side_a].get("preview_path") or ""),
            "side_b_path": str(photos[side_b].get("preview_path") or ""),
        }
        (correct if current[0] == human_id else incorrect).append(case)

    correct.sort(key=lambda case: case["case_id"])
    incorrect.sort(key=lambda case: case["case_id"])
    target_incorrect = min(len(incorrect), max_cases // 2)
    selected = incorrect[:target_incorrect]
    selected.extend(correct[: max_cases - len(selected)])
    if len(selected) < max_cases:
        selected.extend(incorrect[target_incorrect : max_cases - len(selected) + target_incorrect])
    return sorted(selected[:max_cases], key=lambda case: case["case_id"])


def _image_content(label: str, image_bytes: bytes) -> list[dict[str, Any]]:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return [
        {"type": "text", "text": label},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
    ]


def _candidate_board(
    path: Path,
    measurement: person_shadow.PhotoShadowMeasurement | None,
) -> bytes:
    image = Image.open(path).convert("RGB")
    full = ImageOps.contain(image, (768, 576), Image.Resampling.LANCZOS)
    board_width = 768
    face_tile = 192
    faces = [
        face
        for face in person_shadow._primary_faces(measurement)
        if face.bbox is not None and len(face.bbox) == 4
    ] if measurement is not None else []

    crops: list[Image.Image] = []
    for face in sorted(faces, key=lambda item: float(item.area or 0.0), reverse=True)[:4]:
        left, top, right, bottom = face.bbox or (0, 0, 0, 0)
        width, height = max(1, right - left), max(1, bottom - top)
        padding = 0.45
        crop = image.crop(
            (
                max(0, int(left - width * padding)),
                max(0, int(top - height * padding)),
                min(image.width, int(right + width * padding)),
                min(image.height, int(bottom + height * padding)),
            )
        )
        if crop.width > 1 and crop.height > 1:
            crops.append(ImageOps.contain(crop, (face_tile, face_tile), Image.Resampling.LANCZOS))

    board_height = full.height if not crops else full.height + 12 + face_tile
    board = Image.new("RGB", (board_width, board_height), color=(20, 20, 20))
    board.paste(full, ((board_width - full.width) // 2, 0))
    for index, crop in enumerate(crops):
        x = index * face_tile + (face_tile - crop.width) // 2
        y = full.height + 12 + (face_tile - crop.height) // 2
        board.paste(crop, (x, y))
    from io import BytesIO

    buffer = BytesIO()
    board.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def _payload(
    model: str,
    path_a: Path,
    path_b: Path,
    measurement_a: person_shadow.PhotoShadowMeasurement | None,
    measurement_b: person_shadow.PhotoShadowMeasurement | None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": PAIRWISE_PROMPT}]
    content.extend(_image_content("사진 A", _candidate_board(path_a, measurement_a)))
    content.extend(_image_content("사진 B", _candidate_board(path_b, measurement_b)))
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return exactly one JSON object."},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
    }


def _response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("Pairwise VLM response is missing choices")
    content = (choices[0].get("message") or {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


def _save_private(path: Path, job_id: str, cases: list[dict[str, Any]]) -> None:
    payload = {
        "private": True,
        "job_id": job_id,
        "prompt_version": PAIRWISE_PROMPT_VERSION,
        "cases": cases,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    queue = json.loads(args.review.expanduser().read_text(encoding="utf-8"))
    job_id = str(queue.get("job_id") or "")
    rows = _load_rows(args.database.expanduser(), job_id)
    measurements = _load_measurements(args.measurements.expanduser())
    private_root = args.private_root.expanduser().resolve()
    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private_root, 0o700)
    private_path = private_root / "pairwise-private.json"
    cases = _prepare_cases(queue, rows, measurements, max_cases=max(1, args.max_cases))

    cached: dict[str, dict[str, Any]] = {}
    if private_path.is_file():
        existing = json.loads(private_path.read_text(encoding="utf-8"))
        if existing.get("prompt_version") == PAIRWISE_PROMPT_VERSION:
            cached = {
                str(case.get("case_id") or ""): case
                for case in existing.get("cases") or []
                if case.get("decision")
            }
    for case in cases:
        if case["case_id"] in cached:
            case["decision"] = cached[case["case_id"]]["decision"]
            if cached[case["case_id"]].get("mirror_decision"):
                case["mirror_decision"] = cached[case["case_id"]]["mirror_decision"]

    settings = resolve_vision_runtime_settings()
    broker = default_runtime_broker_client()
    new_count = 0
    await broker.acquire()
    try:
        with httpx.Client(
            base_url=str(settings.api_base).rstrip("/"),
            headers={"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {},
            timeout=180.0,
        ) as client:
            for index, case in enumerate(cases, start=1):
                if case.get("decision"):
                    print(f"pairwise cache: {index}/{len(cases)}", flush=True)
                    continue
                path_a = Path(case["side_a_path"])
                path_b = Path(case["side_b_path"])
                if not path_a.is_file() or not path_b.is_file():
                    raise RuntimeError("Pairwise preview path is missing")
                decision = None
                for attempt in range(1, 4):
                    response = client.post(
                        "/chat/completions",
                        json=_payload(
                            settings.model,
                            path_a,
                            path_b,
                            measurements.get(case["side_a_photo_id"]),
                            measurements.get(case["side_b_photo_id"]),
                        ),
                    )
                    response.raise_for_status()
                    output = _response_text(response.json())
                    try:
                        decision = parse_pairwise_decision(output)
                        break
                    except (json.JSONDecodeError, TypeError, ValueError):
                        print(
                            f"pairwise invalid JSON: {index}/{len(cases)} attempt {attempt}/3",
                            flush=True,
                        )
                if decision is None:
                    case["error"] = "invalid_json_after_3_attempts"
                    _save_private(private_path, job_id, cases)
                    print(f"pairwise skipped: {index}/{len(cases)}", flush=True)
                    continue
                case["decision"] = {
                    "winner": decision.winner,
                    "confidence": decision.confidence,
                    "same_primary_subjects": decision.same_primary_subjects,
                }
                new_count += 1
                _save_private(private_path, job_id, cases)
                await broker.mark_used()
                print(f"pairwise complete: {index}/{len(cases)}", flush=True)

            for index, case in enumerate(cases, start=1):
                if case.get("mirror_decision"):
                    print(f"pairwise mirror cache: {index}/{len(cases)}", flush=True)
                    continue
                path_a = Path(case["side_b_path"])
                path_b = Path(case["side_a_path"])
                mirror_decision = None
                for attempt in range(1, 4):
                    response = client.post(
                        "/chat/completions",
                        json=_payload(
                            settings.model,
                            path_a,
                            path_b,
                            measurements.get(case["side_b_photo_id"]),
                            measurements.get(case["side_a_photo_id"]),
                        ),
                    )
                    response.raise_for_status()
                    output = _response_text(response.json())
                    try:
                        mirror_decision = parse_pairwise_decision(output)
                        break
                    except (json.JSONDecodeError, TypeError, ValueError):
                        print(
                            f"pairwise mirror invalid JSON: {index}/{len(cases)} "
                            f"attempt {attempt}/3",
                            flush=True,
                        )
                if mirror_decision is None:
                    case["mirror_error"] = "invalid_json_after_3_attempts"
                    _save_private(private_path, job_id, cases)
                    print(f"pairwise mirror skipped: {index}/{len(cases)}", flush=True)
                    continue
                case["mirror_decision"] = {
                    "winner": mirror_decision.winner,
                    "confidence": mirror_decision.confidence,
                    "same_primary_subjects": mirror_decision.same_primary_subjects,
                }
                new_count += 1
                _save_private(private_path, job_id, cases)
                await broker.mark_used()
                print(f"pairwise mirror complete: {index}/{len(cases)}", flush=True)
    finally:
        await broker.release()

    single_decisions = {
        case["case_id"]: PairwiseShadowDecision(**case["decision"])
        for case in cases
        if case.get("decision")
    }
    consensus_decisions: dict[str, PairwiseShadowDecision] = {}
    for case in cases:
        if not case.get("decision") or not case.get("mirror_decision"):
            continue
        primary = PairwiseShadowDecision(**case["decision"])
        mirrored = mirror_decision_to_original(
            PairwiseShadowDecision(**case["mirror_decision"])
        )
        consensus_decisions[case["case_id"]] = consensus_pairwise_decision(primary, mirrored)
    public_cases = [
        PairwiseShadowCase(
            case_id=case["case_id"],
            human_side=case["human_side"],
            current_side=case["current_side"],
        )
        for case in cases
    ]
    single_summary = evaluate_pairwise_shadow(public_cases, single_decisions)
    consensus_summary = evaluate_pairwise_shadow(public_cases, consensus_decisions)
    summary = {
        "schema_version": 2,
        "privacy": single_summary["privacy"],
        "sample": single_summary["sample"],
        "single_pass": {
            "comparison": single_summary["comparison"],
            "position": single_summary["position"],
        },
        "mirror_consensus": {
            "sample": consensus_summary["sample"],
            "comparison": consensus_summary["comparison"],
            "position": consensus_summary["position"],
            "promotion_gate": consensus_summary["promotion_gate"],
        },
    }
    summary["runtime"] = {
        "provider": settings.provider,
        "model": settings.model,
        "prompt_version": PAIRWISE_PROMPT_VERSION,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cached_request_count": len(cases) * 2 - new_count,
        "new_count": new_count,
    }
    return summary


def main() -> int:
    args = parse_args()
    summary = asyncio.run(_run(args))
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
