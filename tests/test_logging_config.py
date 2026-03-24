"""Tests for common.logging_config — production logging setup."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from common.logging_config import (
    JSONFormatter,
    ErrorRateTracker,
    configure_logging,
    resolve_obsidian_path,
)


class TestJSONFormatter:
    def test_basic_format(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        raw = fmt.format(record)
        parsed = json.loads(raw)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert parsed["msg"] == "hello world"
        assert "ts" in parsed

    def test_extra_fields(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="trade", args=(), exc_info=None,
        )
        record.event_type = "fill"
        record.instrument = "ETH-PERP"
        record.side = "buy"
        raw = fmt.format(record)
        parsed = json.loads(raw)
        assert parsed["event_type"] == "fill"
        assert parsed["instrument"] == "ETH-PERP"
        assert parsed["side"] == "buy"

    def test_exception_included(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="err", args=(), exc_info=sys.exc_info(),
            )
        raw = fmt.format(record)
        parsed = json.loads(raw)
        assert "ValueError: boom" in parsed["exc"]


class TestErrorRateTracker:
    def test_count_within_window(self):
        tracker = ErrorRateTracker(window_s=300, threshold=5)
        for _ in range(3):
            tracker.record_error()
        assert tracker.count == 3

    def test_threshold_warning(self, caplog):
        tracker = ErrorRateTracker(window_s=300, threshold=3)
        with caplog.at_level(logging.WARNING, logger="error_rate"):
            for _ in range(3):
                tracker.record_error()
        assert any("Error rate exceeded" in r.message for r in caplog.records)


class TestConfigureLogging:
    def test_creates_log_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            configure_logging(
                strategy_name="test_strat",
                log_dir=tmpdir,
                json_logs=False,
                level=logging.DEBUG,
            )
            log = logging.getLogger("test_configure")
            log.info("hello from test")

            # Flush handlers
            for h in logging.getLogger().handlers:
                h.flush()

            log_files = list(Path(tmpdir).glob("test_strat-*.log"))
            assert len(log_files) == 1
            content = log_files[0].read_text()
            assert "hello from test" in content

        # Cleanup root logger handlers to avoid polluting other tests
        logging.getLogger().handlers.clear()

    def test_json_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            configure_logging(
                strategy_name="json_test",
                log_dir=tmpdir,
                json_logs=True,
                level=logging.DEBUG,
            )
            log = logging.getLogger("json_check")
            log.info("json line")

            for h in logging.getLogger().handlers:
                h.flush()

            log_files = list(Path(tmpdir).glob("json_test-*.log"))
            assert len(log_files) == 1
            line = log_files[0].read_text().strip().split("\n")[0]
            parsed = json.loads(line)
            assert parsed["msg"] == "json line"

        logging.getLogger().handlers.clear()


class TestResolveObsidianPath:
    def test_explicit_path_returned(self):
        assert resolve_obsidian_path("/some/path") == "/some/path"

    def test_empty_with_existing_vault(self, tmp_path):
        vault = tmp_path / "obsidian-vault"
        vault.mkdir()
        with patch.dict(os.environ, {"HOME": str(tmp_path)}):
            with patch("common.logging_config.os.path.expanduser",
                       return_value=str(vault)):
                result = resolve_obsidian_path("")
                assert result == str(vault)

    def test_empty_no_vault(self, tmp_path):
        with patch("common.logging_config.os.path.expanduser",
                   return_value=str(tmp_path / "nonexistent")):
            result = resolve_obsidian_path("")
            assert result == ""
