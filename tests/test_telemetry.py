"""Tests for cli.telemetry — agent registration and heartbeat."""
import os
from unittest.mock import patch

from cli.telemetry import TelemetryClient, create_telemetry, _get_version, _detect_deploy_mode


def _make_client(**kwargs):
    defaults = dict(
        wallet_address="0xABCDEF1234567890abcdef1234567890ABCDEF12",
        strategy_name="apex",
        network="testnet",
        deploy_mode="local",
        version="0.1.0",
    )
    defaults.update(kwargs)
    return TelemetryClient(**defaults)


class TestInstanceId:
    def test_deterministic(self):
        c1 = _make_client()
        c2 = _make_client()
        assert c1.instance_id == c2.instance_id

    def test_case_insensitive_address(self):
        c1 = _make_client(wallet_address="0xABCD")
        c2 = _make_client(wallet_address="0xabcd")
        assert c1.instance_id == c2.instance_id

    def test_different_strategy_different_id(self):
        c1 = _make_client(strategy_name="apex")
        c2 = _make_client(strategy_name="cfi_hedge")
        assert c1.instance_id != c2.instance_id

    def test_16_char_hex(self):
        c = _make_client()
        assert len(c.instance_id) == 16
        int(c.instance_id, 16)  # valid hex


class TestEnabled:
    def test_disabled_when_no_url(self):
        c = _make_client()
        with patch("cli.telemetry.TELEMETRY_BASE", ""):
            assert c.enabled is False

    def test_enabled_when_url_set(self):
        c = _make_client()
        with patch("cli.telemetry.TELEMETRY_BASE", "https://example.com/telemetry"):
            assert c.enabled is True

    def test_disabled_via_env(self):
        c = _make_client()
        with patch("cli.telemetry.TELEMETRY_BASE", "https://example.com/telemetry"):
            with patch.dict(os.environ, {"NUNCHI_TELEMETRY": "false"}):
                assert c.enabled is False

    def test_disabled_case_insensitive(self):
        c = _make_client()
        with patch("cli.telemetry.TELEMETRY_BASE", "https://example.com/telemetry"):
            with patch.dict(os.environ, {"NUNCHI_TELEMETRY": "False"}):
                assert c.enabled is False


class TestHeartbeatInterval:
    def test_should_heartbeat_at_interval(self):
        c = _make_client()
        assert c.should_heartbeat(0) is False
        assert c.should_heartbeat(10) is True
        assert c.should_heartbeat(20) is True
        assert c.should_heartbeat(7) is False


class TestRegisterNoBlock:
    def test_register_does_not_raise_on_network_error(self):
        """Register should never raise — fire and forget."""
        c = _make_client()
        # Even with a bad URL, register should not raise
        with patch("cli.telemetry.TELEMETRY_BASE", "http://localhost:1"):
            c.register()  # no exception

    def test_register_skipped_when_disabled(self):
        c = _make_client()
        with patch.dict(os.environ, {"NUNCHI_TELEMETRY": "false"}):
            with patch("cli.telemetry.Thread") as mock_thread:
                c.register()
                mock_thread.assert_not_called()

    def test_heartbeat_skipped_when_disabled(self):
        c = _make_client()
        with patch.dict(os.environ, {"NUNCHI_TELEMETRY": "false"}):
            with patch("cli.telemetry.Thread") as mock_thread:
                c.heartbeat(10, 600.0, 2)
                mock_thread.assert_not_called()


class TestDeployMode:
    def test_local_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _detect_deploy_mode() == "local"

    def test_railway(self):
        with patch.dict(os.environ, {"RAILWAY_SERVICE_NAME": "agent-cli"}):
            assert _detect_deploy_mode() == "railway"


class TestFactory:
    def test_create_telemetry_returns_client(self):
        client = create_telemetry("0xDEAD", "apex")
        assert isinstance(client, TelemetryClient)
        assert client.wallet_address == "0xDEAD"
        assert client.strategy_name == "apex"


class TestVersion:
    def test_returns_string(self):
        v = _get_version()
        assert isinstance(v, str)
        assert len(v) > 0
