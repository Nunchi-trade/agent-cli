"""Tests for common/log_filter.py — secret masking in log output."""
import logging
import pytest

from common.log_filter import SecretFilter, install_secret_filter, _HEX_KEY_RE, _BARE_HEX_RE


@pytest.fixture
def secret_filter():
    return SecretFilter()


def _make_record(msg, args=None):
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=args, exc_info=None,
    )
    return record


class TestHexKeyRedaction:
    def test_redacts_0x_prefixed_key(self, secret_filter):
        key = "0x" + "ab" * 32  # 0x + 64 hex chars
        record = _make_record(f"Using key {key} for signing")
        secret_filter.filter(record)
        assert "[REDACTED_KEY]" in record.msg
        assert "abab" not in record.msg

    def test_redacts_multiple_keys(self, secret_filter):
        key1 = "0x" + "ab" * 32
        key2 = "0x" + "cd" * 32
        record = _make_record(f"Keys: {key1} and {key2}")
        secret_filter.filter(record)
        assert record.msg.count("[REDACTED_KEY]") == 2

    def test_does_not_redact_short_hex(self, secret_filter):
        record = _make_record("Address 0x1234567890abcdef is fine")
        secret_filter.filter(record)
        assert "0x1234567890abcdef" in record.msg

    def test_does_not_redact_non_hex(self, secret_filter):
        record = _make_record("Normal log message with numbers 12345")
        secret_filter.filter(record)
        assert "Normal log message" in record.msg


class TestBareHexRedaction:
    def test_redacts_bare_64_char_hex(self, secret_filter):
        bare_key = "ab" * 32  # 64 hex chars without 0x
        record = _make_record(f"Key is {bare_key} here")
        secret_filter.filter(record)
        assert "[REDACTED_HEX]" in record.msg


class TestArgsRedaction:
    def test_redacts_keys_in_args(self, secret_filter):
        key = "0x" + "ab" * 32
        record = _make_record("Using key %s", (key,))
        secret_filter.filter(record)
        assert "[REDACTED_KEY]" in record.args[0]

    def test_non_string_args_unchanged(self, secret_filter):
        record = _make_record("Count: %d, Price: %f", (42, 3.14))
        secret_filter.filter(record)
        assert record.args == (42, 3.14)


class TestFilterBehavior:
    def test_always_returns_true(self, secret_filter):
        record = _make_record("anything")
        assert secret_filter.filter(record) is True

    def test_non_string_msg_unchanged(self, secret_filter):
        record = _make_record(12345)
        secret_filter.filter(record)
        assert record.msg == 12345


class TestInstallFilter:
    def test_install_adds_filter_to_root(self):
        root = logging.getLogger()
        initial_count = len(root.filters)
        install_secret_filter()
        assert len(root.filters) == initial_count + 1
        # Cleanup
        root.filters = [f for f in root.filters if not isinstance(f, SecretFilter)]
