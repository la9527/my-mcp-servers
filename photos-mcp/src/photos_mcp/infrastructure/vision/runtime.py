from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REMOTE_ALLOWED = "remote_allowed"
LOCAL_ONLY = "local_only"
SUPPORTED_POLICIES = {REMOTE_ALLOWED, LOCAL_ONLY}

DEFAULT_PROVIDER = "linux_qwen36"
DEFAULT_BACKEND = "openai_compat"
DEFAULT_MODEL = "Qwen3.6-35B-A3B-Q4_K_M.gguf"
DEFAULT_API_BASE = "http://127.0.0.1:12801/v1"
DEFAULT_TARGET = "linux-qwen36-vlm"
DEFAULT_PREPARE_TIMEOUT_SECONDS = 330.0
DEFAULT_LOCAL_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
DEFAULT_LOCAL_TARGET = "qwen3-vl-4b"


@dataclass(frozen=True, slots=True)
class VisionRuntimeSettings:
    policy: str
    provider: str
    backend: str
    model: str
    api_base: str | None
    api_key: str | None
    target: str
    prepare_command: str
    activity_command: str
    prepare_timeout_seconds: float
    auto_unload: bool

    @property
    def is_remote(self) -> bool:
        return self.provider == DEFAULT_PROVIDER

    @property
    def is_on_demand(self) -> bool:
        return bool(self.prepare_command)


def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _flag_enabled(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _normalize_policy(value: str | None) -> str:
    policy = (value or REMOTE_ALLOWED).strip().lower()
    return policy if policy in SUPPORTED_POLICIES else REMOTE_ALLOWED


def _default_prepare_command() -> str:
    return str(Path.home() / "bin" / "ensure-linux-llama-cpp")


def _default_activity_command() -> str:
    return str(Path.home() / "bin" / "touch-linux-llm-activity")


def resolve_vision_runtime_settings(
    model_override: str | None = None,
) -> VisionRuntimeSettings:
    policy = _normalize_policy(os.environ.get("PHOTOS_MCP_VLM_POLICY"))
    auto_unload = _flag_enabled(
        os.environ.get("PHOTO_RANKER_VLM_AUTO_UNLOAD"),
        default=True,
    )

    if policy == LOCAL_ONLY:
        return VisionRuntimeSettings(
            policy=policy,
            provider="mlx_local",
            backend="mlx",
            model=(
                model_override
                or os.environ.get("PHOTOS_MCP_LOCAL_VLM_MODEL")
                or os.environ.get("PHOTO_RANKER_VLM_MODEL")
                or DEFAULT_LOCAL_MODEL
            ),
            api_base=None,
            api_key=None,
            target=os.environ.get("PHOTOS_MCP_LOCAL_VLM_TARGET", DEFAULT_LOCAL_TARGET),
            prepare_command="",
            activity_command="",
            prepare_timeout_seconds=0.0,
            auto_unload=auto_unload,
        )

    backend = (
        _env_first("PHOTO_RANKER_VLM_BACKEND", default=DEFAULT_BACKEND)
        or DEFAULT_BACKEND
    ).strip().lower()
    if backend not in {"mlx", "openai_compat"}:
        backend = DEFAULT_BACKEND

    if backend == "mlx":
        return VisionRuntimeSettings(
            policy=policy,
            provider="mlx_local",
            backend=backend,
            model=model_override or os.environ.get("PHOTO_RANKER_VLM_MODEL") or DEFAULT_LOCAL_MODEL,
            api_base=None,
            api_key=None,
            target=os.environ.get("PHOTO_RANKER_VLM_TARGET", DEFAULT_LOCAL_TARGET),
            prepare_command="",
            activity_command="",
            prepare_timeout_seconds=0.0,
            auto_unload=auto_unload,
        )

    explicit_api_base = _env_first("PHOTO_RANKER_VLM_API_BASE", "LOCAL_LLM_BASE_URL")
    api_base = (
        explicit_api_base
        or os.environ.get("PHOTOS_MCP_LINUX_VLM_API_BASE")
        or DEFAULT_API_BASE
    ).rstrip("/")
    explicit_target = os.environ.get("PHOTO_RANKER_VLM_TARGET", "").strip()
    explicit_provider = os.environ.get("PHOTOS_MCP_VLM_PROVIDER", "").strip()

    if explicit_provider:
        provider = explicit_provider
    elif explicit_target and explicit_target != DEFAULT_TARGET:
        provider = "local_openai_compat" if api_base.startswith(("http://127.0.0.1", "http://localhost")) else "openai_compat"
    elif explicit_api_base and api_base != DEFAULT_API_BASE:
        provider = "local_openai_compat" if api_base.startswith(("http://127.0.0.1", "http://localhost")) else "openai_compat"
    else:
        provider = DEFAULT_PROVIDER

    if provider == DEFAULT_PROVIDER:
        model = (
            model_override
            or os.environ.get("PHOTO_RANKER_VLM_MODEL")
            or os.environ.get("PHOTOS_MCP_LINUX_VLM_MODEL")
            or DEFAULT_MODEL
        )
        target = explicit_target or DEFAULT_TARGET
        prepare_command = (
            os.environ.get("PHOTOS_MCP_LINUX_VLM_PREPARE_COMMAND")
            or _default_prepare_command()
        )
        activity_command = (
            os.environ.get("PHOTOS_MCP_LINUX_VLM_ACTIVITY_COMMAND")
            or _default_activity_command()
        )
    else:
        model = (
            model_override
            or _env_first("PHOTO_RANKER_VLM_MODEL", "LOCAL_LLM_MODEL")
            or DEFAULT_LOCAL_MODEL
        )
        target = explicit_target or DEFAULT_LOCAL_TARGET
        prepare_command = os.environ.get("PHOTOS_MCP_VLM_PREPARE_COMMAND", "")
        activity_command = os.environ.get("PHOTOS_MCP_VLM_ACTIVITY_COMMAND", "")

    return VisionRuntimeSettings(
        policy=policy,
        provider=provider,
        backend=backend,
        model=model,
        api_base=api_base,
        api_key=_env_first("PHOTO_RANKER_VLM_API_KEY", "LOCAL_LLM_API_KEY", default=""),
        target=target,
        prepare_command=prepare_command,
        activity_command=activity_command,
        prepare_timeout_seconds=float(
            os.environ.get(
                "PHOTOS_MCP_LINUX_VLM_PREPARE_TIMEOUT_SECONDS",
                str(DEFAULT_PREPARE_TIMEOUT_SECONDS),
            )
        ),
        auto_unload=auto_unload,
    )


def _openai_compat_ready(api_base: str, timeout_seconds: float) -> bool:
    request = Request(f"{api_base.rstrip('/')}/models", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def vision_runtime_summary(*, check_ready: bool = False) -> dict[str, object]:
    settings = resolve_vision_runtime_settings()
    ready = False
    if check_ready and settings.backend == "openai_compat" and settings.api_base:
        ready = _openai_compat_ready(settings.api_base, timeout_seconds=0.35)

    if ready:
        status = "ready"
    elif settings.is_on_demand:
        status = "on_demand"
    else:
        status = "configured"

    return {
        "status": status,
        "ready": ready,
        "policy": settings.policy,
        "provider": settings.provider,
        "backend": settings.backend,
        "model": settings.model,
        "api_base": settings.api_base or "",
        "on_demand": settings.is_on_demand,
        "remote_allowed": settings.policy == REMOTE_ALLOWED,
        "local_only_override": "PHOTOS_MCP_VLM_POLICY=local_only",
    }
