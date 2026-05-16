"""MLX-VLM engine for scene description and event classification."""

from __future__ import annotations

import base64
import io
import json
import logging
import tempfile
from pathlib import Path

from models import EventType, SceneDescription

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"

SCENE_PROMPT = """\
당신은 사진 분류 전문가입니다. 아래 사진을 분석하고 반드시 JSON 하나만 출력하세요.

event_type 판단 기준 (우선순위 순서대로 확인):
1. birthday: 반드시 다음 중 하나 이상이 보여야 함: (a) 케이크 위에 촛불이 켜져 있음, (b) "Happy Birthday" 또는 "생일 축하" 문구가 보임, (c) 나이 숫자가 적힌 풍선·케이크 토퍼. 풍선·파티모자만 있고 위 단서가 없으면 birthday가 아니라 celebration
2. graduation: 학사모, 졸업 가운, 졸업장이 보임
3. celebration: 풍선·배너·화환·파티모자 등 파티 장식, 또는 샴페인/와인잔 건배. birthday 고유 단서(촛불 케이크, 생일 문구, 나이 숫자)가 없는 파티는 모두 celebration. 단순히 사람이 모인 것만으로는 celebration이 아님
4. travel: 다음 단서 중 하나라도 보이면 travel: (a) 유명 관광지·랜드마크·역사적 건축물(에펠탑, 타지마할, 자유의여신상, 콜로세움, 만리장성, 성당, 사원, 궁전 등), (b) 공항·비행기 내부·탑승권·여권·여행가방·캐리어, (c) 호텔로비·리조트 수영장·관광버스, (d) 외국어 간판·관광 안내판·지도, (e) 전망대·케이블카·유람선·열기구, (f) 비행기 창문에서 본 풍경·항공촬영. 자연 풍경이라도 관광객·백팩·셀카봉·기념촬영 포즈가 보이면 travel
5. meal: 음식·음료·디저트·케이크(촛불 없는)가 사진의 주요 피사체. 사람이 함께 있어도 음식이 주 피사체면 meal
6. portrait: 인물이 화면 면적의 50% 이상을 차지하고 인물 자체가 주제. 배경이 일상 공간이라도 인물이 주요 피사체이면 portrait
7. outdoor: 자연 풍경(산, 바다, 공원, 해변)이 주제이고 위 travel 단서(a~f)가 전혀 없음. 의심스러우면 travel을 우선 고려
8. daily: 일상 공간(사무실, 카페, 집)이 주 배경이며 인물 비중이 작거나 없음, 특별한 이벤트 없는 평범한 장면

주의:
- 음식이 화면 중심에 있으면 meal을 우선 고려
- 사람들이 모여 있어도 파티 장식이 없으면 celebration이 아님
- birthday vs celebration: 촛불 케이크·생일 문구·나이 숫자 없이 풍선/장식만 있으면 celebration
- 확신이 없을 때: 위 8가지 중 가장 유사한 유형을 선택하고 event_confidence를 0.3-0.5로 낮추세요. "other"는 위 8가지 어디에도 전혀 해당하지 않을 때만 사용

event_confidence: 핵심 단서 2개 이상=0.9, 1개=0.7, 약함=0.5, 소거법=0.3

meaningful_score 기준: 특별한 행사(생일,졸업)=9-10, 가족·여행·축하=7-8, 좋은 풍경·음식=5-6, 평범한 일상=3-4, 흐릿하거나 의미 없음=1-2

JSON만 출력:
{"scene":"한 문장 설명","people_count":0,"is_family_photo":false,"expressions":[],"event_type":"","event_confidence":0.0,"quality_notes":"","meaningful_score":1}"""

# Max dimension for input images (resize to save VLM inference time)
_MAX_IMAGE_DIM = 512


class VLMEngine:
    """Wrapper around mlx-vlm for vision-language inference."""

    def __init__(self, model_path: str = DEFAULT_MODEL):
        self._model_path = model_path
        self._model = None
        self._processor = None
        self._config = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            self._model, self._processor = load(self._model_path)
            self._config = load_config(self._model_path)
            logger.info("VLM model loaded: %s", self._model_path)
        except ImportError:
            raise RuntimeError(
                "mlx-vlm is not installed. "
                "Install with: uv pip install mlx-vlm"
            )

    def describe_scene(
        self, image_b64: str, prompt: str | None = None
    ) -> SceneDescription:
        """Analyze an image and return a structured scene description."""
        self._ensure_loaded()
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from PIL import Image

        prompt_text = prompt or SCENE_PROMPT

        # Decode and resize to limit VLM inference cost
        img_bytes = base64.b64decode(image_b64)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        if max(image.size) > _MAX_IMAGE_DIM:
            image.thumbnail((_MAX_IMAGE_DIM, _MAX_IMAGE_DIM), Image.LANCZOS)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            image.save(f, format="JPEG", quality=85)
            temp_path = f.name

        try:
            formatted_prompt = apply_chat_template(
                self._processor,
                self._config,
                prompt_text,
                num_images=1,
            )
            result = generate(
                self._model,
                self._processor,
                formatted_prompt,
                image=temp_path,
                max_tokens=256,
                verbose=False,
            )
            # generate returns GenerationResult; extract text
            output_text = result.text if hasattr(result, "text") else str(result)
            return parse_scene_output(output_text)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def classify_event(self, image_b64: str) -> tuple[EventType, float]:
        """Classify the event type of an image."""
        scene = self.describe_scene(image_b64)
        return scene.event_type, scene.event_confidence

    def unload(self) -> None:
        """Release model from memory."""
        self._model = None
        self._processor = None
        logger.info("VLM model unloaded")


def parse_scene_output(raw_output: str) -> SceneDescription:
    """Parse VLM JSON output into SceneDescription."""
    try:
        start = raw_output.find("{")
        end = raw_output.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw_output[start:end])
        else:
            raise ValueError("No JSON block found in output")
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse VLM JSON output, using fallback")
        data = {
            "scene": raw_output[:200],
            "people_count": 0,
            "is_family_photo": False,
            "expressions": [],
            "event_type": "other",
            "event_confidence": 0.0,
            "quality_notes": "parse_error",
            "meaningful_score": 5,
        }

    event_str = str(data.get("event_type", "other")).lower()
    try:
        event_type = EventType(event_str)
    except ValueError:
        event_type = EventType.OTHER

    # Safely parse numeric fields (VLM may return non-numeric text)
    def safe_int(val, default: int = 0) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def safe_float(val, default: float = 0.0) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    return SceneDescription(
        scene=str(data.get("scene", "")),
        people_count=safe_int(data.get("people_count", 0)),
        is_family_photo=bool(data.get("is_family_photo", False)),
        expressions=list(data.get("expressions", [])),
        event_type=event_type,
        event_confidence=safe_float(data.get("event_confidence", 0.0)),
        quality_notes=str(data.get("quality_notes", "")),
        meaningful_score=safe_int(data.get("meaningful_score", 5), default=5),
        raw_json=data,
    )
