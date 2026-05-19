#!/usr/bin/env python3
"""Run a read-only MCP smoke test for photo-ranker openai_compat fallback.

This script launches the local photo-ranker MCP server over stdio, injects the
configured primary/fallback VLM environment, and calls the `describe_scene`
tool with either a provided image or a generated JPEG fixture.

Usage:
    cd mcp-servers/photo-ranker
    source ~/.nanobot/nanobot.env
    uv run --python 3.12 python scripts/validate_mcp_openai_compat_fallback.py

    uv run --python 3.12 python scripts/validate_mcp_openai_compat_fallback.py \
      --primary-api-base http://127.0.0.1:1248/v1 \
      --primary-model mlx-community/Qwen3.5-27B-4bit \
      --fallback-model gpt-5.4-mini-2026-03-17 \
      --image /path/to/test.jpg
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
from _script_bootstrap import prepare_photo_ranker_runtime

prepare_photo_ranker_runtime(__file__)

from photos_mcp_vendor_photo_ranker.engines.vlm import probe_openai_compat_vision_support


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate photo-ranker MCP openai_compat fallback with a read-only smoke.",
    )
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="Optional JPEG/PNG image path. If omitted, a generated JPEG fixture is used.",
    )
    parser.add_argument(
        "--generated-image-size",
        type=int,
        default=512,
        help="Fixture image size in pixels when --image is omitted. Default: 512.",
    )
    parser.add_argument(
        "--primary-api-base",
        default=os.environ.get("PHOTO_RANKER_VLM_API_BASE")
        or os.environ.get("LOCAL_LLM_BASE_URL")
        or "http://127.0.0.1:1248/v1",
        help="Primary OpenAI-compatible API base. Default prefers PHOTO_RANKER_VLM_API_BASE, then LOCAL_LLM_BASE_URL.",
    )
    parser.add_argument(
        "--primary-model",
        default=os.environ.get("PHOTO_RANKER_VLM_MODEL")
        or os.environ.get("LOCAL_LLM_MODEL")
        or "mlx-community/Qwen3.5-27B-4bit",
        help="Primary model id. Default prefers PHOTO_RANKER_VLM_MODEL, then LOCAL_LLM_MODEL.",
    )
    parser.add_argument(
        "--fallback-api-base",
        default=os.environ.get("PHOTO_RANKER_VLM_FALLBACK_API_BASE", "https://api.openai.com/v1"),
        help="Fallback OpenAI-compatible API base.",
    )
    parser.add_argument(
        "--fallback-model",
        default=os.environ.get("PHOTO_RANKER_VLM_FALLBACK_MODEL", "gpt-5.4-mini-2026-03-17"),
        help="Fallback model id.",
    )
    parser.add_argument(
        "--fallback-api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable name containing the fallback API key. Default: OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--prompt",
        default="Return exactly one JSON object describing this generated smoke-test image.",
        help="Prompt sent to describe_scene.",
    )
    parser.add_argument(
        "--server-command",
        default="uv",
        help="Command used to launch the MCP server. Default: uv.",
    )
    parser.add_argument(
        "--server-args",
        nargs="+",
        default=["run", "--python", "3.12", "server.py"],
        help="Arguments used to launch the MCP server over stdio.",
    )
    return parser.parse_args()


def build_fixture(path: Path, size: int) -> Path:
    size = max(64, size)
    image = Image.new("RGB", (size, size), (220, 40, 40))
    image.save(path, format="JPEG", quality=95)
    return path


def resolve_image_path(args: argparse.Namespace) -> Path:
    if args.image is not None:
        if not args.image.exists():
            raise FileNotFoundError(f"Image not found: {args.image}")
        return args.image

    tmp_dir = Path(tempfile.mkdtemp(prefix="photo-ranker-fallback-smoke-"))
    return build_fixture(tmp_dir / f"fixture-{args.generated_image_size}.jpg", args.generated_image_size)


def image_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def require_fallback_key(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise RuntimeError(f"Missing fallback API key in environment variable: {env_name}")
    return value


async def run_mcp_smoke(args: argparse.Namespace, image_b64: str, fallback_api_key: str) -> list[str]:
    server_env = dict(os.environ)
    server_env.update({
        "PHOTO_RANKER_VLM_BACKEND": "openai_compat",
        "PHOTO_RANKER_VLM_API_BASE": args.primary_api_base,
        "PHOTO_RANKER_VLM_MODEL": args.primary_model,
        "PHOTO_RANKER_VLM_FALLBACK_API_BASE": args.fallback_api_base,
        "PHOTO_RANKER_VLM_FALLBACK_MODEL": args.fallback_model,
        "PHOTO_RANKER_VLM_FALLBACK_API_KEY": fallback_api_key,
    })

    server = StdioServerParameters(
        command=args.server_command,
        args=args.server_args,
        cwd=PROJECT_ROOT,
        env=server_env,
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not any(tool.name == "describe_scene" for tool in tools.tools):
                raise RuntimeError("photo-ranker MCP server did not expose describe_scene")

            result = await session.call_tool(
                "describe_scene",
                {
                    "image_b64": image_b64,
                    "prompt": args.prompt,
                },
            )

            payloads: list[str] = []
            for item in result.content:
                text = getattr(item, "text", None)
                if text is not None:
                    payloads.append(text)
            return payloads


def validate_payloads(payloads: list[str]) -> dict:
    if not payloads:
        raise RuntimeError("describe_scene returned no text payloads")
    try:
        parsed = json.loads(payloads[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"describe_scene returned non-JSON payload: {payloads[0]!r}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"describe_scene returned unexpected payload type: {type(parsed).__name__}")
    return parsed


def main() -> int:
    args = parse_args()
    image_path = resolve_image_path(args)
    image_b64 = image_to_b64(image_path)
    fallback_api_key = require_fallback_key(args.fallback_api_key_env)

    probe = probe_openai_compat_vision_support(args.primary_api_base, args.primary_model)
    print("primary_probe=", json.dumps({
        "supports_vision": probe[0],
        "check_ok": probe[1],
        "message": probe[2],
    }, ensure_ascii=False))
    print("image_path=", image_path)
    print("image_size_bytes=", image_path.stat().st_size)

    payloads = anyio.run(run_mcp_smoke, args, image_b64, fallback_api_key)
    parsed = validate_payloads(payloads)
    print("describe_scene_payload=", json.dumps(parsed, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())