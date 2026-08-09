"""MLX-VLM engine for scene description and event classification."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from photos_mcp.infrastructure.vendor_adapter.compat import resolve_vision_runtime_settings

from ..models import EventType, SceneDescription

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen3.6-35B-A3B-Q4_K_M.gguf"
DEFAULT_BACKEND = "openai_compat"
_LOCAL_VISION_PREFLIGHT_TTL_SECONDS = 30.0
_LOCAL_VISION_PREFLIGHT_CACHE: dict[tuple[str, str], tuple[float, tuple[bool, bool, str]]] = {}

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

# Preserve twice the previous detail for face and photo-quality analysis.
_MAX_IMAGE_DIM = 1024
SCENE_PROMPT_VERSION = "photo-ranker-scene-v1"


@dataclass(frozen=True)
class VLMRuntimeConfig:
    backend: str
    model_path: str
    api_base: str | None
    api_key: str | None
    auto_unload: bool
    target: str


@dataclass(frozen=True)
class OpenAICompatFallbackConfig:
    api_base: str
    api_key: str | None
    model_path: str


def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _flag_enabled(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def build_openai_compat_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_openai_compat_payload(
    *,
    model_path: str,
    prompt_text: str,
    image_b64: str,
) -> dict:
    payload = {
        "model": model_path,
        "messages": [
            {
                "role": "system",
                "content": "Return exactly one JSON object and no extra text.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                        },
                    },
                ],
            },
        ],
        "temperature": 0.1,
    }
    if model_path.startswith("gpt-5"):
        payload["max_completion_tokens"] = 256
    else:
        payload["max_tokens"] = 256
    return payload


def explain_openai_compat_error(
    *,
    status_code: int,
    body_text: str,
    model_path: str,
    api_base: str | None,
) -> str | None:
    lowered = body_text.lower()
    if "only 'text' content type is supported" in lowered:
        return (
            "Configured OpenAI-compatible model does not support vision/image inputs: "
            f"{model_path} ({api_base or 'unknown api base'}). "
            "The endpoint only accepts text content."
        )
    if "image input is not supported" in lowered:
        suffix = ""
        if "mmproj" in lowered:
            suffix = " The runtime reported that an mmproj-backed multimodal model is required."
        return (
            "Configured OpenAI-compatible model does not support vision/image inputs: "
            f"{model_path} ({api_base or 'unknown api base'})." + suffix
        )
    return None


def resolve_openai_compat_fallback() -> OpenAICompatFallbackConfig | None:
    api_base = (_env_first("PHOTO_RANKER_VLM_FALLBACK_API_BASE", default="") or "").strip()
    model_path = (_env_first("PHOTO_RANKER_VLM_FALLBACK_MODEL", default="") or "").strip()
    if not api_base or not model_path:
        return None
    api_key = _env_first("PHOTO_RANKER_VLM_FALLBACK_API_KEY", default="")
    return OpenAICompatFallbackConfig(
        api_base=api_base,
        api_key=api_key,
        model_path=model_path,
    )


def build_openai_compat_client(api_base: str, api_key: str | None):
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is not installed. Install with: uv pip install httpx"
        ) from exc

    headers = build_openai_compat_headers(api_key)
    return httpx.Client(base_url=api_base.rstrip("/"), headers=headers, timeout=90.0)


def should_preflight_local_vision(api_base: str | None) -> bool:
    if not api_base:
        return False
    host = (urlparse(api_base).hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0"}


def probe_openai_compat_vision_support(
    api_base: str,
    model_path: str,
    *,
    client=None,
) -> tuple[bool, bool, str]:
    cache_key = (api_base.rstrip("/"), model_path)
    now = time.monotonic()
    cached = _LOCAL_VISION_PREFLIGHT_CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]

    close_client = False
    if client is None:
        client = build_openai_compat_client(api_base, None)
        close_client = True

    try:
        models_response = client.get("/models")
        if models_response.is_success:
            models_payload = models_response.json()
            advertised_models = models_payload.get("models") or models_payload.get("data") or []
            for advertised in advertised_models:
                capabilities = {str(item).lower() for item in advertised.get("capabilities") or []}
                if "multimodal" in capabilities or "vision" in capabilities:
                    result = (True, True, "vision capability advertised by /models")
                    _LOCAL_VISION_PREFLIGHT_CACHE[cache_key] = (
                        now + _LOCAL_VISION_PREFLIGHT_TTL_SECONDS,
                        result,
                    )
                    return result

        from PIL import Image

        probe_image = Image.new("RGB", (32, 32), color=(128, 128, 128))
        probe_buffer = io.BytesIO()
        probe_image.save(probe_buffer, format="JPEG", quality=80)
        response = client.post(
            "/chat/completions",
            json=build_openai_compat_payload(
                model_path=model_path,
                prompt_text="Return exactly one JSON object.",
                image_b64=base64.b64encode(probe_buffer.getvalue()).decode("ascii"),
            ),
        )
        mapped_error = explain_openai_compat_error(
            status_code=response.status_code,
            body_text=response.text,
            model_path=model_path,
            api_base=api_base,
        )
        if mapped_error is not None:
            result = (False, True, mapped_error)
        else:
            response.raise_for_status()
            result = (True, True, "vision preflight passed")
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        result = (False, False, str(exc))
    finally:
        if close_client:
            client.close()

    _LOCAL_VISION_PREFLIGHT_CACHE[cache_key] = (now + _LOCAL_VISION_PREFLIGHT_TTL_SECONDS, result)
    return result


def resolve_runtime_config(model_path: str | None = None) -> VLMRuntimeConfig:
    settings = resolve_vision_runtime_settings(model_path)

    return VLMRuntimeConfig(
        backend=settings.backend,
        model_path=settings.model,
        api_base=settings.api_base,
        api_key=settings.api_key,
        auto_unload=settings.auto_unload,
        target=settings.target,
    )


class VLMEngine:
    """Wrapper around mlx-vlm for vision-language inference."""

    def __init__(self, model_path: str | None = None):
        runtime = resolve_runtime_config(model_path)
        self._backend = runtime.backend
        self._model_path = runtime.model_path
        self._api_base = runtime.api_base
        self._api_key = runtime.api_key
        self._should_auto_unload = runtime.auto_unload
        self._target = runtime.target
        self._model = None
        self._processor = None
        self._config = None
        self._client = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None or self._client is not None

    @property
    def should_auto_unload(self) -> bool:
        return self._should_auto_unload

    def runtime_metadata(self) -> dict[str, object]:
        """Return reproducibility metadata without endpoint credentials or image data."""
        settings = resolve_vision_runtime_settings(self._model_path)
        return {
            "provider": settings.provider,
            "policy": settings.policy,
            "backend": self._backend,
            "model": self._model_path,
            "target": self._target,
            "prompt_version": SCENE_PROMPT_VERSION,
            "input_max_dimension": _MAX_IMAGE_DIM,
            "auto_unload": self._should_auto_unload,
        }

    def _ensure_loaded(self) -> None:
        if self.is_loaded:
            return
        if self._backend == "openai_compat":
            self._ensure_openai_client()
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

    def _ensure_openai_client(self) -> None:
        if self._client is not None:
            return
        if not self._api_base:
            raise RuntimeError(
                "PHOTO_RANKER_VLM_API_BASE or LOCAL_LLM_BASE_URL is required "
                "when PHOTO_RANKER_VLM_BACKEND=openai_compat"
            )
        self._client = build_openai_compat_client(self._api_base, self._api_key)
        logger.info("OpenAI-compatible VLM client ready: %s (%s)", self._model_path, self._api_base)

    @staticmethod
    def _extract_openai_content(payload: dict) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI-compatible VLM response is missing choices")

        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            return "\n".join(part for part in text_parts if part)
        return str(content)

    def _describe_scene_with_openai_client(
        self,
        *,
        client,
        model_path: str,
        image_b64: str,
        prompt_text: str,
    ) -> SceneDescription:
        payload = build_openai_compat_payload(
            model_path=model_path,
            prompt_text=prompt_text,
            image_b64=image_b64,
        )
        response = client.post("/chat/completions", json=payload)
        mapped_error = explain_openai_compat_error(
            status_code=response.status_code,
            body_text=response.text,
            model_path=model_path,
            api_base=str(getattr(client, "base_url", self._api_base)),
        )
        if mapped_error is not None:
            raise RuntimeError(mapped_error)
        response.raise_for_status()
        return parse_scene_output(self._extract_openai_content(response.json()))

    def _describe_scene_openai_compat(
        self, image_b64: str, prompt_text: str,
    ) -> SceneDescription:
        self._ensure_loaded()

        if should_preflight_local_vision(self._api_base):
            supports_vision, vision_check_ok, vision_message = probe_openai_compat_vision_support(
                self._api_base,
                self._model_path,
                client=self._client,
            )
            if not supports_vision:
                fallback = resolve_openai_compat_fallback()
                if fallback is not None:
                    fallback_client = build_openai_compat_client(fallback.api_base, fallback.api_key)
                    try:
                        return self._describe_scene_with_openai_client(
                            client=fallback_client,
                            model_path=fallback.model_path,
                            image_b64=image_b64,
                            prompt_text=prompt_text,
                        )
                    finally:
                        fallback_client.close()
                if vision_check_ok:
                    raise RuntimeError(vision_message)
                raise RuntimeError(
                    "Unable to verify local vision support before image request: "
                    f"{vision_message}"
                )

        return self._describe_scene_with_openai_client(
            client=self._client,
            model_path=self._model_path,
            image_b64=image_b64,
            prompt_text=prompt_text,
        )

    def describe_scene(
        self, image_b64: str, prompt: str | None = None
    ) -> SceneDescription:
        """Analyze an image and return a structured scene description."""
        prompt_text = prompt or SCENE_PROMPT
        if self._backend == "openai_compat":
            return self._describe_scene_openai_compat(image_b64, prompt_text)

        self._ensure_loaded()
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from PIL import Image

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
        if self._client is not None:
            self._client.close()
            self._client = None
        self._model = None
        self._processor = None
        self._config = None
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
