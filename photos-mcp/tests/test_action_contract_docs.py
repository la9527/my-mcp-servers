from __future__ import annotations

from pathlib import Path
import re

from photos_mcp.facade.action_options import ACTION_SPECS


CATALOG_PATH = Path(__file__).resolve().parents[1] / "docs" / "03-integration" / "02-tool-reference.md"
CONTRACT_PATTERN = re.compile(r"\|\s*`(photos_(?:query|select|write|workflow))`\s*\|\s*`([a-z0-9_]+)`\s*\|")


def documented_actions() -> set[tuple[str, str]]:
    document = CATALOG_PATH.read_text(encoding="utf-8")
    start = "<!-- action-contract:start -->"
    end = "<!-- action-contract:end -->"
    assert start in document and end in document, "MCP action contract markers are required"
    contract = document.split(start, 1)[1].split(end, 1)[0]
    return set(CONTRACT_PATTERN.findall(contract))


def test_documented_action_contract_matches_registry() -> None:
    assert documented_actions() == set(ACTION_SPECS)
