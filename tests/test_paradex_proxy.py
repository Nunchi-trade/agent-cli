from __future__ import annotations

from unittest.mock import patch

import pytest

from parent.paradex_proxy import ParadexProxy


TEST_L1_KEY = "0x59c6995e998f97a5a0044966f0945382d7d6f4858cc5b64cf68545ce7f0d3f4d"
EXPECTED_L1_ADDRESS = "0x5776cC4ee9b26B615a6c799db7f9d5EE827c595b"
TEST_L2_KEY = "0x1234"
TEST_L2_ADDRESS = "0x" + "1" * 62


@pytest.fixture(autouse=True)
def no_keystore_resolution():
    with patch("parent.paradex_proxy.resolve_private_key", return_value=TEST_L2_KEY):
        yield


def test_paradex_proxy_accepts_matching_l1_signer_and_address():
    proxy = ParadexProxy(
        l1_private_key=TEST_L1_KEY,
        l1_address=EXPECTED_L1_ADDRESS,
        l2_private_key=TEST_L2_KEY,
        l2_address=TEST_L2_ADDRESS,
        testnet=False,
    )
    assert proxy.l1_address.lower() == EXPECTED_L1_ADDRESS.lower()


def test_paradex_proxy_rejects_mismatched_explicit_l1_address():
    with pytest.raises(ValueError, match="Paradex L1 signer mismatch"):
        ParadexProxy(
            l1_private_key=TEST_L1_KEY,
            l1_address="0x" + "2" * 40,
            l2_private_key=TEST_L2_KEY,
            l2_address=TEST_L2_ADDRESS,
        )


def test_paradex_proxy_rejects_mismatched_env_l1_address(monkeypatch):
    monkeypatch.setenv("PARADEX_L1_ADDRESS", "0x" + "3" * 40)
    with pytest.raises(ValueError, match="Paradex L1 signer mismatch"):
        ParadexProxy(
            l1_private_key=TEST_L1_KEY,
            l2_private_key=TEST_L2_KEY,
            l2_address=TEST_L2_ADDRESS,
        )
