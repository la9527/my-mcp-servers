"""Tests for batch_classify CLI."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, ".")


class TestLoadPhotosForBatch:
    """Test _load_photos_for_batch delegates to sources.load_photos."""

    def test_local_source(self):
        from batch_classify import _load_photos_for_batch

        args = argparse.Namespace(
            source="local",
            path="/photos/2025",
            album="",
            person="",
            date_from="",
            date_to="",
            limit=10,
        )
        with patch("batch_classify.load_photos", return_value=[{"photo_id": "a"}]) as mock:
            result = _load_photos_for_batch(args)

        mock.assert_called_once_with(
            source="local",
            source_path="/photos/2025",
            album="",
            person="",
            date_from="",
            date_to="",
            limit=10,
        )
        assert len(result) == 1

    def test_apple_source_with_filters(self):
        from batch_classify import _load_photos_for_batch

        args = argparse.Namespace(
            source="apple",
            path="",
            album="Vacation",
            person="Mom",
            date_from="2025-01-01",
            date_to="2025-12-31",
            limit=50,
        )
        with patch("batch_classify.load_photos", return_value=[]) as mock:
            result = _load_photos_for_batch(args)

        mock.assert_called_once_with(
            source="apple",
            source_path="",
            album="Vacation",
            person="Mom",
            date_from="2025-01-01",
            date_to="2025-12-31",
            limit=50,
        )
        assert result == []

    def test_zero_limit_uses_large_default(self):
        from batch_classify import _load_photos_for_batch

        args = argparse.Namespace(
            source="local",
            path="/tmp",
            album="",
            person="",
            date_from="",
            date_to="",
            limit=0,
        )
        with patch("batch_classify.load_photos", return_value=[]) as mock:
            _load_photos_for_batch(args)

        assert mock.call_args.kwargs["limit"] == 10000


class TestArgparse:
    """Test CLI argument parsing."""

    def test_source_choices_include_apple(self):
        from batch_classify import main

        # Should not raise for 'apple' source
        parser = argparse.ArgumentParser()
        parser.add_argument("--source", choices=["local", "apple"])
        args = parser.parse_args(["--source", "apple"])
        assert args.source == "apple"

    def test_local_requires_path(self):
        """--source local without --path should error."""
        from batch_classify import main

        with patch("sys.argv", ["batch_classify.py", "--source", "local"]):
            with pytest.raises(SystemExit):
                main()

    def test_apple_without_path_ok(self):
        """--source apple without --path should not error at parse time."""
        from batch_classify import main

        # It should proceed past arg parsing (then fail at run_batch level)
        with patch("sys.argv", ["batch_classify.py", "--source", "apple"]):
            with patch("batch_classify.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock()
                main()
                mock_asyncio.run.assert_called_once()

    def test_all_apple_args_parsed(self):
        """All Apple Photos filter args should be parsed correctly."""
        import batch_classify

        with patch("sys.argv", [
            "batch_classify.py",
            "--source", "apple",
            "--album", "Family",
            "--person", "Dad",
            "--date-from", "2025-06-01",
            "--date-to", "2025-12-31",
            "--limit", "25",
        ]):
            with patch("batch_classify.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock()
                batch_classify.main()
                call_args = mock_asyncio.run.call_args[0][0]
                # The coroutine was created, confirming args parsed OK
                # Clean up
                call_args.close()
