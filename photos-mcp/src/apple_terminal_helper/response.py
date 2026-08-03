"""Structured response helpers shared by Terminal-hosted Photos operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_terminal_response(path: Path, request: dict[str, Any], result: dict[str, Any] | list[Any]) -> None:
    """Write a request-correlated success envelope without logging its payload."""
    path.write_text(
        json.dumps(
            {
                "request_id": str(request.get("request_id") or ""),
                "status": "ok",
                "result": result,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
