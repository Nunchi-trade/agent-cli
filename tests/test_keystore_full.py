"""Tests for cli/keystore.py — encrypted wallet keystore operations."""
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from cli.keystore import (
    create_keystore,
    load_keystore,
    list_keystores,
    _resolve_password,
    _load_env_password,
    get_keystore_key,
    get_keystore_key_for_address,
    KEYSTORE_DIR,
)


@pytest.fixture
def tmp_keystore(tmp_path):
    """Redirect keystore dir to a temp directory."""
    with patch("cli.keystore.KEYSTORE_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def tmp_env_file(tmp_path):
    """Redirect env file to a temp path."""
    env_path = tmp_path / "env"
    with patch("cli.keystore.ENV_FILE", env_path):
        yield env_path


# Known test key (DO NOT use in production)
TEST_KEY = "0x" + "ab" * 32  # 0xabab...ab (64 hex chars)
TEST_PASSWORD = "test-password-123"


class TestCreateAndLoad:
    def test_create_and_load_roundtrip(self, tmp_keystore):
        ks_path = create_keystore(TEST_KEY, TEST_PASSWORD)
        assert ks_path.exists()
        assert ks_path.suffix == ".json"

        # Load back
        address = ks_path.stem
        recovered_key = load_keystore(address, TEST_PASSWORD)
        assert recovered_key.lower() == TEST_KEY.lower()

    def test_create_stores_valid_json(self, tmp_keystore):
        ks_path = create_keystore(TEST_KEY, TEST_PASSWORD)
        data = json.loads(ks_path.read_text())
        assert "address" in data
        assert "crypto" in data

    def test_load_wrong_password_raises(self, tmp_keystore):
        create_keystore(TEST_KEY, TEST_PASSWORD)
        address = list(tmp_keystore.glob("*.json"))[0].stem
        with pytest.raises(Exception):
            load_keystore(address, "wrong-password")

    def test_load_missing_keystore_raises(self, tmp_keystore):
        with pytest.raises(FileNotFoundError):
            load_keystore("nonexistent", TEST_PASSWORD)

    def test_address_with_0x_prefix(self, tmp_keystore):
        ks_path = create_keystore(TEST_KEY, TEST_PASSWORD)
        address = ks_path.stem
        # Should work with or without 0x prefix
        key = load_keystore(f"0x{address}", TEST_PASSWORD)
        assert key.lower() == TEST_KEY.lower()


class TestListKeystores:
    def test_empty_dir(self, tmp_keystore):
        result = list_keystores()
        assert result == []

    def test_lists_created_keystores(self, tmp_keystore):
        create_keystore(TEST_KEY, TEST_PASSWORD)
        result = list_keystores()
        assert len(result) == 1
        assert result[0]["address"].startswith("0x")
        assert result[0]["path"].endswith(".json")

    def test_multiple_keystores(self, tmp_keystore):
        create_keystore(TEST_KEY, TEST_PASSWORD)
        key2 = "0x" + "cd" * 32
        create_keystore(key2, TEST_PASSWORD)
        result = list_keystores()
        assert len(result) == 2


class TestResolvePassword:
    def test_explicit_password_takes_priority(self):
        assert _resolve_password("explicit") == "explicit"

    def test_env_var_fallback(self):
        with patch.dict(os.environ, {"HL_KEYSTORE_PASSWORD": "from-env"}):
            assert _resolve_password() == "from-env"

    def test_env_file_fallback(self, tmp_env_file):
        tmp_env_file.write_text("HL_KEYSTORE_PASSWORD=from-file\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HL_KEYSTORE_PASSWORD", None)
            assert _resolve_password() == "from-file"

    def test_returns_empty_when_nothing_set(self, tmp_env_file):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HL_KEYSTORE_PASSWORD", None)
            assert _resolve_password() == ""


class TestGetKeystoreKey:
    def test_returns_key_with_password(self, tmp_keystore):
        create_keystore(TEST_KEY, TEST_PASSWORD)
        with patch.dict(os.environ, {"HL_KEYSTORE_PASSWORD": TEST_PASSWORD}):
            key = get_keystore_key()
        assert key is not None
        assert key.lower() == TEST_KEY.lower()

    def test_returns_none_without_password(self, tmp_keystore):
        create_keystore(TEST_KEY, TEST_PASSWORD)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HL_KEYSTORE_PASSWORD", None)
            with patch("cli.keystore.ENV_FILE", Path("/nonexistent")):
                key = get_keystore_key()
        assert key is None

    def test_returns_none_with_no_keystores(self, tmp_keystore):
        key = get_keystore_key()
        assert key is None

    def test_specific_address(self, tmp_keystore):
        ks_path = create_keystore(TEST_KEY, TEST_PASSWORD)
        address = f"0x{ks_path.stem}"
        with patch.dict(os.environ, {"HL_KEYSTORE_PASSWORD": TEST_PASSWORD}):
            key = get_keystore_key(address=address)
        assert key is not None


class TestGetKeystoreKeyForAddress:
    def test_returns_key_for_valid_address(self, tmp_keystore):
        ks_path = create_keystore(TEST_KEY, TEST_PASSWORD)
        address = f"0x{ks_path.stem}"
        with patch.dict(os.environ, {"HL_KEYSTORE_PASSWORD": TEST_PASSWORD}):
            key = get_keystore_key_for_address(address)
        assert key is not None

    def test_returns_none_for_empty_address(self, tmp_keystore):
        assert get_keystore_key_for_address("") is None

    def test_returns_none_for_missing_address(self, tmp_keystore):
        with patch.dict(os.environ, {"HL_KEYSTORE_PASSWORD": TEST_PASSWORD}):
            key = get_keystore_key_for_address("0x" + "ff" * 20)
        assert key is None
