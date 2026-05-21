from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from photos_mcp.facade.common import call_vendor


@pytest.mark.asyncio
async def test_call_vendor_runs_sync_vendor_functions_off_event_loop(monkeypatch) -> None:
    timestamps: dict[str, float] = {}

    def sync_vendor() -> dict[str, bool]:
        time.sleep(0.2)
        return {"ok": True}

    monkeypatch.setattr(
        "photos_mcp.facade.common.load_vendor_server",
        lambda _name: SimpleNamespace(sync_vendor=sync_vendor),
    )

    start = time.monotonic()

    async def ticker() -> None:
        await __import__("asyncio").sleep(0.02)
        timestamps["tick"] = time.monotonic() - start

    result, _ = await __import__("asyncio").gather(
        call_vendor("photo-source", "sync_vendor"),
        ticker(),
    )

    assert result == {"ok": True}
    assert timestamps["tick"] < 0.12