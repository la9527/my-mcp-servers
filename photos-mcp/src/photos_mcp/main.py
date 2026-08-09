"""Stable console entry point for Photos MCP."""

from photos_mcp.app.main import main, run_cli

__all__ = ["main", "run_cli"]


if __name__ == "__main__":
    main()
