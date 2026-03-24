"""Tests for common/credentials.py — backend isolation + resolver priority."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)


# ---------------------------------------------------------------------------
# EncryptedKeystoreBackend
# ---------------------------------------------------------------------------

class TestEncryptedKeystoreBackend:
    def test_available_always_true(self):
        from common.credentials import EncryptedKeystoreBackend
        be = EncryptedKeystoreBackend()
        assert be.available() is True

    def test_name(self):
        from common.credentials import EncryptedKeystoreBackend
        assert EncryptedKeystoreBackend().name() == "keystore"

    def test_get_key_delegates_to_keystore(self):
        from common.credentials import EncryptedKeystoreBackend
        be = EncryptedKeystoreBackend()
        with patch("cli.keystore.get_keystore_key", return_value="0xabc123") as mock_get:
            result = be.get_key()
            assert result == "0xabc123"
            mock_get.assert_called_once()

    def test_get_key_with_address(self):
        from common.credentials import EncryptedKeystoreBackend
        be = EncryptedKeystoreBackend()
        with patch("cli.keystore.get_keystore_key_for_address", return_value="0xdef456") as mock_get:
            result = be.get_key(address="0xtest")
            assert result == "0xdef456"
            mock_get.assert_called_once_with("0xtest")

    def test_list_keys_delegates(self):
        from common.credentials import EncryptedKeystoreBackend
        be = EncryptedKeystoreBackend()
        with patch("cli.keystore.list_keystores", return_value=[
            {"address": "0xaaa", "path": "/tmp/aaa.json"},
            {"address": "0xbbb", "path": "/tmp/bbb.json"},
        ]):
            result = be.list_keys()
            assert result == ["0xaaa", "0xbbb"]

    def test_store_key_requires_password(self):
        from common.credentials import EncryptedKeystoreBackend
        be = EncryptedKeystoreBackend()
        with patch("cli.keystore._resolve_password", return_value=""):
            with pytest.raises(RuntimeError, match="No keystore password"):
                be.store_key("0xaddr", "0xkey")

    def test_store_key_delegates(self):
        from common.credentials import EncryptedKeystoreBackend
        be = EncryptedKeystoreBackend()
        with patch("cli.keystore._resolve_password", return_value="testpass"), \
             patch("cli.keystore.create_keystore") as mock_create:
            be.store_key("0xaddr", "0xkey")
            mock_create.assert_called_once_with("0xkey", "testpass")


# ---------------------------------------------------------------------------
# MacOSKeychainBackend
# ---------------------------------------------------------------------------

class TestMacOSKeychainBackend:
    def test_name(self):
        from common.credentials import MacOSKeychainBackend
        assert MacOSKeychainBackend().name() == "keychain"

    def test_available_on_non_darwin(self):
        from common.credentials import MacOSKeychainBackend
        be = MacOSKeychainBackend()
        with patch("common.credentials.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert be.available() is False

    def test_get_key_calls_security(self):
        from common.credentials import MacOSKeychainBackend
        be = MacOSKeychainBackend()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "0xprivatekey123\n"

        with patch.object(be, "available", return_value=True), \
             patch("common.credentials.subprocess.run", return_value=mock_result) as mock_run:
            result = be.get_key(address="0xMyAddr")
            assert result == "0xprivatekey123"
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "find-generic-password" in args
            assert "0xmyaddr" in args  # normalized to lowercase

    def test_get_key_returns_none_on_failure(self):
        from common.credentials import MacOSKeychainBackend
        be = MacOSKeychainBackend()

        mock_result = MagicMock()
        mock_result.returncode = 44  # security: item not found

        with patch.object(be, "available", return_value=True), \
             patch.object(be, "list_keys", return_value=[]), \
             patch("common.credentials.subprocess.run", return_value=mock_result):
            assert be.get_key() is None

    def test_store_key_calls_security(self):
        from common.credentials import MacOSKeychainBackend
        be = MacOSKeychainBackend()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(be, "available", return_value=True), \
             patch("common.credentials.subprocess.run", return_value=mock_result) as mock_run:
            be.store_key("0xAddr", "0xKey")
            args = mock_run.call_args[0][0]
            assert "add-generic-password" in args
            assert "-U" in args

    def test_store_key_raises_when_unavailable(self):
        from common.credentials import MacOSKeychainBackend
        be = MacOSKeychainBackend()
        with patch.object(be, "available", return_value=False):
            with pytest.raises(RuntimeError, match="not available"):
                be.store_key("0xaddr", "0xkey")

    def test_list_keys_parses_dump(self):
        from common.credentials import MacOSKeychainBackend
        be = MacOSKeychainBackend()

        dump_output = '''keychain: "/Users/test/Library/Keychains/login.keychain-db"
    "svce"<blob>="agent-cli"
    "acct"<blob>="0xABCdef1234567890ABCdef1234567890ABCdef12"
keychain: "/Users/test/Library/Keychains/login.keychain-db"
    "svce"<blob>="other-service"
    "acct"<blob>="0x9999999999999999999999999999999999999999"
'''

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = dump_output

        with patch.object(be, "available", return_value=True), \
             patch("common.credentials.subprocess.run", return_value=mock_result):
            result = be.list_keys()
            assert len(result) == 1
            assert result[0] == "0xabcdef1234567890abcdef1234567890abcdef12"


# ---------------------------------------------------------------------------
# RailwayEnvBackend
# ---------------------------------------------------------------------------

class TestRailwayEnvBackend:
    def test_name(self):
        from common.credentials import RailwayEnvBackend
        assert RailwayEnvBackend().name() == "railway"

    def test_available_when_railway_env_set(self):
        from common.credentials import RailwayEnvBackend
        be = RailwayEnvBackend()
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}):
            assert be.available() is True

    def test_not_available_without_railway_env(self):
        from common.credentials import RailwayEnvBackend
        be = RailwayEnvBackend()
        with patch.dict(os.environ, {}, clear=True):
            assert be.available() is False

    def test_get_key_from_hl_private_key(self):
        from common.credentials import RailwayEnvBackend
        be = RailwayEnvBackend()
        with patch.dict(os.environ, {
            "RAILWAY_ENVIRONMENT": "production",
            "HL_PRIVATE_KEY": "0xrailwaykey",
        }):
            assert be.get_key() == "0xrailwaykey"

    def test_get_key_returns_none_when_no_key(self):
        from common.credentials import RailwayEnvBackend
        be = RailwayEnvBackend()
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}, clear=True):
            assert be.get_key() is None

    def test_store_key_raises(self):
        from common.credentials import RailwayEnvBackend
        be = RailwayEnvBackend()
        with pytest.raises(NotImplementedError, match="Railway dashboard"):
            be.store_key("0xaddr", "0xkey")


# ---------------------------------------------------------------------------
# FlatFileBackend
# ---------------------------------------------------------------------------

class TestFlatFileBackend:
    def test_name(self):
        from common.credentials import FlatFileBackend
        assert FlatFileBackend().name() == "file"

    def test_available_always_true(self):
        from common.credentials import FlatFileBackend
        assert FlatFileBackend().available() is True

    def test_store_and_get_roundtrip(self, tmp_path):
        from common.credentials import FlatFileBackend
        be = FlatFileBackend()

        with patch("common.credentials.KEYS_DIR", tmp_path):
            be.store_key("0xTestAddr", "0xsecretkey")
            # File should exist with correct permissions
            key_file = tmp_path / "0xtestaddr.txt"
            assert key_file.exists()
            assert key_file.read_text() == "0xsecretkey"
            assert oct(key_file.stat().st_mode & 0o777) == "0o600"

            # Retrieve it
            result = be.get_key("0xTestAddr")
            assert result == "0xsecretkey"

    def test_get_key_returns_none_when_missing(self, tmp_path):
        from common.credentials import FlatFileBackend
        be = FlatFileBackend()
        with patch("common.credentials.KEYS_DIR", tmp_path):
            assert be.get_key("0xnonexistent") is None

    def test_list_keys(self, tmp_path):
        from common.credentials import FlatFileBackend
        be = FlatFileBackend()

        # Create some key files
        (tmp_path / "0xaaa.txt").write_text("key1")
        (tmp_path / "0xbbb.txt").write_text("key2")

        with patch("common.credentials.KEYS_DIR", tmp_path):
            result = be.list_keys()
            assert sorted(result) == ["0xaaa", "0xbbb"]

    def test_list_keys_empty_dir(self, tmp_path):
        from common.credentials import FlatFileBackend
        be = FlatFileBackend()
        with patch("common.credentials.KEYS_DIR", tmp_path):
            assert be.list_keys() == []

    def test_list_keys_nonexistent_dir(self, tmp_path):
        from common.credentials import FlatFileBackend
        be = FlatFileBackend()
        with patch("common.credentials.KEYS_DIR", tmp_path / "nonexistent"):
            assert be.list_keys() == []

    def test_get_key_logs_warning(self, tmp_path, caplog):
        import logging
        from common.credentials import FlatFileBackend
        be = FlatFileBackend()

        (tmp_path / "0xaddr.txt").write_text("0xkey")
        with patch("common.credentials.KEYS_DIR", tmp_path):
            with caplog.at_level(logging.WARNING, logger="credentials"):
                be.get_key("0xaddr")
            assert "Plaintext key storage" in caplog.text


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class TestResolvePrivateKey:
    def test_resolver_uses_first_available_backend(self):
        from common.credentials import resolve_private_key, MacOSKeychainBackend

        with patch.object(MacOSKeychainBackend, "available", return_value=True), \
             patch.object(MacOSKeychainBackend, "get_key", return_value="0xkeychain_key"):
            result = resolve_private_key()
            assert result == "0xkeychain_key"

    def test_resolver_skips_unavailable_backends(self):
        from common.credentials import (
            resolve_private_key,
            MacOSKeychainBackend,
            EncryptedKeystoreBackend,
        )

        with patch.object(MacOSKeychainBackend, "available", return_value=False), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value="0xkeystore_key"):
            result = resolve_private_key()
            assert result == "0xkeystore_key"

    def test_resolver_skips_backend_returning_none(self):
        from common.credentials import (
            resolve_private_key,
            MacOSKeychainBackend,
            EncryptedKeystoreBackend,
        )

        with patch.object(MacOSKeychainBackend, "available", return_value=True), \
             patch.object(MacOSKeychainBackend, "get_key", return_value=None), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value="0xfallback"):
            result = resolve_private_key()
            assert result == "0xfallback"

    def test_resolver_falls_back_to_env_var(self):
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
             patch.dict(os.environ, {"HL_PRIVATE_KEY": "0xenvkey"}):
            result = resolve_private_key()
            assert result == "0xenvkey"

    def test_resolver_raises_when_nothing_found(self):
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
             patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="No private key"):
                resolve_private_key()

    def test_resolver_uses_venue_env_var(self):
        """resolve_private_key(venue='yex') should check YEX_PRIVATE_KEY."""
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
             patch.dict(os.environ, {"YEX_PRIVATE_KEY": "0xyexkey"}, clear=True):
            result = resolve_private_key(venue="yex")
            assert result == "0xyexkey"

    def test_resolver_priority_order(self):
        """Keychain > keystore > railway > file."""
        from common.credentials import (
            resolve_private_key,
            MacOSKeychainBackend,
            EncryptedKeystoreBackend,
            RailwayEnvBackend,
            FlatFileBackend,
        )

        # All backends available and have keys — keychain should win
        with patch.object(MacOSKeychainBackend, "available", return_value=True), \
             patch.object(MacOSKeychainBackend, "get_key", return_value="0xkeychain"), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value="0xkeystore"), \
             patch.object(RailwayEnvBackend, "available", return_value=True), \
             patch.object(RailwayEnvBackend, "get_key", return_value="0xrailway"), \
             patch.object(FlatFileBackend, "available", return_value=True), \
             patch.object(FlatFileBackend, "get_key", return_value="0xfile"):
            assert resolve_private_key() == "0xkeychain"

        # Keychain returns None — keystore should win
        with patch.object(MacOSKeychainBackend, "available", return_value=True), \
             patch.object(MacOSKeychainBackend, "get_key", return_value=None), \
             patch.object(EncryptedKeystoreBackend, "available", return_value=True), \
             patch.object(EncryptedKeystoreBackend, "get_key", return_value="0xkeystore"), \
             patch.object(RailwayEnvBackend, "available", return_value=True), \
             patch.object(RailwayEnvBackend, "get_key", return_value="0xrailway"), \
             patch.object(FlatFileBackend, "available", return_value=True), \
             patch.object(FlatFileBackend, "get_key", return_value="0xfile"):
            assert resolve_private_key() == "0xkeystore"


class TestGetBackend:
    def test_get_existing_backend(self):
        from common.credentials import get_backend
        be = get_backend("keystore")
        assert be is not None
        assert be.name() == "keystore"

    def test_get_nonexistent_backend(self):
        from common.credentials import get_backend
        assert get_backend("nonexistent") is None

    def test_get_all_backends(self):
        from common.credentials import get_all_backends
        backends = get_all_backends()
        names = [b.name() for b in backends]
        assert "keychain" in names
        assert "keystore" in names
        assert "railway" in names
        assert "file" in names
