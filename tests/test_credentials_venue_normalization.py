import os
from unittest.mock import patch

import pytest


class TestAppPathResolution:
    def test_resolve_app_home_prefers_primary_then_legacy(self, tmp_path):
        from common import app_paths

        home = tmp_path / 'home'
        home.mkdir()
        primary = home / '.agent-cli'
        legacy = home / '.hl-agent'
        legacy.mkdir()

        with patch('pathlib.Path.home', return_value=home):
            assert app_paths.resolve_app_home() == legacy
            primary.mkdir()
            assert app_paths.resolve_app_home() == primary


class TestVenueAwarePrivateKeyResolution:
    def test_private_key_env_vars_for_paradex(self):
        from common.credentials import private_key_env_vars_for_venue

        assert private_key_env_vars_for_venue("paradex") == [
            "PARADEX_PRIVATE_KEY",
            "PARADEX_L2_PRIVATE_KEY",
            "AGENT_PRIVATE_KEY",
        ]

    def test_resolve_private_key_uses_paradex_specific_env(self):
        from common.credentials import (
            resolve_private_key,
            MacOSKeychainBackend,
            EncryptedKeystoreBackend,
            RailwayEnvBackend,
            FlatFileBackend,
        )

        with patch.object(MacOSKeychainBackend, "available", return_value=False), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value=None), \
             patch.object(RailwayEnvBackend, "available", return_value=False), \
             patch.object(FlatFileBackend, "available", return_value=True), \
             patch.object(FlatFileBackend, "get_key", return_value=None), \
             patch.dict(os.environ, {"PARADEX_L2_PRIVATE_KEY": "0xparadexkey"}, clear=True):
            assert resolve_private_key(venue="paradex") == "0xparadexkey"

    def test_resolve_private_key_prefers_explicit_env_over_keystore(self):
        from common.credentials import (
            resolve_private_key,
            MacOSKeychainBackend,
            EncryptedKeystoreBackend,
            RailwayEnvBackend,
            FlatFileBackend,
        )

        with patch.object(MacOSKeychainBackend, "available", return_value=False), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value="0xkeystore"), \
             patch.object(RailwayEnvBackend, "available", return_value=False), \
             patch.object(FlatFileBackend, "available", return_value=False), \
             patch.dict(os.environ, {"PARADEX_L2_PRIVATE_KEY": "0xparadexkey"}, clear=True):
            assert resolve_private_key(venue="paradex") == "0xparadexkey"

    def test_resolve_private_key_uses_generic_agent_env_last(self):
        from common.credentials import (
            resolve_private_key,
            MacOSKeychainBackend,
            EncryptedKeystoreBackend,
            RailwayEnvBackend,
            FlatFileBackend,
        )

        with patch.object(MacOSKeychainBackend, "available", return_value=False), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value=None), \
             patch.object(RailwayEnvBackend, "available", return_value=False), \
             patch.object(FlatFileBackend, "available", return_value=True), \
             patch.object(FlatFileBackend, "get_key", return_value=None), \
             patch.dict(os.environ, {"AGENT_PRIVATE_KEY": "0xgeneric"}, clear=True):
            assert resolve_private_key(venue="paradex") == "0xgeneric"


class TestVenueAwareAddressResolution:
    def test_resolve_wallet_address_prefers_explicit_arg(self):
        from common.credentials import resolve_wallet_address

        addr = "0x1111111111111111111111111111111111111111"
        with patch.dict(os.environ, {"PARADEX_ADDRESS": "0x2222222222222222222222222222222222222222"}, clear=True):
            assert resolve_wallet_address("paradex", address=addr) == addr

    def test_resolve_wallet_address_uses_venue_specific_env(self):
        from common.credentials import resolve_wallet_address

        with patch.dict(os.environ, {"PARADEX_ADDRESS": "0x3333333333333333333333333333333333333333"}, clear=True):
            assert resolve_wallet_address("paradex") == "0x3333333333333333333333333333333333333333"

    def test_resolve_wallet_address_ignores_invalid_values(self, caplog):
        from common.credentials import resolve_wallet_address

        with patch.dict(os.environ, {"PARADEX_ADDRESS": "not-an-address"}, clear=True):
            assert resolve_wallet_address("paradex") == ""
        assert "invalid paradex address candidate" in caplog.text.lower()


class TestKeystorePasswordCompatibility:
    def test_resolve_password_accepts_generic_env_var(self):
        from cli.keystore import _resolve_password

        with patch.dict(os.environ, {"AGENT_CLI_KEYSTORE_PASSWORD": "secret"}, clear=True):
            assert _resolve_password() == "secret"

    def test_load_env_password_reads_generic_key(self, tmp_path):
        from cli import keystore

        env_file = tmp_path / "env"
        env_file.write_text("AGENT_CLI_KEYSTORE_PASSWORD=file-secret\n")
        with patch.object(keystore, "ENV_FILE", env_file):
            assert keystore._load_env_password() == "file-secret"
