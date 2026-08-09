"""Single application-facing gateway for bundled vendor calls."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from photos_mcp.vendor_loader import load_vendor_server


def parse_vendor_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def call_vendor(server_name: str, function_name: str, *args: Any, **kwargs: Any) -> Any:
    module = load_vendor_server(server_name)
    function = getattr(module, function_name)
    if inspect.iscoroutinefunction(function):
        result = function(*args, **kwargs)
    else:
        result = await asyncio.to_thread(function, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return parse_vendor_payload(result)


__all__ = ["call_vendor", "load_vendor_server", "parse_vendor_payload"]
